import asyncio
import logging
from datetime import date, timedelta
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Sum
from telegram import Bot
from core.agent.infrastructure.gemini_adapter import GeminiAdapter, FALLBACK_MESSAGE
from core.agent.infrastructure.models import AgentRequest

logger = logging.getLogger(__name__)


def build_daily_summary() -> str:
    """Construye el texto del resumen diario consultando la BD y Gemini."""
    today = date.today()
    yesterday = today - timedelta(days=1)

    qs = AgentRequest.objects.filter(timestamp__date=yesterday)
    total_requests = qs.count()
    total_tokens = qs.aggregate(t=Sum('estimated_tokens'))['t'] or 0
    tools_used = list(qs.values_list('tool_used', flat=True).distinct())

    tools_str = ", ".join(t for t in tools_used if t) or "ninguna"

    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    if not api_key:
        return (
            f'📊 Resumen del {yesterday}\n\n'
            f'• Solicitudes: {total_requests}\n'
            f'• Tokens consumidos: {total_tokens:,}\n'
            f'• Herramientas usadas: {tools_str}\n\n'
            f'Buenos días. Que tengas un gran día.'
        )

    prompt = (
        f'Eres un asistente de negocio. Genera un breve resumen matutino en español para el dueño del negocio.\n\n'
        f'Estadísticas del día anterior ({yesterday}):\n'
        f'- Solicitudes al agente: {total_requests}\n'
        f'- Tokens consumidos: {total_tokens:,}\n'
        f'- Herramientas utilizadas: {tools_str}\n\n'
        f'Redacta el resumen en 3-5 líneas. Incluye los datos, un comentario motivador y '
        f'una sugerencia de acción para hoy. Usa emojis. Sin formato Markdown especial.'
    )
    try:
        gemini = GeminiAdapter()
        model = getattr(settings, 'AI_MODEL', 'gemini-3.1-flash-lite')
        result = gemini.generate_response(prompt=prompt, api_key=api_key, model_name=model)
        if result == FALLBACK_MESSAGE or not result.strip():
            raise ValueError('Gemini returned fallback')
        return result
    except Exception as e:
        logger.error(f'Error generando resumen con Gemini: {e}', exc_info=True)
        return (
            f'📊 Resumen del {yesterday}\n\n'
            f'• Solicitudes: {total_requests}\n'
            f'• Tokens: {total_tokens:,}\n'
            f'• Herramientas: {tools_str}'
        )


class Command(BaseCommand):
    help = 'Envía el resumen diario por Telegram'

    def handle(self, *args, **options):
        token = getattr(settings, 'TELEGRAM_BOT_TOKEN', '')
        chat_id = getattr(settings, 'TELEGRAM_CHAT_ID', '')
        if not token or not chat_id:
            self.stderr.write('TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID no configurados.')
            return
        summary = build_daily_summary()

        async def _send():
            bot = Bot(token=token)
            try:
                await bot.send_message(chat_id=chat_id, text=summary, parse_mode='Markdown')
            except Exception:
                await bot.send_message(chat_id=chat_id, text=summary)

        asyncio.run(_send())
        self.stdout.write(f'Resumen enviado a chat_id={chat_id}')
