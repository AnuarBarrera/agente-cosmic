import logging
import os
import tempfile
import django_rq
from asgiref.sync import sync_to_async
from django.core.management.base import BaseCommand
from django.conf import settings
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes,
)

logger = logging.getLogger(__name__)


# ─── helpers sync→async ────────────────────────────────────────────────────

async def safe_reply(message, text: str) -> None:
    """Envía texto con Markdown. Divide en partes si supera el límite de Telegram (4096 chars)."""
    MAX = 4000
    parts = [text[i:i + MAX] for i in range(0, len(text), MAX)] if len(text) > MAX else [text]
    for part in parts:
        try:
            await message.reply_text(part, parse_mode='Markdown')
        except Exception:
            try:
                await message.reply_text(part)
            except Exception:
                pass


def _process_message_sync(chat_id, username, full_name, text):
    from core.agent.application.agent_service import AgentService
    return AgentService().process_message(chat_id, username, full_name, text)


def _run_tool_sync(tool_name, session_id, **kwargs):
    from core.agent.infrastructure.tools.registry import get_tool
    from core.agent.infrastructure.repositories import DjangoRequestRepository
    from core.agent.domain.entities import AgentRequest
    import time

    tool = get_tool(tool_name)
    if not tool:
        return None

    start = time.time()
    result = tool.execute(**kwargs)
    duration_ms = int((time.time() - start) * 1000)

    DjangoRequestRepository().log(AgentRequest(
        session_id=session_id,
        user_message=str(kwargs),
        ai_response=result.content,
        model_used=getattr(settings, 'AI_MODEL', 'gemini-2.5-flash'),
        tool_used=tool_name,
        duration_ms=duration_ms,
        estimated_tokens=len(result.content) // 4,
        success=result.success,
        error_message=result.error,
    ))
    return result


def _get_or_create_session_sync(chat_id, username, full_name):
    from core.agent.infrastructure.repositories import DjangoSessionRepository
    return DjangoSessionRepository().get_or_create(chat_id, username, full_name)


process_message = sync_to_async(_process_message_sync)
run_tool = sync_to_async(_run_tool_sync)
get_or_create_session = sync_to_async(_get_or_create_session_sync)


# ─── utilidades ────────────────────────────────────────────────────────────

def _parse_args(args: list, defaults: dict) -> dict:
    """Parsea argumentos posicionales con defaults."""
    keys = list(defaults.keys())
    result = dict(defaults)
    for i, val in enumerate(args):
        if i < len(keys):
            result[keys[i]] = val
        else:
            # El resto se acumula en el último campo
            last_key = keys[-1]
            result[last_key] = ' '.join(args[i - len(keys) + 1:])
            break
    return result


