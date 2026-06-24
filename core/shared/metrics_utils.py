import time
import logging
from contextlib import contextmanager
from core.shared.metrics import (
    EXTERNAL_API_REQUESTS,
    EXTERNAL_API_DURATION,
    EXTERNAL_API_ERRORS,
    GEMINI_TOKENS,
)

logger = logging.getLogger(__name__)


@contextmanager
def track_external_api(service: str):
    start = time.monotonic()
    try:
        yield
        elapsed = time.monotonic() - start
        EXTERNAL_API_REQUESTS.labels(service=service, status='success').inc()
        EXTERNAL_API_DURATION.labels(service=service).observe(elapsed)
    except Exception as exc:
        elapsed = time.monotonic() - start
        EXTERNAL_API_DURATION.labels(service=service).observe(elapsed)
        error_type = _classify_error(exc)
        EXTERNAL_API_REQUESTS.labels(service=service, status='error').inc()
        EXTERNAL_API_ERRORS.labels(service=service, error_type=error_type).inc()
        raise


def record_tokens(resp, service: str = 'gemini'):
    try:
        usage = getattr(resp, 'usage_metadata', None)
        if usage:
            prompt = getattr(usage, 'prompt_token_count', 0) or 0
            candidates = getattr(usage, 'candidates_token_count', 0) or 0
            if prompt:
                GEMINI_TOKENS.labels(direction='input').inc(prompt)
            if candidates:
                GEMINI_TOKENS.labels(direction='output').inc(candidates)
    except Exception:
        pass


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
