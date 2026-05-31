"""
RQ jobs para el agente. Se ejecutan en el contenedor rqworker.
"""
import json
import logging
import re
import requests
from django.conf import settings
from core.agent.infrastructure.n8n_client import N8nClient

_SOCIAL_DOMAINS = {
    'instagram': 'instagram.com',
    'linkedin': 'linkedin.com',
    'facebook': 'facebook.com',
    'tiktok': 'tiktok.com',
}

# Mapeo platform → workflow_id en n8n (para plataformas con nombres de workflow no estándar)
_WORKFLOW_MAPPING = {
    'facebook_tuwebmx': 'facebook_stats_tuwebmx',
    'facebook_anuarbarrera': 'facebook_stats_anuarbarrera',
}


def detect_social_platform(url: str) -> str | None:
    """Retorna la plataforma social de la URL, o None si no es social."""
    url_lower = url.lower()
    for platform, domain in _SOCIAL_DOMAINS.items():
        if domain in url_lower:
            return platform
    return None


logger = logging.getLogger(__name__)

SHEET_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}"


def _send_telegram(chat_id: int, text: str, parse_mode: str = 'Markdown') -> None:
    """Envía mensaje de Telegram de forma síncrona (para uso en workers RQ)."""
    token = settings.TELEGRAM_BOT_TOKEN
    if not token or not chat_id:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={'chat_id': chat_id, 'text': text, 'parse_mode': parse_mode},
            timeout=10,
        )
    except Exception as e:
        logger.error(f"Error enviando Telegram a {chat_id}: {e}")


def scrape_post_job(url: str, chat_id: int) -> None:
    """
    Scraping de estadísticas de un post en redes sociales via Playwright.
    Se ejecuta en el rqworker y notifica al usuario por Telegram.
    """
    from core.agent.infrastructure.browser import scrape_post_stats

    logger.info(f"Scraping estadísticas: {url}")

    try:
        # Intentar cargar cookies de sesión si existen
        cookies = _get_browser_cookies(url)
        stats = scrape_post_stats(url, cookies=cookies)
        _send_telegram(chat_id, stats.format_telegram())
    except Exception as e:
        logger.error(f"Error en scrape_post_job: {e}", exc_info=True)
        _send_telegram(chat_id, f"❌ Error al obtener estadísticas: {e}")


def _get_browser_cookies(url: str) -> list:
    """Carga cookies de sesión guardadas para la plataforma del URL."""
    try:
        from core.agent.infrastructure.browser import detect_platform
        from core.agent.infrastructure.models import BrowserSession
        platform = detect_platform(url)
        session = BrowserSession.objects.filter(platform=platform, is_valid=True).first()
        return session.cookies if session else []
    except Exception:
        return []


def _score_leads_with_gemini(leads: list, giro: str) -> list:
    """Asigna score 1-10 a cada lead. Modifica y retorna la lista."""
    from core.agent.infrastructure.gemini_adapter import GeminiAdapter
    gemini = GeminiAdapter()
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        for lead in leads:
            lead.setdefault('score', 5)
            lead.setdefault('score_reason', '')
        return leads

    model = getattr(settings, 'AI_MODEL', 'gemini-2.5-flash')
    leads_summary = json.dumps([{
        'name': l.get('name', ''),
        'phone': l.get('phone', ''),
        'website': l.get('website', ''),
        'rating': l.get('rating'),
        'reviews': l.get('reviews_total', 0),
    } for l in leads], ensure_ascii=False)

    prompt = (
        f'Eres un asesor de ventas digitales. Analiza estos negocios del giro "{giro}" '
        f'y asigna a cada uno un score del 1 al 10 como posible cliente para una agencia web.\n\n'
        f'Criterios:\n'
        f'- Sin website: +4 puntos (necesitan uno urgente)\n'
        f'- Teléfono disponible: +3 puntos\n'
        f'- Rating >= 3.5: +2 puntos\n'
        f'- Muchas reseñas (>50): +1 punto\n\n'
        f'Negocios (en este orden exacto):\n{leads_summary}\n\n'
        f'Responde SOLO con un JSON array con un objeto por negocio, en el mismo orden, '
        f'con campos "score" (int 1-10) y "reason" (frase corta en español). '
        f'Ejemplo: [{{"score": 9, "reason": "sin web, teléfono disponible"}}]'
    )

    try:
        raw = gemini.generate_response(
            prompt=prompt, api_key=api_key, model_name=model, thinking_budget=0
        )
        json_str = re.sub(r'^```json\n?|^```\n?|```$', '', raw.strip(), flags=re.MULTILINE).strip()
        scores = json.loads(json_str)
        for i, lead in enumerate(leads):
            if i < len(scores):
                lead['score'] = int(scores[i].get('score', 5))
                lead['score_reason'] = str(scores[i].get('reason', ''))
            else:
                lead.setdefault('score', 5)
                lead.setdefault('score_reason', '')
    except Exception as e:
        logger.warning(f'Error scoring con Gemini: {e} — usando score 5 por defecto')
        for lead in leads:
            lead.setdefault('score', 5)
            lead.setdefault('score_reason', '')

    return leads


