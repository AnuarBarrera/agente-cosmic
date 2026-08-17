import re
import time
import logging

logger = logging.getLogger(__name__)

# Límites reales de Vertex AI para este proyecto (aiplatform.googleapis.com/
# online_prediction_requests_per_base_model — verificado con
# `gcloud alpha services quota list`). Vacío desde 2026-08-07 hasta 2026-08-11
# (migración Imagen 3 -> Gemini 3.1 Flash Image, HALLAZGO 90): las 2 entradas de
# Imagen 3 ('imagen-3.0-generate'/'imagen-3.0-capability') quedaron sin uso al
# cambiar de modelo, y se asumió que gemini-3.1-flash-image usaba Dynamic Shared
# Quota sin límite fijo, igual que gemini-2.5-flash.
#
# Esa suposición resultó falsa: aparecieron 429s reales en producción
# (logs/django.log, 2026-08-07 y 2026-08-10, calendario real con imágenes/reel
# perdidos). 1/min es el techo sostenido que Anuar confirmó con soporte de
# Google el 2026-08-11 (una prueba manual llegó a 4 rpm pero cayó a 1 rpm en la
# 3a iteración — cuota real más baja de lo que sugiere el tier). No subir este
# número sin volver a probar en un pipeline real: el propio pipeline dispara
# ráfagas de 3 rqworkers en paralelo sin ningún otro control de concurrencia.
#
# 2026-08-14: el plan pagado migra la generación de imagen (posts + tomas de
# reel) a Gemini API directa (api_key, no Vertex) — decisión de Anuar para
# separar el gasto real de usuarios pagos de los créditos de GCP del trial
# gratis, que se queda en Vertex. Mismo modelo, dos superficies con techos muy
# distintos: 20 rpm confirmado empíricamente en Gemini API Tier 1 (limpio, sin
# un solo 429 en 21 peticiones/157s — no se buscó el techo real, ver
# project_gemini_image_rate_limit_2026_08_07.md), contra el 1 rpm de Vertex.
# RPM_LIMITS pasa a estar indexado por proveedor para que ambas superficies no
# compartan el mismo contador de Redis.
RPM_LIMITS = {
    'vertex': {
        'gemini-3.1-flash-image': 1,
        # gemini-3.1-flash-lite-image: valor conservador de partida (mismo que
        # el modelo normal) -- sin prueba empirica propia en Vertex todavia.
        # Uso admin/prueba unicamente (bajo volumen), el impacto de un limite
        # conservador es minimo. Ver docs/superpowers/specs/2026-08-15-product-photo-image-module-design.md.
        'gemini-3.1-flash-lite-image': 1,
    },
    'gemini_api': {
        'gemini-3.1-flash-image': 20,
        # Primera vez que este modelo se alcanza via Gemini API (antes solo
        # Vertex) -- valor de partida conservador (mismo que el modelo
        # normal), sin validacion empirica propia en esta superficie todavia.
        # Ver docs/superpowers/specs/2026-08-17-product-photo-pool-design.md.
        'gemini-3.1-flash-lite-image': 20,
    },
}

RETRY_DELAYS = [10, 20, 40]


def _base_model(model_name: str) -> str:
    """'imagen-3.0-generate-001' -> 'imagen-3.0-generate' (la cuota es por base_model, sin versión)."""
    return re.sub(r'-\d+$', '', model_name)


def _redis():
    import django_rq
    return django_rq.get_connection('default')


def _minute_key(base_model: str, provider: str) -> str:
    return f"{provider}_rpm:{base_model}:{int(time.time() // 60)}"


def throttle(model_name: str, provider: str = 'vertex') -> None:
    """Bloquea hasta que haya cupo en la ventana del minuto actual para este modelo
    en esta superficie (vertex/gemini_api), coordinando entre todos los procesos
    de rqworker vía Redis. No hace nada para modelos sin límite fijo conocido en
    esa superficie (ej. gemini-2.5-flash, DSQ)."""
    base_model = _base_model(model_name)
    limit = RPM_LIMITS.get(provider, {}).get(base_model)
    if limit is None:
        return
    conn = _redis()
    while True:
        key = _minute_key(base_model, provider)
        count = conn.incr(key)
        if count == 1:
            conn.expire(key, 60)
        if count <= limit:
            return
        conn.decr(key)
        wait = max(0.5, 60 - (time.time() % 60))
        logger.warning(f"Rate limiter: {provider}/{base_model} al límite ({limit}/min), esperando {wait:.1f}s")
        time.sleep(wait)


def diagnose_429(model_name: str, provider: str = 'vertex') -> str:
    """Compara el conteo medido de peticiones de este minuto contra el límite
    conocido, para confirmar o descartar que un 429 se explica por nuestro
    propio rate limit medido (en vez de asumirlo)."""
    base_model = _base_model(model_name)
    limit = RPM_LIMITS.get(provider, {}).get(base_model)
    if limit is None:
        return f"{provider}/{base_model} no tiene límite fijo conocido (posible DSQ compartido, no es nuestro rate limit)"
    count = int(_redis().get(_minute_key(base_model, provider)) or 0)
    if count >= limit:
        return f"CONFIRMADO: {count} peticiones a {provider}/{base_model} medidas este minuto (límite {limit}/min)"
    return f"{provider}/{base_model}: solo {count}/{limit} peticiones medidas este minuto — el 429 no se explica por nuestro límite, revisar otra causa"


def call_with_429_retry(fn, model_name: str, provider: str = 'vertex', max_retries: int = 3):
    """Ejecuta fn() con throttle preventivo y reintento con backoff si la API
    responde 429. Relanza la excepción si se agotan los reintentos."""
    for attempt in range(max_retries):
        throttle(model_name, provider)
        try:
            return fn()
        except Exception as e:
            is_429 = '429' in str(e)
            if is_429 and attempt < max_retries - 1:
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                logger.warning(f"Rate limit en {provider}/{model_name}, reintento {attempt + 1} en {delay}s — {diagnose_429(model_name, provider)}")
                time.sleep(delay)
            else:
                if is_429:
                    logger.error(f"Rate limit persistente en {provider}/{model_name} tras {attempt + 1} intento(s) — {diagnose_429(model_name, provider)}")
                raise