async def _reply_tool(update: Update, context: ContextTypes.DEFAULT_TYPE, tool_name: str, **kwargs):
    """Obtiene sesión, ejecuta tool y responde."""
    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text("No estás autorizado para usar este agente.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    result = await run_tool(tool_name, session.id, **kwargs)
    if result:
        await safe_reply(update.message, result.content)
    else:
        await update.message.reply_text("Herramienta no disponible.")


# ─── consulta de consumo ────────────────────────────────────────────────────

def _get_consumo_sync():
    from django.db.models import Count, Avg, Sum, Q
    from django.utils import timezone
    from datetime import timedelta
    from core.agent.infrastructure.models import AgentSession, AgentMemory, AgentRequest, BrowserSession

    since = timezone.now() - timedelta(days=30)
    hoy = timezone.now() - timedelta(days=1)

    agg = AgentRequest.objects.filter(timestamp__gte=since).aggregate(
        total=Count('id'),
        ok=Count('id', filter=Q(success=True)),
        avg_ms=Avg('duration_ms'),
        tokens=Sum('estimated_tokens'),
    )
    total = agg['total'] or 0
    ok = agg['ok'] or 0
    tasa = round(ok / total * 100, 1) if total else 0
    avg_ms = round(agg['avg_ms'] or 0)
    tokens = agg['tokens'] or 0

    top_tools = list(
        AgentRequest.objects.filter(timestamp__gte=since)
        .exclude(tool_used=None)
        .values('tool_used')
        .annotate(n=Count('id'))
        .order_by('-n')[:5]
    )

    hoy_total = AgentRequest.objects.filter(timestamp__gte=hoy).count()
    sesiones = AgentSession.objects.filter(is_authorized=True).count()
    memorias = AgentMemory.objects.count()
    cookies = BrowserSession.objects.filter(is_valid=True).values_list('platform', flat=True)

    return {
        'total': total, 'ok': ok, 'tasa': tasa,
        'avg_ms': avg_ms, 'tokens': tokens,
        'top_tools': top_tools,
        'hoy': hoy_total,
        'sesiones': sesiones,
        'memorias': memorias,
        'cookies': list(cookies),
    }


get_consumo = sync_to_async(_get_consumo_sync)


# ─── multi-usuario ──────────────────────────────────────────────────────────

ADMIN_ONLY_MSG = "⛔ Este comando es solo para administradores."


async def _require_admin(update: Update, session) -> bool:
    """Returns True if user is admin. Sends error and returns False otherwise."""
    if not session.is_authorized:
        await update.message.reply_text("No estás autorizado para usar este agente.")
        return False
    if session.role != 'admin':
        await update.message.reply_text(ADMIN_ONLY_MSG)
        return False
    return True


def _list_sessions_sync() -> str:
    from core.agent.infrastructure.models import AgentSession as SessionModel
    sessions = SessionModel.objects.filter(is_authorized=True).order_by('-last_active_at')
    if not sessions.exists():
        return '📋 No hay usuarios autorizados.'
    lines = ['📋 *Usuarios autorizados:*\n']
    for s in sessions:
        role_icon = '👑' if s.role == 'admin' else '👤'
        last = s.last_active_at.strftime('%d/%m %H:%M') if s.last_active_at else '—'
        name = s.full_name or s.username or str(s.chat_id)
        lines.append(f'{role_icon} *{name}* (`{s.role}`) — Último acceso: {last}')
    return '\n'.join(lines)


_list_sessions = sync_to_async(_list_sessions_sync)


async def cmd_usuarios(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/usuarios (solo admin) — Lista todos los usuarios autorizados y sus roles."""
    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not await _require_admin(update, session):
        return
    text = await _list_sessions()
    await safe_reply(update.message, text)


# ─── comandos básicos ───────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or 'amigo'
    await update.message.reply_text(
        f"Hola {name} 👋\n"
        "Soy tu asistente de negocio. Puedes escribirme lo que necesites.\n\n"
        "*Comandos disponibles:*\n"
        "/ayuda — ver qué puedo hacer\n"
        "/estado — estado del agente\n"
        "/post — generar post para redes\n"
        "/texto — redactar un texto\n"
        "/short — guión para short/reel\n"
        "/reporte — reporte mensual",
        parse_mode='Markdown',
    )


AYUDA_TEXT = (
    "*¿Qué puedo hacer por ti?*\n\n"
    "💬 *Conversación libre*\n"
    "Escríbeme cualquier cosa y te respondo con contexto.\n\n"
    "📱 */post [red] [tono] <tema>*\n"
    "Genera un post listo para publicar.\n"
    "Redes: instagram, facebook, linkedin, twitter\n"
    "Tonos: profesional, casual, motivador, informativo\n"
    "Ejemplo: `/post instagram casual Apertura nueva sucursal`\n\n"
    "✍️ */texto [tipo] <contexto>*\n"
    "Redacta emails, descripciones, anuncios, bios...\n"
    "Tipos: email, descripcion, bio, anuncio, mensaje, propuesta\n"
    "Ejemplo: `/texto email Bienvenida a nuevos clientes`\n\n"
    "🎬 */short <tema>*\n"
    "Genera un guión para un short/reel de 60 segundos.\n"
    "Ejemplo: `/short Beneficios del marketing digital`\n\n"
    "📄 */documento [tipo] <descripción>*\n"
    "Genera un Word (.docx) profesional listo para entregar.\n"
    "Tipos: propuesta, contrato, informe, brief, presupuesto\n"
    "Ejemplo: `/documento propuesta Identidad de marca para restaurante`\n\n"
    "🖼 */imagen [plataforma] <tema>*\n"
    "Genera imagen lista para publicar en redes sociales.\n"
    "Plataformas: instagram (default), story, linkedin\n"
    "Ejemplo: `/imagen linkedin Apertura nueva sucursal`\n\n"
    "🎬 */video <descripción>*\n"
    "Genera un video corto con IA (3-5 min de espera).\n"
    "Ejemplo: `/video Beneficios del diseño web profesional`\n\n"
    "🎙 */audio <texto>*\n"
    "Convierte texto a narración de voz en MP3.\n"
    "Ejemplo: `/audio Bienvenidos a Tu Web MX`\n\n"
    "🔍 */buscar <consulta>*\n"
    "Busca en internet con Brave Search (resultados en México).\n"
    "Ejemplo: `/buscar tendencias marketing digital 2026`\n\n"
    "🔎 */prospecto <nombre> [url1] [url2...]*\n"
    "Investiga un prospecto o competidor: web, redes, brief de ventas.\n"
    "Ejemplo: `/prospecto \"Tu Web MX\" https://tuwebmx.com`\n\n"
    "📚 */consultar <pregunta>*\n"
    "Consulta tus documentos cargados (catálogos, contratos, precios).\n"
    "Ejemplo: `/consultar ¿Cuánto cuesta el servicio de identidad de marca?`\n\n"
    "📊 */estadisticas <url o página>*\n"
    "Estadísticas de un post o página de redes sociales.\n"
    "Atajos de página: `tuwebmx`, `anuarbarrera`, `linkedin`, `instagram`\n"
    "Ejemplo post: `/estadisticas https://www.instagram.com/p/...`\n"
    "Ejemplo página: `/estadisticas tuwebmx`\n\n"
    "🗺 */prospectar <giro> <ciudad> [radio_km]*\n"
    "Busca negocios en Google Maps y guarda los leads.\n"
    "Ejemplo: `/prospectar plomeros Monterrey 5`\n\n"
    "✅ */contactado*\n"
    "Marca como contactados los prospectos del batch más reciente.\n\n"
    "📊 */reporte [mes] [año]*\n"
    "Reporte mensual de uso del agente. Ejemplo: `/reporte 5 2026`\n\n"
    "📊 */consumo*\n"
    "Métricas de uso: solicitudes, tokens, herramientas más usadas.\n\n"
    "🎙 *Envía un audio o nota de voz*\n"
    "Te transcribo el audio automáticamente.\n\n"
    "📎 *Envía un PDF o .docx*\n"
    "Lo cargo en tu base de conocimiento para consultar con /consultar.\n\n"
    "👥 */usuarios* _(solo admin)_\n"
    "Lista los usuarios autorizados con su rol y último acceso.\n\n"
    "📅 */agenda <descripción>*\n"
    "Crea un evento en Google Calendar con lenguaje natural.\n"
    "Ejemplo: `/agenda Llamada con Carlos el viernes a las 3pm`\n\n"
    "📅 */calendario [días]*\n"
    "Muestra los próximos eventos del calendario (default: 7 días).\n"
    "Ejemplo: `/calendario 14`\n\n"
    "📊 */exportar leads*\n"
    "Exporta tus prospectos guardados a Google Sheets.\n\n"
    "📊 */importar*\n"
    "Lee datos desde tu Google Sheet configurado.\n\n"
    "🗂 */drive <búsqueda>*\n"
    "Busca archivos en tu biblioteca de Google Drive.\n"
    "Ejemplo: `/drive propuesta identidad de marca`\n\n"
)


async def cmd_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(AYUDA_TEXT, parse_mode='Markdown')


async def cmd_estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    model = getattr(settings, 'AI_MODEL', 'gemini-2.5-flash')
    await update.message.reply_text(
        f"✅ Agente activo\n"
        f"🤖 Modelo: `{model}`\n"
        f"💾 Memoria: PostgreSQL + pgvector\n"
        f"🛠 Herramientas: post, texto, short, reporte, transcripción\n"
        f"📊 `/consumo` para ver métricas de uso",
        parse_mode='Markdown',
    )


async def cmd_consumo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /consumo
    Muestra métricas de uso del agente directamente desde la BD.
    """
    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text("No estás autorizado.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    d = await get_consumo()

    tools_txt = ''
    if d['top_tools']:
        tools_txt = '\n'.join(f"  • `{t['tool_used']}`: {t['n']} usos" for t in d['top_tools'])
    else:
        tools_txt = '  (sin herramientas usadas)'

    cookies_txt = ', '.join(d['cookies']) if d['cookies'] else 'ninguna'

    await update.message.reply_text(
        "📊 *Consumo del agente — últimos 30 días*\n\n"
        f"📨 Solicitudes totales: *{d['total']}*\n"
        f"✅ Exitosas: *{d['ok']}* ({d['tasa']}%)\n"
        f"⏱ Tiempo promedio: *{d['avg_ms']} ms*\n"
        f"🔢 Tokens estimados: *{d['tokens']:,}*\n"
        f"📅 Hoy (últimas 24h): *{d['hoy']}* solicitudes\n\n"
        f"👥 Sesiones autorizadas: *{d['sesiones']}*\n"
        f"🧠 Memorias guardadas: *{d['memorias']}*\n"
        f"🍪 Cookies activas: {cookies_txt}\n\n"
        f"🛠 *Top herramientas usadas:*\n{tools_txt}",
        parse_mode='Markdown',
    )


# ─── herramientas ──────────────────────────────────────────────────────────

async def cmd_credentials(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /credentials usuario contraseña
    Completa el flujo de login iniciado con /login.
    """
    user = update.effective_user
    chat_id = update.effective_chat.id
    session = await get_or_create_session(chat_id, user.username or '', user.full_name or '')

    if not session.is_authorized:
        await update.message.reply_text("No estás autorizado.")
        return

    # Intentar borrar el mensaje con credenciales inmediatamente
    try:
        await update.message.delete()
    except Exception:
        pass

    from django.core.cache import cache
    platform = cache.get(f"pending_login:{chat_id}")
    if not platform:
        await context.bot.send_message(
            chat_id=chat_id,
            text="⚠️ No hay login pendiente. Usa `/login <plataforma>` primero.",
            parse_mode='Markdown',
        )
        return

    args = context.args
    if not args or len(args) < 2:
        await context.bot.send_message(
            chat_id=chat_id,
            text="Formato: `/credentials usuario contraseña`",
            parse_mode='Markdown',
        )
        return

    username = args[0]
    password = ' '.join(args[1:])
    cache.delete(f"pending_login:{chat_id}")

    await context.bot.send_message(chat_id=chat_id, text=f"🔐 Iniciando sesión en {platform.capitalize()}...")

    result = await run_tool(
        'browser_login', session.id,
        platform=platform, username=username, password=password, chat_id=chat_id,
    )
    if result:
        await context.bot.send_message(
            chat_id=chat_id,
            text=result.content,
            parse_mode='Markdown',
        )


async def handle_cookie_json(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Detecta si el mensaje es un JSON de cookies y lo procesa.
    Retorna True si fue manejado, False si debe seguir al handler normal.
    """
    text = update.message.text or ''
    if not (text.strip().startswith('[') or text.strip().startswith('{')):
        return False

    chat_id = update.effective_chat.id
    from django.core.cache import cache
    platform = cache.get(f"pending_cookie_import:{chat_id}")
    if not platform:
        return False

    import json
    try:
        raw = json.loads(text.strip())
        # Cookie-Editor exporta una lista de objetos
        if isinstance(raw, dict):
            raw = [raw]

        # Normalizar formato
        cookies = []
        for c in raw:
            if 'name' in c and 'value' in c:
                cookie = {k: v for k, v in c.items() if k in (
                    'name', 'value', 'domain', 'path', 'expires',
                    'httpOnly', 'secure', 'sameSite',
                )}
                cookies.append(cookie)

        if not cookies:
            await update.message.reply_text("No encontré cookies válidas en el JSON. Intenta exportar de nuevo.")
            return True

        # Guardar en BrowserSession
        def _save():
            from core.agent.infrastructure.models import BrowserSession
            BrowserSession.objects.update_or_create(
                platform=platform,
                username='imported',
                defaults={'cookies': cookies, 'is_valid': True},
            )

        await sync_to_async(_save)()
        cache.delete(f"pending_cookie_import:{chat_id}")

        await update.message.reply_text(
            f"✅ *Cookies de {platform.capitalize()} importadas*\n\n"
            f"Se importaron *{len(cookies)} cookies*.\n"
            f"Ahora `/estadisticas` usará tu sesión real para obtener más datos.",
            parse_mode='Markdown',
        )
        return True

    except json.JSONDecodeError:
        return False


async def cmd_estadisticas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /estadisticas <url o atajo de página>
    /estadisticas tuwebmx | anuarbarrera | linkedin
    /estadisticas https://www.instagram.com/p/...
    """
    args = context.args
    if not args:
        await update.message.reply_text(
            "Uso: `/estadisticas <url o página>`\n\n"
            "Atajos de página:\n"
            "`/estadisticas tuwebmx` — Tu Web MX (Facebook)\n"
            "`/estadisticas anuarbarrera` — Anuar Barrera (Facebook)\n"
            "`/estadisticas linkedin` — LinkedIn\n\n"
            "Post específico:\n"
            "`/estadisticas https://www.instagram.com/p/...`",
            parse_mode='Markdown',
        )
        return

    url = args[0]
    await _reply_tool(update, context, 'get_post_stats', url=url, chat_id=update.effective_chat.id)


async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /post [platform] [tone] <topic>
    /post instagram casual Apertura de nueva sucursal
    /post Apertura de nueva sucursal  (usa defaults)
    """
    args = context.args
    if not args:
        await update.message.reply_text(
            "Uso: `/post [red] [tono] <tema>`\n"
            "Ejemplo: `/post instagram profesional Apertura nueva sucursal`",
            parse_mode='Markdown',
        )
        return

    platforms = {'instagram', 'facebook', 'linkedin', 'twitter', 'x'}
    tones = {'profesional', 'casual', 'motivador', 'humoristico', 'informativo'}

    platform = 'instagram'
    tone = 'profesional'
    topic_start = 0

    if args[0].lower() in platforms:
        platform = args[0].lower()
        topic_start = 1
        if len(args) > 1 and args[1].lower() in tones:
            tone = args[1].lower()
            topic_start = 2

    topic = ' '.join(args[topic_start:]) if topic_start < len(args) else ' '.join(args)

    if not topic.strip():
        await update.message.reply_text("Necesito el tema del post.")
        return

    await _reply_tool(update, context, 'generate_post', topic=topic, platform=platform, tone=tone)


async def cmd_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /texto [type] <context>
    /texto email Bienvenida a nuevos clientes de la tienda
    """
    args = context.args
    if not args:
        await update.message.reply_text(
            "Uso: `/texto [tipo] <contexto>`\n"
            "Tipos: email, descripcion, bio, anuncio, mensaje, propuesta\n"
            "Ejemplo: `/texto email Bienvenida a nuevos clientes`",
            parse_mode='Markdown',
        )
        return

    text_types = {'email', 'descripcion', 'bio', 'anuncio', 'mensaje', 'propuesta', 'guion'}
    text_type = 'email'
    ctx_start = 0

    if args[0].lower() in text_types:
        text_type = args[0].lower()
        ctx_start = 1

    text_context = ' '.join(args[ctx_start:]) if ctx_start < len(args) else ' '.join(args)

    if not text_context.strip():
        await update.message.reply_text("Necesito el contexto o instrucciones del texto.")
        return

    await _reply_tool(update, context, 'write_text', text_context=text_context, text_type=text_type)


async def cmd_buscar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/buscar <consulta>"""
    args = ' '.join(context.args).strip()
    if not args:
        await update.message.reply_text(
            '🔍 Uso: `/buscar <consulta>`\nEjemplo: `/buscar tendencias marketing digital 2026`',
            parse_mode='Markdown',
        )
        return
    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text("No estás autorizado para usar este agente.")
        return
    await update.message.reply_text('🔍 Buscando en internet...', parse_mode='Markdown')
    result = await run_tool('web_search', session.id, query=args)
    if result:
        if result.success:
            await safe_reply(update.message, result.content)
        else:
            await update.message.reply_text(f'❌ {result.content}')


async def cmd_documento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/documento [tipo] <descripcion>"""
    args = context.args
    if not args:
        await update.message.reply_text(
            '📄 Uso: `/documento [tipo] <descripción>`\n'
            'Tipos: propuesta, contrato, informe, brief, presupuesto\n'
            'Ejemplo: `/documento propuesta Identidad de marca para restaurante`',
            parse_mode='Markdown',
        )
        return
    from core.agent.infrastructure.tools.document_tools import DOC_TYPES
    doc_types = list(DOC_TYPES.keys())
    if args[0].lower() in doc_types:
        doc_type = args[0].lower()
        description = ' '.join(args[1:]).strip()
    else:
        doc_type = 'propuesta'
        description = ' '.join(args).strip()
    if not description:
        await update.message.reply_text('❌ Debes incluir una descripción del documento.')
        return
    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text("No estás autorizado para usar este agente.")
        return
    await update.message.reply_text(f'📝 Generando {doc_type}...', parse_mode='Markdown')
    result = await run_tool('generate_document', session.id, doc_type=doc_type, description=description)
    if result:
        if result.success:
            docx_bytes = result.metadata.get('docx_bytes')
            filename = result.metadata.get('filename', 'documento.docx')
            pdf_bytes = result.metadata.get('pdf_bytes')
            pdf_filename = result.metadata.get('pdf_filename', 'documento.pdf')
            import io
            await update.message.reply_document(
                document=io.BytesIO(docx_bytes),
                filename=filename,
                caption='📄 Word (.docx) — editable',
            )
            if pdf_bytes:
                await update.message.reply_document(
                    document=io.BytesIO(pdf_bytes),
                    filename=pdf_filename,
                    caption='📋 PDF — listo para compartir',
                )
        else:
            await update.message.reply_text(f'❌ {result.content}')


async def cmd_short(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /short <topic>
    /short Beneficios del marketing digital para pequeñas empresas
    """
    args = context.args
    if not args:
        await update.message.reply_text(
            "Uso: `/short <tema>`\n"
            "Ejemplo: `/short Beneficios del marketing digital`",
            parse_mode='Markdown',
        )
        return

    topic = ' '.join(args)
    await _reply_tool(update, context, 'generate_short_script', topic=topic)


async def cmd_prospectar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /prospectar <giro> <ciudad o lat,lng> [rango_km]
    /prospectar plomeros Monterrey 5
    /prospectar "salones de belleza" "Ciudad de México" 3
    /prospectar restaurantes 25.67,-100.31 10
    """
    args = context.args
    if not args or len(args) < 2:
        await update.message.reply_text(
            "Uso: `/prospectar <giro> <ciudad o lat,lng> [rango_km]`\n\n"
            "Ejemplos:\n"
            "`/prospectar plomeros Monterrey 5`\n"
            "`/prospectar restaurantes \"Ciudad de México\" 3`\n"
            "`/prospectar ferreterías 25.67,-100.31 10`",
            parse_mode='Markdown',
        )
        return

    # Parsear: último arg puede ser número (rango), penúltimo ciudad, primero giro
    rango_km = 5.0
    if args[-1].replace('.', '', 1).isdigit():
        rango_km = float(args[-1])
        args = args[:-1]

    giro = args[0]
    location = ' '.join(args[1:])

    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not await _require_admin(update, session):
        return

    # Verificar leads sin contactar de los últimos 30 días
    from asgiref.sync import sync_to_async
    from core.agent.infrastructure.models import ProspectLead
    from django.utils import timezone
    from datetime import timedelta

    chat_id_str = str(update.effective_chat.id)

    @sync_to_async
    def _count_uncontacted():
        cutoff = timezone.now() - timedelta(days=30)
        return ProspectLead.objects.filter(
            chat_id=chat_id_str,
            contacted=False,
            searched_at__gte=cutoff,
        ).count()

    uncontacted = await _count_uncontacted()
    if uncontacted > 0:
        await update.message.reply_text(
            f'📋 Tienes *{uncontacted} prospectos* sin contactar de búsquedas anteriores.\n'
            f'Usa `/contactado` para marcarlos como contactados cuando los hayas llamado.',
            parse_mode='Markdown',
        )

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')

    # Llamar tool directamente (encola RQ job internamente)
    result = await run_tool(
        'prospect_maps', session.id,
        giro=giro, location=location, rango_km=rango_km, chat_id=update.effective_chat.id,
    )
    if result:
        await update.message.reply_text(result.content, parse_mode='Markdown')


async def cmd_contactado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/contactado — marca todos los prospectos recientes como contactados"""
    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not await _require_admin(update, session):
        return

    from asgiref.sync import sync_to_async
    from core.agent.infrastructure.models import ProspectLead
    from django.utils import timezone
    from datetime import timedelta

    chat_id_str = str(update.effective_chat.id)

    @sync_to_async
    def _mark_contacted():
        cutoff = timezone.now() - timedelta(days=30)
        qs = ProspectLead.objects.filter(
            chat_id=chat_id_str,
            contacted=False,
            searched_at__gte=cutoff,
        )
        count = qs.count()
        qs.update(contacted=True, contacted_at=timezone.now())
        return count

    count = await _mark_contacted()
    if count == 0:
        await update.message.reply_text('No hay prospectos pendientes de los últimos 30 días.')
    else:
        await update.message.reply_text(
            f'✅ *{count} prospectos* marcados como contactados.',
            parse_mode='Markdown',
        )


async def _send_prospect_result(update: Update, result, name: str) -> None:
    """Envía el brief como documento .docx o mensaje de error."""
    if not result:
        await update.message.reply_text('❌ Herramienta no disponible.')
        return
    if not result.success:
        await update.message.reply_text(f'❌ {result.content}')
        return
    docx_bytes = result.metadata.get('docx_bytes')
    filename = result.metadata.get('filename', f'brief_{name}.docx')
    pdf_bytes = result.metadata.get('pdf_bytes')
    pdf_filename = result.metadata.get('pdf_filename', f'brief_{name}.pdf')
    if docx_bytes:
        import io
        await update.message.reply_document(
            document=io.BytesIO(docx_bytes),
            filename=filename,
            caption='📄 Word (.docx) — editable',
        )
        if pdf_bytes:
            await update.message.reply_document(
                document=io.BytesIO(pdf_bytes),
                filename=pdf_filename,
                caption='📋 PDF — listo para compartir',
            )
    else:
        await safe_reply(update.message, result.content)


async def cmd_prospecto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/prospecto <nombre> [url1] [url2...]"""
    args = context.args
    if not args:
        await update.message.reply_text(
            '🔎 Uso: `/prospecto <nombre> [url1] [url2...]`\n'
            'Ejemplo: `/prospecto "Tu Web MX" https://tuwebmx.com https://instagram.com/tuwebmx`',
            parse_mode='Markdown',
        )
        return

    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not await _require_admin(update, session):
        return

    from core.agent.infrastructure.jobs import detect_social_platform

    # Separar URLs de las palabras del nombre
    urls = [a for a in args if a.startswith('http')]
    name_parts = [a for a in args if not a.startswith('http')]
    name = ' '.join(name_parts).strip('"\'') or args[0]

    social_urls = [(u, detect_social_platform(u)) for u in urls if detect_social_platform(u)]
    non_social_urls = [u for u in urls if not detect_social_platform(u)]

    chat_id = update.effective_chat.id

    if social_urls:
        await update.message.reply_text(
            f'🔎 Investigando perfiles sociales de *{name}*...\n'
            f'Te aviso cuando tenga los resultados.',
            parse_mode='Markdown',
        )
        queue = django_rq.get_queue('default')
        for social_url, platform in social_urls:
            queue.enqueue(
                'core.agent.infrastructure.jobs.competitor_n8n_job',
                name=name,
                social_url=social_url,
                platform=platform,
                chat_id=chat_id,
                job_timeout=120,
            )

    if non_social_urls:
        await update.message.reply_text(
            f'🔎 Investigando *{name}* en la web...',
            parse_mode='Markdown',
        )
        result = await run_tool('prospect_research', session.id, name=name, urls=non_social_urls)
        await _send_prospect_result(update, result, name)

    if not social_urls and not non_social_urls:
        await update.message.reply_text(f'🔎 Investigando *{name}*...', parse_mode='Markdown')
        result = await run_tool('prospect_research', session.id, name=name, urls=[])
        await _send_prospect_result(update, result, name)


async def cmd_consultar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/consultar <pregunta>"""
    args = ' '.join(context.args).strip()
    if not args:
        await update.message.reply_text(
            '📚 Uso: `/consultar <pregunta>`\n'
            'Ejemplo: `/consultar ¿Cuánto cuesta el servicio de identidad de marca?`',
            parse_mode='Markdown',
        )
        return
    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text("No estás autorizado para usar este agente.")
        return
    await update.message.reply_text('🔍 Consultando tus documentos...', parse_mode='Markdown')
    result = await run_tool('rag_query', session.id, query=args)
    if result:
        await safe_reply(update.message, result.content)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe un archivo PDF o .docx y lo carga en el RAG."""
    doc = update.message.document
    if not doc:
        return
    filename = doc.file_name or 'documento.pdf'
    ext = filename.lower().split('.')[-1]
    if ext not in ('pdf', 'docx'):
        await update.message.reply_text('❌ Solo acepto archivos PDF o .docx.')
        return
    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text("No estás autorizado para usar este agente.")
        return
    await update.message.reply_text(f'📥 Cargando "{filename}"...')
    file = await context.bot.get_file(doc.file_id)
    file_bytes = bytes(await file.download_as_bytearray())
    result = await run_tool('rag_upload', session.id, filename=filename, file_bytes=file_bytes, doc_type='general')
    if result:
        if result.success:
            await update.message.reply_text(f'✅ {result.content}')
        else:
            await update.message.reply_text(f'❌ {result.content}')


async def cmd_imagen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/imagen [plataforma] <tema>"""
    args = context.args
    if not args:
        await update.message.reply_text(
            '🖼 Uso: `/imagen [plataforma] <tema>`\n'
            'Plataformas: instagram (default), story, linkedin\n'
            'Ejemplo: `/imagen instagram Apertura nueva sucursal`',
            parse_mode='Markdown',
        )
        return
    platforms = {'instagram', 'story', 'linkedin'}
    if args[0].lower() in platforms:
        platform = args[0].lower()
        topic = ' '.join(args[1:]).strip()
    else:
        platform = 'instagram'
        topic = ' '.join(args).strip()
    if not topic:
        await update.message.reply_text('❌ Debes incluir el tema de la imagen.')
        return
    if len(topic) > 200:
        topic = topic[:200]
        await update.message.reply_text('ℹ️ Tema truncado a 200 caracteres para generar la imagen.')
    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text("No estás autorizado para usar este agente.")
        return
    await update.message.reply_text(f'🎨 Generando imagen para {platform}...', parse_mode='Markdown')
    import io
    result = await run_tool('generate_post_image', session.id, topic=topic, platform=platform)
    if result and result.success:
        image_bytes = result.metadata.get('image_bytes')
        filename = result.metadata.get('filename', 'post.png')
        await update.message.reply_photo(
            photo=io.BytesIO(image_bytes),
            caption=f'✅ {result.content}',
        )
    elif result:
        await update.message.reply_text(f'❌ {result.content}')
    else:
        await update.message.reply_text('❌ Herramienta no disponible.')


async def cmd_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/video <descripción>"""
    prompt = ' '.join(context.args).strip() if context.args else ''
    if not prompt:
        await update.message.reply_text(
            '🎬 Uso: `/video <descripción del video>`\n'
            'Ejemplo: `/video Los beneficios de tener presencia en redes sociales`\n\n'
            '⏱ Tiempo estimado: 3-5 minutos.',
            parse_mode='Markdown',
        )
        return
    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text('No estás autorizado para usar este agente.')
        return
    result = await run_tool(
        'generate_video', session.id,
        prompt=prompt, chat_id=update.effective_chat.id,
    )
    if result:
        await safe_reply(update.message, result.content)
    else:
        await update.message.reply_text('❌ Herramienta no disponible.')


async def cmd_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/audio <texto a narrar>"""
    text = ' '.join(context.args).strip() if context.args else ''
    if not text:
        await update.message.reply_text(
            '🎙 Uso: `/audio <texto a narrar>`\n'
            'Ejemplo: `/audio Bienvenidos a Tu Web MX, especialistas en diseño web`\n\n'
            'Máximo 2000 caracteres.',
            parse_mode='Markdown',
        )
        return
    if len(text) > 2000:
        await update.message.reply_text('❌ Texto demasiado largo (máximo 2000 caracteres).')
        return
    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text('No estás autorizado para usar este agente.')
        return
    await update.message.reply_text('🎙 Generando audio...')
    result = await run_tool('generate_audio', session.id, text=text)
    if result and result.success:
        import io
        audio_bytes = result.metadata.get('audio_bytes')
        filename = result.metadata.get('filename', 'audio.mp3')
        await update.message.reply_document(
            document=io.BytesIO(audio_bytes),
            filename=filename,
            caption='🎙 Audio generado',
        )
    elif result:
        await update.message.reply_text(f'❌ {result.content}')
    else:
        await update.message.reply_text('❌ Herramienta no disponible.')


async def cmd_agenda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /agenda <descripción en lenguaje natural>
    Crea un evento en Google Calendar.
    Ejemplo: /agenda Llamada con Carlos el viernes a las 3pm
    """
    description = ' '.join(context.args).strip() if context.args else ''
    if not description:
        await update.message.reply_text(
            '📅 Uso: `/agenda <descripción del evento>`\n\n'
            'Ejemplos:\n'
            '`/agenda Llamada con Carlos el viernes 23 mayo a las 3pm`\n'
            '`/agenda Reunión de equipo mañana 10am, 2 horas`\n'
            '`/agenda Entrega de propuesta a cliente el lunes a las 9am`',
            parse_mode='Markdown',
        )
        return

    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text('No estás autorizado para usar este agente.')
        return

    await update.message.reply_text('📅 Creando evento en tu calendario... Te aviso cuando esté listo.')
    queue = django_rq.get_queue('default')
    queue.enqueue(
        'core.agent.infrastructure.jobs.calendar_create_job',
        kwargs={'description': description, 'chat_id': update.effective_chat.id},
        job_timeout=60,
    )


async def cmd_calendario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /calendario [dias]
    Muestra los próximos eventos del calendario.
    Ejemplo: /calendario 7
    """
    try:
        days = int(context.args[0]) if context.args else 7
        if days < 1 or days > 90:
            raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text(
            '📅 Uso: `/calendario [días]`\n'
            'Ejemplo: `/calendario 7` — próximos 7 días\n'
            'Rango válido: 1-90 días.',
            parse_mode='Markdown',
        )
        return

    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text('No estás autorizado para usar este agente.')
        return

    await update.message.reply_text(f'📅 Consultando tu calendario para los próximos {days} días...')
    queue = django_rq.get_queue('default')
    queue.enqueue(
        'core.agent.infrastructure.jobs.calendar_list_job',
        days=days,
        chat_id=update.effective_chat.id,
        job_timeout=30,
    )


async def cmd_exportar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /exportar leads
    Exporta los prospectos guardados a Google Sheets.
    """
    target = ' '.join(context.args).strip().lower() if context.args else ''
    if not target:
        await update.message.reply_text(
            '📊 Uso: `/exportar <qué exportar>`\n\n'
            'Opciones disponibles:\n'
            '`/exportar leads` — exporta todos tus prospectos a Google Sheets',
            parse_mode='Markdown',
        )
        return

    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text('No estás autorizado para usar este agente.')
        return

    if target == 'leads':
        await update.message.reply_text('📊 Exportando prospectos a Google Sheets... Te aviso cuando esté listo.')
        queue = django_rq.get_queue('default')
        queue.enqueue(
            'core.agent.infrastructure.jobs.sheets_export_job',
            chat_id=update.effective_chat.id,
            job_timeout=60,
        )
    else:
        await update.message.reply_text(
            f'❌ Opción no reconocida: `{target}`\n'
            'Usa `/exportar leads` para exportar prospectos.',
            parse_mode='Markdown',
        )


async def cmd_importar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /importar
    Lee datos desde Google Sheets y los muestra.
    """
    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text('No estás autorizado para usar este agente.')
        return

    await update.message.reply_text('📊 Leyendo tu Google Sheet... Te aviso cuando tenga los datos.')
    queue = django_rq.get_queue('default')
    queue.enqueue(
        'core.agent.infrastructure.jobs.sheets_read_job',
        chat_id=update.effective_chat.id,
        job_timeout=30,
    )


async def cmd_drive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/drive <búsqueda> — busca en Google Drive"""
    args = ' '.join(context.args).strip()
    if not args:
        await update.message.reply_text(
            '🗂 Uso: `/drive <búsqueda>`\n'
            'Ejemplo: `/drive propuesta identidad de marca`',
            parse_mode='Markdown',
        )
        return

    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text("No estás autorizado para usar este agente.")
        return

    await update.message.reply_text('🗂 Buscando en Google Drive...', parse_mode='Markdown')
    queue = django_rq.get_queue('default')
    queue.enqueue(
        'core.agent.infrastructure.jobs.drive_search_job',
        kwargs={'query': args, 'chat_id': update.effective_chat.id},
        job_timeout=60,
    )


async def cmd_reporte(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /reporte [month] [year]
    /reporte          → mes actual
    /reporte 4 2026   → abril 2026
    """
    from django.utils import timezone
    args = context.args
    now = timezone.now()

    try:
        month = int(args[0]) if args else now.month
        year = int(args[1]) if len(args) > 1 else now.year
        if not (1 <= month <= 12):
            raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text("Uso: `/reporte [mes 1-12] [año]`", parse_mode='Markdown')
        return

    await _reply_tool(update, context, 'generate_monthly_report', month=month, year=year)


# ─── mensajes de texto libres ───────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Primero checar si es un JSON de cookies importadas
    if await handle_cookie_json(update, context):
        return

    chat_id = update.effective_chat.id
    user = update.effective_user
    text = update.message.text

    await context.bot.send_chat_action(chat_id=chat_id, action='typing')

    try:
        response = await process_message(
            chat_id, user.username or '', user.full_name or '', text,
        )
    except Exception as e:
        logger.error(f"Error en handle_message: {e}", exc_info=True)
        response = "Error interno. Intenta de nuevo en un momento."

    await update.message.reply_text(response)


# ─── mensajes de voz / audio ────────────────────────────────────────────────

async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text("No estás autorizado para usar este agente.")
        return

    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    await update.message.reply_text("🎙 Transcribiendo tu audio...")

    voice = update.message.voice or update.message.audio
    file = await context.bot.get_file(voice.file_id)

    with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        await file.download_to_drive(tmp_path)
        result = await run_tool('transcribe_audio', session.id, audio_path=tmp_path)

        if result and result.success:
            duration = result.metadata.get('duration', '?')
            await update.message.reply_text(
                f"📝 *Transcripción* ({duration}s)\n\n{result.content}\n\n"
                "¿Quieres que haga algo con este texto? (generar un short, post, etc.)",
                parse_mode='Markdown',
            )
        else:
            await update.message.reply_text(
                result.error if result else "No pude transcribir el audio."
            )
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


# ─── comando principal ──────────────────────────────────────────────────────

class Command(BaseCommand):
    help = 'Ejecuta el bot de Telegram del agente de negocio (polling)'

    def handle(self, *args, **options):
        token = settings.TELEGRAM_BOT_TOKEN
        if not token:
            self.stderr.write("ERROR: TELEGRAM_BOT_TOKEN no configurado en .env")
            return

        self.stdout.write("Iniciando bot de Telegram en modo polling...")

        app = Application.builder().token(token).build()

        # Comandos básicos
        app.add_handler(CommandHandler('start', cmd_start))
        app.add_handler(CommandHandler('ayuda', cmd_ayuda))
        app.add_handler(CommandHandler('estado', cmd_estado))
        app.add_handler(CommandHandler('consumo', cmd_consumo))

        # Herramientas
        app.add_handler(CommandHandler('estadisticas', cmd_estadisticas))
        app.add_handler(CommandHandler('credentials', cmd_credentials))
        app.add_handler(CommandHandler('post', cmd_post))
        app.add_handler(CommandHandler('texto', cmd_texto))
        app.add_handler(CommandHandler('buscar', cmd_buscar))
        app.add_handler(CommandHandler('documento', cmd_documento))
        app.add_handler(CommandHandler('short', cmd_short))
        app.add_handler(CommandHandler('reporte', cmd_reporte))
        app.add_handler(CommandHandler('prospectar', cmd_prospectar))
        app.add_handler(CommandHandler('contactado', cmd_contactado))
        app.add_handler(CommandHandler('prospecto', cmd_prospecto))
        app.add_handler(CommandHandler('usuarios', cmd_usuarios))
        app.add_handler(CommandHandler('imagen', cmd_imagen))
        app.add_handler(CommandHandler('video', cmd_video))
        app.add_handler(CommandHandler('audio', cmd_audio))
        app.add_handler(CommandHandler('agenda', cmd_agenda))
        app.add_handler(CommandHandler('calendario', cmd_calendario))
        app.add_handler(CommandHandler('exportar', cmd_exportar))
        app.add_handler(CommandHandler('importar', cmd_importar))
        app.add_handler(CommandHandler('drive', cmd_drive))
        app.add_handler(CommandHandler('consultar', cmd_consultar))
        app.add_handler(MessageHandler(filters.Document.PDF | filters.Document.FileExtension('docx'), handle_document))

        # Mensajes
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_voice))

        self.stdout.write("Bot activo. Esperando mensajes...")
        app.run_polling(allowed_updates=Update.ALL_TYPES)
