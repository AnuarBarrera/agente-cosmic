import time
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
# ---------------------------------------------------------------------------
_GEMINI_INPUT_COST_PER_TOKEN = 0.075    # USD / 1M
_GEMINI_OUTPUT_COST_PER_TOKEN = 0.300   # USD / 1M
_IMAGEN_COST_PER_IMAGE = 40000          # $0.04 = 40,000 microdólares


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


def record_tokens(resp, operation: str = 'unknown', user_email: str = '', job_id: str = '', response_preview: str = ''):
    input_tokens = 0
    output_tokens = 0
    try:
        usage = getattr(resp, 'usage_metadata', None)
        if usage:
            input_tokens = getattr(usage, 'prompt_token_count', 0) or 0
            output_tokens = getattr(usage, 'candidates_token_count', 0) or 0
            if input_tokens:
                _redis_inc(f'cosmic:prom:G:input:{operation}', input_tokens)
                cost_in = int(input_tokens * _GEMINI_INPUT_COST_PER_TOKEN / 1000)
                if cost_in > 0:
                    _redis_inc(f'cosmic:prom:GC:{operation}', cost_in)
            if output_tokens:
                _redis_inc(f'cosmic:prom:G:output:{operation}', output_tokens)
                cost_out = int(output_tokens * _GEMINI_OUTPUT_COST_PER_TOKEN / 1000)
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
            'response_preview': (response_preview or '')[:300],
        }, ensure_ascii=False))
    except Exception:
        pass


def record_imagen_generation(imagen_type: str = 'generate'):
    """Registra una generación de Imagen 3 con su costo estimado."""
    _redis_inc(f'cosmic:prom:I:{imagen_type}')
    _redis_inc(f'cosmic:prom:IC:{imagen_type}', _IMAGEN_COST_PER_IMAGE)


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
