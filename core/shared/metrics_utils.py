import time
import math
import logging
from contextlib import contextmanager
from core.shared.metrics import (
    EXTERNAL_API_REQUESTS,
    EXTERNAL_API_DURATION,
    EXTERNAL_API_ERRORS,
)

logger = logging.getLogger(__name__)
llm_audit = logging.getLogger('cosmic.llm_audit')

# ---------------------------------------------------------------------------
# Precios (microdólares para evitar floats en contadores)
# Gemini 2.5 Flash: $0.075/1M input tokens, $0.30/1M output tokens
# Imagen 3 generate / bgswap: $0.04/imagen
# Veo 3 Fast (video sin audio): estimado $0.10/segundo — verificar contra
#   facturación real de GCP, este entorno no tiene acceso a la Cloud Billing
#   Catalog API para confirmar la tarifa exacta vigente.
# Cloud Speech-to-Text (modelo estándar): $0.024/min, facturado en bloques
#   de 15s = $0.006/bloque — tarifa pública estándar, documentada.
# Lyria 3 Clip (preview) y gemini-2.5-flash-tts (audio): sin tarifa pública
#   confirmada para este entorno — se registra solo conteo/uso, no costo.
# ---------------------------------------------------------------------------
_GEMINI_INPUT_COST_PER_TOKEN = 0.075    # USD / 1M
_GEMINI_OUTPUT_COST_PER_TOKEN = 0.300   # USD / 1M
_IMAGEN_COST_PER_IMAGE = 40000          # $0.04 = 40,000 microdólares
_VEO_COST_PER_SECOND = 100000           # $0.10/s = 100,000 microdólares (estimado)
_STT_COST_PER_15S_BLOCK = 6000          # $0.006/bloque de 15s = 6,000 microdólares


def _redis_inc(key: str, amount: float = 1.0) -> None:
    """Incrementa un contador en Redis — falla silenciosamente si Redis no está disponible."""
    try:
        import django_rq
        r = django_rq.get_connection('default')
        r.incrbyfloat(key, amount)
    except Exception:
        pass


@contextmanager
def track_external_api(service: str, operation: str = ''):
    start = time.monotonic()
    try:
        yield
        elapsed = time.monotonic() - start
        EXTERNAL_API_REQUESTS.labels(service=service, status='success').inc()
        EXTERNAL_API_DURATION.labels(service=service).observe(elapsed)
        if operation:
            _redis_inc(f'cosmic:prom:L:{operation}:success')
    except Exception as exc:
        elapsed = time.monotonic() - start
        EXTERNAL_API_DURATION.labels(service=service).observe(elapsed)
        error_type = _classify_error(exc)
        EXTERNAL_API_REQUESTS.labels(service=service, status='error').inc()
        EXTERNAL_API_ERRORS.labels(service=service, error_type=error_type).inc()
        if operation:
            _redis_inc(f'cosmic:prom:L:{operation}:error')
        raise


def record_tokens(resp, operation: str = 'unknown', user_email: str = '', job_id: str = '', response_preview: str = '', prompt_preview: str = ''):
    input_tokens = 0
    output_tokens = 0
    try:
        usage = getattr(resp, 'usage_metadata', None)
        if usage:
            input_tokens = getattr(usage, 'prompt_token_count', 0) or 0
            output_tokens = getattr(usage, 'candidates_token_count', 0) or 0
            if input_tokens:
                _redis_inc(f'cosmic:prom:G:input:{operation}', input_tokens)
                # 0.075 microdólares/token (= $0.075 por 1M tokens)
                cost_in = int(input_tokens * _GEMINI_INPUT_COST_PER_TOKEN)
                if cost_in > 0:
                    _redis_inc(f'cosmic:prom:GC:{operation}', cost_in)
            if output_tokens:
                _redis_inc(f'cosmic:prom:G:output:{operation}', output_tokens)
                # 0.300 microdólares/token (= $0.30 por 1M tokens)
                cost_out = int(output_tokens * _GEMINI_OUTPUT_COST_PER_TOKEN)
                if cost_out > 0:
                    _redis_inc(f'cosmic:prom:GC:{operation}', cost_out)
    except Exception:
        pass

    # Audit log estructurado — visible en /app/logs/llm_audit.jsonl
    try:
        import json
        llm_audit.info(json.dumps({
            'operation': operation,
            'user': user_email or 'system',
            'job_id': job_id or '',
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'est_cost_usd': round((input_tokens * 0.075 + output_tokens * 0.300) / 1_000_000, 6),
            'prompt_preview': (prompt_preview or '')[:500],
            'response_preview': (response_preview or '')[:500],
        }, ensure_ascii=False))
    except Exception:
        pass


def record_imagen_generation(imagen_type: str = 'generate'):
    """Registra una generación de Imagen 3 con su costo estimado."""
    _redis_inc(f'cosmic:prom:I:{imagen_type}')
    _redis_inc(f'cosmic:prom:IC:{imagen_type}', _IMAGEN_COST_PER_IMAGE)


def record_veo_generation(duration_seconds: float):
    """Registra un clip de Veo generado con su costo estimado (ver nota de precios arriba)."""
    _redis_inc('cosmic:prom:V:clips')
    _redis_inc('cosmic:prom:V:seconds', duration_seconds)
    cost = int(duration_seconds * _VEO_COST_PER_SECOND)
    if cost > 0:
        _redis_inc('cosmic:prom:VC:generate', cost)


def record_lyria_generation():
    """Registra una generación de música con Lyria — sin tarifa pública confirmada, solo conteo."""
    _redis_inc('cosmic:prom:LY:clips')


def record_tts_generation(char_count: int):
    """Registra una narración TTS generada — sin tarifa pública confirmada, solo conteo de caracteres."""
    _redis_inc('cosmic:prom:TTS:clips')
    if char_count > 0:
        _redis_inc('cosmic:prom:TTS:chars', char_count)


def record_stt_call(audio_duration_seconds: float):
    """Registra una llamada a Cloud Speech-to-Text con su costo estimado (bloques de 15s)."""
    _redis_inc('cosmic:prom:STT:calls')
    _redis_inc('cosmic:prom:STT:seconds', audio_duration_seconds)
    blocks = math.ceil(audio_duration_seconds / 15) if audio_duration_seconds > 0 else 0
    cost = blocks * _STT_COST_PER_15S_BLOCK
    if cost > 0:
        _redis_inc('cosmic:prom:STTC:recognize', cost)


def record_playwright_overlay_fallback(element: str):
    """Registra que un elemento (hook/cta) cayo de Playwright a drawtext — mide la tasa real de fallo bajo carga de produccion durante el experimento de REEL_TEXT_OVERLAY_ENGINE."""
    _redis_inc(f'reel_playwright_fallback_{element}_total')


def _classify_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if 'timeout' in msg or 'timed out' in msg:
        return 'timeout'
    if '429' in msg:
        return 'rate_limit'
    if any(c in msg for c in ('400', '401', '403', '404')):
        return 'client_error'
    if any(c in msg for c in ('500', '502', '503')):
        return 'server_error'
    if 'connection' in msg:
        return 'connection_error'
    return 'unknown'