def prospect_n8n_job(giro: str, lat: float, lng: float, rango_km: float, chat_id: int) -> None:
    """Llama al webhook de n8n, deduplica por place_id, scoring con Gemini y notifica."""
    n8n_url = getattr(settings, 'N8N_WEBHOOK_URL', '')
    if not n8n_url:
        _send_telegram(chat_id, '❌ Error: URL de n8n no configurada.')
        return

    logger.info(f'Prospección: giro={giro}, lat={lat}, lng={lng}, rango={rango_km}km')
    try:
        resp = requests.post(
            n8n_url,
            json={'giro': giro, 'lat': lat, 'lng': lng, 'rango_km': rango_km},
            timeout=360,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.Timeout:
        logger.error('Timeout en prospección n8n')
        _send_telegram(
            chat_id,
            '⚠️ La prospección está tardando más de lo esperado. '
            'Revisa tu Google Sheet en unos minutos.',
        )
        return
    except Exception as e:
        logger.error(f'Error HTTP n8n prospección: {e}')
        _send_telegram(chat_id, f'❌ Error al conectar con n8n: {e}')
        return

    leads_raw = data.get('leads', [])
    total_found = data.get('total', len(leads_raw))

    if not leads_raw:
        _send_telegram(chat_id, f'🔍 Prospección completada — 0 resultados para *{giro}*.')
        return

    # Dedup: filtrar place_ids ya guardados para este usuario
    from core.agent.infrastructure.models import ProspectLead
    existing_ids = set(
        ProspectLead.objects.filter(
            chat_id=str(chat_id),
            place_id__in=[l['place_id'] for l in leads_raw if l.get('place_id')],
        ).values_list('place_id', flat=True)
    )
    new_leads = [l for l in leads_raw if l.get('place_id') and l['place_id'] not in existing_ids]
    duplicates = total_found - len(new_leads)

    if not new_leads:
        _send_telegram(
            chat_id,
            f'🔍 Prospección completada — todos los {total_found} resultados ya estaban en tu historial.',
        )
        return

    # Scoring con Gemini
    new_leads = _score_leads_with_gemini(new_leads, giro)
    new_leads.sort(key=lambda l: l.get('score', 0), reverse=True)

    # Guardar en BD
    for lead in new_leads:
        try:
            ProspectLead.objects.create(
                place_id=lead['place_id'],
                chat_id=str(chat_id),
                name=lead.get('name', ''),
                address=lead.get('address', ''),
                phone=lead.get('phone', ''),
                website=lead.get('website', ''),
                rating=lead.get('rating'),
                reviews_total=lead.get('reviews_total', 0),
                giro=giro,
                lat=lead.get('lat'),
                lng=lead.get('lng'),
                score=lead.get('score'),
                score_reason=lead.get('score_reason', ''),
            )
        except Exception as e:
            logger.warning(f'Error guardando lead {lead.get("place_id")}: {e}')

    # Formatear mensaje (top 10)
    top = new_leads[:10]
    lines = [f'✅ *{len(new_leads)} nuevos prospectos* encontrados para *{giro}*']
    if duplicates > 0:
        lines.append(f'_(+{duplicates} ya vistos anteriormente, omitidos)_')
    lines.append('')
    for i, lead in enumerate(top, 1):
        score_val = lead.get('score') or 0
        score_emoji = '🔥' if score_val >= 8 else '⭐' if score_val >= 5 else '📍'
        phone_line = f' · 📞 {lead["phone"]}' if lead.get('phone') else ''
        web_line = f' · 🌐 sin web' if not lead.get('website') else ''
        score_line = f' · Score: {score_val}/10' if lead.get('score') else ''
        lines.append(
            f'{score_emoji} *{i}. {lead.get("name", "Sin nombre")}*{score_line}\n'
            f'_{lead.get("address", "")}{phone_line}{web_line}_'
        )
    if len(new_leads) > 10:
        lines.append(f'\n_...y {len(new_leads) - 10} más guardados en tu historial._')
    lines.append('\nUsa `/contactado` cuando hayas contactado este batch.')
    _send_telegram(chat_id, '\n'.join(lines))


def stats_n8n_job(url: str, platform: str, chat_id: int) -> None:
    """Despacha obtención de stats de post a n8n. La respuesta llega por callback."""
    from core.agent.infrastructure.models import PendingJob
    workflow_id = _WORKFLOW_MAPPING.get(platform, f'{platform}_stats')
    job = PendingJob.objects.create(
        chat_id=str(chat_id),
        command='estadisticas',
        workflow=workflow_id,
    )
    try:
        client = N8nClient()
        client.dispatch(
            workflow_id=workflow_id,
            params={'url': url},
            job_id=str(job.job_id),
            chat_id=str(chat_id),
        )
    except Exception as e:
        logger.error(f'Error despachando stats a n8n: {e}', exc_info=True)
        job.status = 'failed'
        job.save()
        _send_telegram(chat_id, f'❌ Error al conectar con el servicio de estadísticas: {e}')


def competitor_n8n_job(name: str, social_url: str, platform: str, chat_id: int) -> None:
    """Despacha investigación de perfil social competidor a n8n. Respuesta llega por callback."""
    from core.agent.infrastructure.models import PendingJob
    workflow_id = f'{platform}_competitor'
    job = PendingJob.objects.create(
        chat_id=str(chat_id),
        command='prospecto',
        workflow=workflow_id,
    )
    try:
        client = N8nClient()
        client.dispatch(
            workflow_id=workflow_id,
            params={'url': social_url, 'name': name},
            job_id=str(job.job_id),
            chat_id=str(chat_id),
        )
    except Exception as e:
        logger.error(f'Error despachando competitor a n8n: {e}', exc_info=True)
        job.status = 'failed'
        job.save()
        _send_telegram(chat_id, f'❌ Error al investigar el perfil social: {e}')


def _send_telegram_video(chat_id: int, video_bytes: bytes) -> None:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token or not chat_id:
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{token}/sendVideo',
            data={'chat_id': str(chat_id), 'caption': '🎬 Video generado'},
            files={'video': ('video.mp4', video_bytes, 'video/mp4')},
            timeout=120,
        )
    except Exception as e:
        logger.error(f'Error enviando video a Telegram {chat_id}: {e}')


