import re
import time
import logging

logger = logging.getLogger(__name__)

# Límites reales de Vertex AI para este proyecto (aiplatform.googleapis.com/
# online_prediction_requests_per_base_model — verificado con
# `gcloud alpha services quota list`). Vacío desde 2026-08-07 (migración
# Imagen 3 -> Gemini 3.1 Flash Image, HALLAZGO 90): las 2 entradas de Imagen 3
# ('imagen-3.0-generate'/'imagen-3.0-capability') quedaron sin uso al cambiar de
# modelo. gemini-3.1-flash-image no tiene límite fijo conocido (probable Dynamic
# Shared Quota, igual que gemini-2.5-flash) — agregar una entrada aquí solo si
# aparecen 429s reales en producción.
RPM_LIMITS = {}

RETRY_DELAYS = [10, 20, 40]


def _base_model(model_name: str) -> str:
    """'imagen-3.0-generate-001' -> 'imagen-3.0-generate' (la cuota es por base_model, sin versión)."""
    return re.sub(r'-\d+$', '', model_name)


def _redis():
    import django_rq
    return django_rq.get_connection('default')


def _minute_key(base_model: str) -> str:
    return f"vertex_rpm:{base_model}:{int(time.time() // 60)}"


def throttle(model_name: str) -> None:
    """Bloquea hasta que haya cupo en la ventana del minuto actual para este modelo,
    coordinando entre todos los procesos de rqworker vía Redis. No hace nada para
    modelos sin límite fijo conocido (ej. gemini-2.5-flash, DSQ)."""
    base_model = _base_model(model_name)
    limit = RPM_LIMITS.get(base_model)
    if limit is None:
        return
    conn = _redis()
    while True:
        key = _minute_key(base_model)
        count = conn.incr(key)
        if count == 1:
            conn.expire(key, 60)
        if count <= limit:
            return
        conn.decr(key)
        wait = max(0.5, 60 - (time.time() % 60))
        logger.warning(f"Rate limiter: {base_model} al límite ({limit}/min), esperando {wait:.1f}s")
        time.sleep(wait)


def diagnose_429(model_name: str) -> str:
    """Compara el conteo medido de peticiones de este minuto contra el límite
    conocido, para confirmar o descartar que un 429 se explica por nuestro
    propio rate limit medido (en vez de asumirlo)."""
    base_model = _base_model(model_name)
    limit = RPM_LIMITS.get(base_model)
    if limit is None:
        return f"{base_model} no tiene límite fijo conocido (posible DSQ compartido, no es nuestro rate limit)"
    count = int(_redis().get(_minute_key(base_model)) or 0)
    if count >= limit:
        return f"CONFIRMADO: {count} peticiones a {base_model} medidas este minuto (límite {limit}/min)"
    return f"{base_model}: solo {count}/{limit} peticiones medidas este minuto — el 429 no se explica por nuestro límite, revisar otra causa"


def call_with_429_retry(fn, model_name: str, max_retries: int = 3):
    """Ejecuta fn() con throttle preventivo y reintento con backoff si Vertex
    responde 429. Relanza la excepción si se agotan los reintentos."""
    for attempt in range(max_retries):
        throttle(model_name)
        try:
            return fn()
        except Exception as e:
            is_429 = '429' in str(e)
            if is_429 and attempt < max_retries - 1:
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                logger.warning(f"Rate limit en {model_name}, reintento {attempt + 1} en {delay}s — {diagnose_429(model_name)}")
                time.sleep(delay)
            else:
                if is_429:
                    logger.error(f"Rate limit persistente en {model_name} tras {attempt + 1} intento(s) — {diagnose_429(model_name)}")
                raise
