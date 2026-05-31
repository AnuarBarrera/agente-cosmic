import json
import logging
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.conf import settings
from django.utils import timezone
from core.agent.infrastructure.gemini_adapter import GeminiAdapter

logger = logging.getLogger(__name__)


@csrf_exempt
@require_POST
def n8n_callback(request):
    token = request.headers.get('X-N8N-Token', '')
    expected = getattr(settings, 'N8N_CALLBACK_TOKEN', '')
    if not expected or token != expected:
        return JsonResponse({'error': 'Unauthorized'}, status=401)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    job_id = body.get('job_id')
    chat_id = body.get('chat_id')
    status = body.get('status', 'ok')
    data = body.get('data', {})
    # n8n puede enviar `data` como string JSON si lo serializa como parámetro
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except (json.JSONDecodeError, ValueError):
            pass

    if not job_id or not chat_id:
        return JsonResponse({'error': 'Missing job_id or chat_id'}, status=400)

    from core.agent.infrastructure.models import PendingJob
    try:
        job = PendingJob.objects.get(job_id=job_id)
    except PendingJob.DoesNotExist:
        return JsonResponse({'error': 'Job not found'}, status=404)

    if status != 'ok':
        job.status = 'failed'
        job.completed_at = timezone.now()
        job.save()
        _send_telegram(chat_id, f'❌ Error en la operación: {data.get("error", "error desconocido")}')
        return JsonResponse({'ok': True})

    job.status = 'completed'
    job.completed_at = timezone.now()
    job.save()

    formatted = _format_with_gemini(job.command, data)
    _send_telegram(chat_id, formatted)
    return JsonResponse({'ok': True})


def _format_with_gemini(command: str, data: dict) -> str:
    gemini = GeminiAdapter()
    api_key = settings.GEMINI_API_KEY
    model = getattr(settings, 'AI_MODEL', 'gemini-2.5-flash')
    prompt = (
        f'Eres el asistente de negocio del usuario. '
        f'Formatea el siguiente resultado del comando /{command} en español, '
        f'de forma clara y concisa para Telegram (usa Markdown, máximo 3000 caracteres):\n\n'
        f'{json.dumps(data, ensure_ascii=False, indent=2)}'
    )
    try:
        return gemini.generate_response(prompt=prompt, api_key=api_key, model_name=model)
    except Exception as e:
        logger.error(f'Error formateando con Gemini en callback: {e}')
        return f'✅ Resultado recibido:\n```\n{json.dumps(data, ensure_ascii=False, indent=2)[:2000]}\n```'


def _send_telegram(chat_id: str, text: str) -> None:
    import requests
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        return
    # Telegram limita a 4096 caracteres
    text = text[:4000] + ('…' if len(text) > 4000 else '')
    for parse_mode in ('Markdown', None):
        try:
            payload = {'chat_id': chat_id, 'text': text}
            if parse_mode:
                payload['parse_mode'] = parse_mode
            resp = requests.post(
                f'https://api.telegram.org/bot{token}/sendMessage',
                json=payload,
                timeout=10,
            )
            if resp.ok:
                return
            err = resp.json().get('description', '')
            logger.warning(f'Telegram rechazó mensaje (parse_mode={parse_mode}): {err}')
            if 'too long' in err.lower():
                text = text[:2000] + '…'
        except Exception as e:
            logger.error(f'Error enviando Telegram desde callback: {e}')
            return