def _search_pexels_clip(keyword: str, api_key: str) -> str | None:
    """Busca un clip en Pexels y lo descarga a un archivo temporal. Retorna ruta local o None."""
    try:
        resp = requests.get(
            'https://api.pexels.com/videos/search',
            headers={'Authorization': api_key},
            params={'query': keyword, 'per_page': 3, 'size': 'medium', 'orientation': 'landscape'},
            timeout=15,
        )
        resp.raise_for_status()
        videos = resp.json().get('videos', [])
        if not videos:
            logger.warning(f'Pexels: sin resultados para "{keyword}"')
            return None
        for video in videos:
            for vf in sorted(video.get('video_files', []), key=lambda x: x.get('width', 0)):
                if vf.get('file_type') == 'video/mp4' and vf.get('quality') in ('sd', 'hd'):
                    link = vf['link']
                    break
            else:
                continue
            r = requests.get(link, timeout=60)
            r.raise_for_status()
            import tempfile as _tmp
            f = _tmp.NamedTemporaryFile(suffix='.mp4', delete=False)
            f.write(r.content)
            f.close()
            return f.name
    except Exception as e:
        logger.warning(f'Error descargando clip Pexels "{keyword}": {e}')
    return None


def calendar_create_job(description: str, chat_id: int) -> None:
    """
    Parses natural language description with Gemini, creates Google Calendar event via n8n.
    Runs in rqworker.
    """
    from core.agent.infrastructure.gemini_adapter import GeminiAdapter
    from django.utils import timezone
    import uuid

    workflow_id = getattr(settings, 'N8N_WORKFLOW_CALENDAR_CREATE', '')
    if not workflow_id:
        _send_telegram(chat_id, '❌ Google Calendar no está configurado.')
        return

    now = timezone.now().isoformat()
    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    model = getattr(settings, 'AI_MODEL', 'gemini-3.5-flash')

    prompt = (
        f'Fecha y hora actual: {now}\n'
        f'Solicitud del usuario: "{description}"\n\n'
        'Extrae los datos del evento de calendario como JSON con este formato exacto:\n'
        '{\n'
        '  "title": "Título del evento",\n'
        '  "start_datetime": "2026-05-25T15:00:00-06:00",\n'
        '  "end_datetime": "2026-05-25T16:00:00-06:00",\n'
        '  "description": "notas opcionales"\n'
        '}\n\n'
        'Zona horaria: México (UTC-6). Si no se indica hora de fin, suma 1 hora al inicio.\n'
        'Responde SOLO con el JSON, sin texto adicional, sin markdown, sin ```.'
    )

    try:
        gemini = GeminiAdapter()
        raw = gemini.generate_response(prompt=prompt, api_key=api_key, model_name=model)
        raw = raw.strip().strip('`').strip()
        if raw.startswith('json'):
            raw = raw[4:].strip()
        import json
        event_data = json.loads(raw)
    except Exception as e:
        logger.error(f'Error parseando evento de Gemini: {e}')
        _send_telegram(chat_id, '❌ No pude interpretar la fecha/hora del evento. Intenta con: "/agenda Reunión con X el viernes 23 mayo a las 3pm"')
        return

    from core.agent.infrastructure.models import PendingJob
    job_id = str(uuid.uuid4())
    PendingJob.objects.create(
        job_id=job_id,
        chat_id=str(chat_id),
        command='agenda',
        workflow=workflow_id,
    )

    try:
        N8nClient().dispatch(
            workflow_id=workflow_id,
            params=event_data,
            job_id=job_id,
            chat_id=str(chat_id),
        )
    except Exception as e:
        logger.error(f'Error dispatch calendar_create: {e}')
        _send_telegram(chat_id, f'❌ Error al crear el evento: {e}')


def calendar_list_job(days: int, chat_id: int) -> None:
    """
    Lists next N days of Google Calendar events via n8n.
    Runs in rqworker.
    """
    import uuid
    from core.agent.infrastructure.models import PendingJob

    workflow_id = getattr(settings, 'N8N_WORKFLOW_CALENDAR_LIST', '')
    if not workflow_id:
        _send_telegram(chat_id, '❌ Google Calendar no está configurado.')
        return

    job_id = str(uuid.uuid4())
    PendingJob.objects.create(
        job_id=job_id,
        chat_id=str(chat_id),
        command='calendario',
        workflow=workflow_id,
    )

    try:
        N8nClient().dispatch(
            workflow_id=workflow_id,
            params={'days': days},
            job_id=job_id,
            chat_id=str(chat_id),
        )
    except Exception as e:
        logger.error(f'Error dispatch calendar_list: {e}')
        _send_telegram(chat_id, f'❌ Error al consultar el calendario: {e}')


def video_pexels_job(prompt: str, chat_id: int) -> None:
    """Genera video: Gemini crea guión + keywords, Edge-TTS narra, Pexels clips, MoviePy ensambla."""
    import json as _json
    import re as _re
    import os as _os
    import tempfile as _tmp
    from core.agent.infrastructure.gemini_adapter import GeminiAdapter
    from core.agent.infrastructure.tools.media_tools import _tts

    api_key = settings.GEMINI_API_KEY
    pexels_key = getattr(settings, 'PEXELS_API_KEY', '')
    model = getattr(settings, 'AI_MODEL', 'gemini-2.5-flash')

    if not pexels_key:
        _send_telegram(chat_id, '❌ PEXELS_API_KEY no configurada en .env')
        return

    temp_files = []
    try:
        # 1. Gemini genera narración en español + keywords en inglés para stock footage
        gemini = GeminiAdapter()
        script_prompt = (
            f'Eres un creador de contenido para redes sociales. '
            f'Crea el script para un video corto de 45 segundos sobre: "{prompt}"\n\n'
            f'Responde ÚNICAMENTE con este JSON sin markdown:\n'
            f'{{"narration": "texto completo de la narración en español (máx 120 palabras)", '
            f'"scenes": ["keyword en inglés escena 1", "keyword en inglés escena 2", "keyword en inglés escena 3"]}}\n'
            f'Las keywords describen stock footage (ej: "professional web design laptop", "business meeting office").'
        )
        raw = gemini.generate_response(
            prompt=script_prompt, api_key=api_key, model_name=model, thinking_budget=0
        )
        json_str = _re.sub(r'^```json\n?|^```\n?|```$', '', raw.strip(), flags=_re.MULTILINE).strip()
        script = _json.loads(json_str)
        narration = script['narration']
        scenes = script['scenes'][:3]

        # 2. Edge-TTS genera narración
        audio_bytes = _tts(narration)
        audio_tmp = _tmp.NamedTemporaryFile(suffix='.mp3', delete=False)
        audio_tmp.write(audio_bytes)
        audio_tmp.close()
        temp_files.append(audio_tmp.name)

        # 3. Pexels: descargar clips
        clip_paths = []
        for keyword in scenes:
            path = _search_pexels_clip(keyword, pexels_key)
            if path:
                clip_paths.append(path)
                temp_files.append(path)

        if not clip_paths:
            _send_telegram(chat_id, '❌ No se encontraron clips en Pexels. Intenta con una descripción más general.')
            return

        # 4. MoviePy: ensamblar clips + audio
        # MoviePy 1.0.3 usa PIL.Image.ANTIALIAS que fue eliminado en Pillow 10+
        import PIL.Image as _pil_image
        if not hasattr(_pil_image, 'ANTIALIAS'):
            _pil_image.ANTIALIAS = _pil_image.LANCZOS
        from moviepy.editor import VideoFileClip, concatenate_videoclips, AudioFileClip

        audio_clip = AudioFileClip(audio_tmp.name)
        total_dur = audio_clip.duration
        clip_dur = total_dur / len(clip_paths)

        processed = []
        for path in clip_paths:
            raw_clip = VideoFileClip(path)
            cut = min(clip_dur, raw_clip.duration)
            c = raw_clip.subclip(0, cut).resize(width=1080)
            processed.append(c)

        final = concatenate_videoclips(processed, method='compose').set_audio(audio_clip)

        output_tmp = _tmp.NamedTemporaryFile(suffix='.mp4', delete=False)
        output_path = output_tmp.name
        output_tmp.close()
        temp_files.append(output_path)

        final.write_videofile(
            output_path, fps=24, codec='libx264', audio_codec='aac',
            logger=None, threads=2,
        )

        # 5. Enviar a Telegram
        with open(output_path, 'rb') as f:
            _send_telegram_video(chat_id, f.read())

    except Exception as e:
        logger.error(f'Error en video_pexels_job: {e}', exc_info=True)
        _send_telegram(chat_id, f'❌ Error generando el video: {e}')
    finally:
        for path in temp_files:
            try:
                _os.unlink(path)
            except Exception:
                pass


def sheets_export_job(chat_id: int) -> None:
    """
    Reads ProspectLead from DB for chat_id and exports to Google Sheets via n8n.
    Runs in rqworker.
    """
    import uuid
    from core.agent.infrastructure.models import PendingJob, ProspectLead

    workflow_id = getattr(settings, 'N8N_WORKFLOW_SHEETS_EXPORT', '')
    sheet_id = getattr(settings, 'GOOGLE_SHEETS_LEADS_ID', '')
    if not workflow_id:
        _send_telegram(chat_id, '❌ Google Sheets no está configurado.')
        return

    leads_qs = ProspectLead.objects.filter(chat_id=str(chat_id)).order_by('-searched_at')
    if not leads_qs.exists():
        _send_telegram(chat_id, '📊 No hay prospectos guardados para exportar. Usa `/prospectar` primero.')
        return

    leads_data = [
        {
            'name': lead.name,
            'phone': lead.phone,
            'address': lead.address,
            'website': lead.website,
            'rating': lead.rating,
            'reviews_total': lead.reviews_total,
            'giro': lead.giro,
            'score': lead.score,
            'contacted': lead.contacted,
            'searched_at': lead.searched_at.isoformat() if lead.searched_at else '',
        }
        for lead in leads_qs
    ]

    job_id = str(uuid.uuid4())
    PendingJob.objects.create(
        job_id=job_id,
        chat_id=str(chat_id),
        command='exportar',
        workflow=workflow_id,
    )

    try:
        N8nClient().dispatch(
            workflow_id=workflow_id,
            params={'leads': leads_data, 'sheet_id': sheet_id},
            job_id=job_id,
            chat_id=str(chat_id),
        )
    except Exception as e:
        logger.error(f'Error dispatch sheets_export: {e}')
        _send_telegram(chat_id, f'❌ Error al exportar a Google Sheets: {e}')


def sheets_read_job(chat_id: int, sheet_range: str = 'A:Z') -> None:
    """
    Reads data from Google Sheets via n8n and sends to user via callback.
    Runs in rqworker.
    """
    import uuid
    from core.agent.infrastructure.models import PendingJob

    workflow_id = getattr(settings, 'N8N_WORKFLOW_SHEETS_READ', '')
    sheet_id = getattr(settings, 'GOOGLE_SHEETS_LEADS_ID', '')
    if not workflow_id:
        _send_telegram(chat_id, '❌ Google Sheets no está configurado.')
        return

    job_id = str(uuid.uuid4())
    PendingJob.objects.create(
        job_id=job_id,
        chat_id=str(chat_id),
        command='importar',
        workflow=workflow_id,
    )

    try:
        N8nClient().dispatch(
            workflow_id=workflow_id,
            params={'sheet_id': sheet_id, 'range': sheet_range},
            job_id=job_id,
            chat_id=str(chat_id),
        )
    except Exception as e:
        logger.error(f'Error dispatch sheets_read: {e}')
        _send_telegram(chat_id, f'❌ Error al leer Google Sheets: {e}')


def drive_search_job(query: str, chat_id: int) -> None:
    """
    Busca archivos en Google Drive via n8n. La respuesta llega por callback.
    Runs in rqworker.
    """
    import uuid
    from core.agent.infrastructure.models import PendingJob

    workflow_id = getattr(settings, 'N8N_WORKFLOW_DRIVE_SEARCH', '')
    folder_id = getattr(settings, 'GOOGLE_DRIVE_FOLDER_ID', '')
    if not workflow_id:
        _send_telegram(chat_id, '❌ Google Drive no está configurado.')
        return

    job_id = str(uuid.uuid4())
    PendingJob.objects.create(
        job_id=job_id,
        chat_id=str(chat_id),
        command='drive',
        workflow=workflow_id,
    )

    try:
        N8nClient().dispatch(
            workflow_id=workflow_id,
            params={'query': query, 'folder_id': folder_id},
            job_id=job_id,
            chat_id=str(chat_id),
        )
    except Exception as e:
        logger.error(f'Error dispatch drive_search: {e}')
        _send_telegram(chat_id, f'❌ Error al buscar en Drive: {e}')
