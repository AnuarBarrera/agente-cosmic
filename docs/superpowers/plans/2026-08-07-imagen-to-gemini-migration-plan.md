# Migración Imagen 3/4 → Gemini 3.1 Flash Image — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrar los 2 sitios de generación de imagen del pipeline (`image_generator.py`, `reel_generator.py`) de Imagen 3 (`generate_images`, discontinuado por Google el 2026-08-17) a Gemini 3.1 Flash Image (`generate_content`), y limpiar la configuración/métricas/rate-limiting que quedan obsoletas.

**Architecture:** Ambos sitios migran a `client.models.generate_content(..., config=types.GenerateContentConfig(response_modalities=['IMAGE','TEXT'], image_config=types.ImageConfig(aspect_ratio=...)))`, leyendo `resp.candidates[0].content.parts[].inline_data.data`. `GOOGLE_CLOUD_LOCATION` pasa a `'global'` (único location donde estos modelos responden, confirmado con Veo/TTS también funcionando ahí sin riesgo). El negative prompt (que Gemini no soporta como parámetro estructurado) se dobla dentro del texto del prompt afirmativo en ambos sitios — decisión explícita de Anuar pese al riesgo histórico documentado en `reel_generator.py`, mitigado por el QC visual ya existente que rechaza+reintenta.

**Tech Stack:** Django, `google-genai` SDK (`google.genai.types.GenerateContentConfig`/`ImageConfig`), pytest, Redis (rate limiting), Prometheus (métricas).

## Global Constraints

- Modelo destino: `gemini-3.1-flash-image` (calidad completa) en los 2 sitios — no `gemini-3.1-flash-lite-image`.
- `GOOGLE_CLOUD_LOCATION` default: `'global'` (ya no `'us-central1'`) — afecta Veo/TTS/imagen por igual, confirmado sin riesgo con llamadas reales.
- `image_config=types.ImageConfig(aspect_ratio=...)` es el único mecanismo para forzar proporción en Gemini (`'1:1'` en `image_generator.py`, `'9:16'` en `reel_generator.py`).
- Gemini no tiene parámetro `negative_prompt` estructurado — el texto correspondiente se dobla dentro de `contents` en ambos sitios.
- Costo real de `gemini-3.1-flash-image`: $0.067/imagen a 1024×1024 estándar (fuente: documentación oficial de precios de Google) = 67000 microdólares.
- Métricas: el label `track_external_api('imagen3', ...)` se renombra a `'gemini_image'`. Los nombres de métrica de Prometheus `cosmic_imagen_generations_by_type_total`/`cosmic_imagen_cost_microdollars_total` (`core/shared/metrics.py`) **NO se tocan** — siguen leyendo las mismas llaves de Redis (`cosmic:prom:I:*`/`cosmic:prom:IC:*`) sin cambio.
- No hacer `git push` — commits locales en `main`, mismo patrón de esta sesión.
- Spec completo: `docs/superpowers/specs/2026-08-07-imagen-to-gemini-migration-design.md`.

---

### Task 1: Configuración, rate limiter y rename de métricas

**Files:**
- Modify: `saas_chatbot/settings.py:159,166,167`
- Modify: `core/shared/rate_limiter.py:11-14`
- Modify: `core/shared/tests/test_rate_limiter.py` (4 tests, líneas ~44-80)
- Modify: `core/shared/metrics_utils.py:24-38,112-116`
- Modify: `core/content_pipeline/generators/image_generator.py:17,800` (solo el import y ese call site — el resto de la función se reescribe en la Tarea 2)
- Modify: `core/content_pipeline/generators/reel_generator.py:24,706` (solo el import y ese call site — el resto de la función se reescribe en la Tarea 3)
- Modify: `core/content_pipeline/tests/test_reel_generator.py:467` (solo el `patch(...)` target, no el resto del test)

**Interfaces:**
- Produces: `core.shared.metrics_utils.record_gemini_image_generation(imagen_type: str = 'generate') -> None` (reemplaza a `record_imagen_generation`, misma firma, mismas llaves de Redis internas).
- Produces: `RPM_LIMITS` en `rate_limiter.py` queda vacío (`{}`) — Tareas 2 y 3 no necesitan agregar entradas nuevas.

Este task es puramente mecánico: renombrar un símbolo en su definición y en cada sitio que lo importa/llama, sin tocar la lógica de generación de imagen (eso es Tareas 2 y 3). El objetivo es que la suite completa siga en verde después de este task, aunque `image_generator.py`/`reel_generator.py` todavía usen `generate_images` (Imagen) internamente hasta las próximas 2 tareas.

- [ ] **Step 1: Cambiar `settings.py`**

En `saas_chatbot/settings.py`, reemplaza:

```python
GOOGLE_CLOUD_LOCATION = get_env('GOOGLE_CLOUD_LOCATION', default='us-central1')
GOOGLE_CLOUD_LOCATION_TEXT = get_env('GOOGLE_CLOUD_LOCATION_TEXT', default='global')
# Label adjunto a cada llamada a Vertex AI (labels= en Generate*Config) para poder
# separar costo de produccion vs desarrollo en el billing export de BigQuery.
# Produccion debe fijar GCP_REQUEST_ORIGIN=production en .env.prod.
GCP_REQUEST_ORIGIN = get_env('GCP_REQUEST_ORIGIN', default='development')
VERTEX_TEXT_MODEL = 'publishers/google/models/gemini-3.5-flash'
VERTEX_IMAGE_MODEL = 'imagen-3.0-generate-001'
VERTEX_IMAGE_EDIT_MODEL = 'imagen-3.0-capability-001'
VERTEX_VERTEX_MODEL = 'publishers/google/models/gemini-2.5-flash'
```

por:

```python
GOOGLE_CLOUD_LOCATION = get_env('GOOGLE_CLOUD_LOCATION', default='global')
GOOGLE_CLOUD_LOCATION_TEXT = get_env('GOOGLE_CLOUD_LOCATION_TEXT', default='global')
# Label adjunto a cada llamada a Vertex AI (labels= en Generate*Config) para poder
# separar costo de produccion vs desarrollo en el billing export de BigQuery.
# Produccion debe fijar GCP_REQUEST_ORIGIN=production en .env.prod.
GCP_REQUEST_ORIGIN = get_env('GCP_REQUEST_ORIGIN', default='development')
VERTEX_TEXT_MODEL = 'publishers/google/models/gemini-3.5-flash'
VERTEX_IMAGE_MODEL = 'gemini-3.1-flash-image'
VERTEX_VERTEX_MODEL = 'publishers/google/models/gemini-2.5-flash'
```

(`VERTEX_IMAGE_EDIT_MODEL` se elimina por completo — verificado con `grep -rn "VERTEX_IMAGE_EDIT_MODEL" --include="*.py" .` que no tiene ningún consumidor en el repo, resto muerto del pipeline BGSWAP ya eliminado.)

- [ ] **Step 2: Limpiar `rate_limiter.py`**

En `core/shared/rate_limiter.py`, reemplaza:

```python
# Límites reales de Vertex AI para este proyecto (aiplatform.googleapis.com/
# online_prediction_requests_per_base_model, region us-central1 — verificado con
# `gcloud alpha services quota list`). gemini-2.5-flash no aparece aquí: usa
# Dynamic Shared Quota (pool compartido de Google, sin límite fijo por proyecto).
RPM_LIMITS = {
    'imagen-3.0-generate': 20,      # subido de 1 -> 20 el 2026-07-06
    'imagen-3.0-capability': 10,
}
```

por:

```python
# Límites reales de Vertex AI para este proyecto (aiplatform.googleapis.com/
# online_prediction_requests_per_base_model — verificado con
# `gcloud alpha services quota list`). Vacío desde 2026-08-07 (migración
# Imagen 3 -> Gemini 3.1 Flash Image, HALLAZGO 90): las 2 entradas de Imagen 3
# ('imagen-3.0-generate'/'imagen-3.0-capability') quedaron sin uso al cambiar de
# modelo. gemini-3.1-flash-image no tiene límite fijo conocido (probable Dynamic
# Shared Quota, igual que gemini-2.5-flash) — agregar una entrada aquí solo si
# aparecen 429s reales en producción.
RPM_LIMITS = {}
```

- [ ] **Step 3: Reescribir los 4 tests de `test_rate_limiter.py` que dependían de una entrada real en `RPM_LIMITS`**

Estos 4 tests probaban el mecanismo de bloqueo/diagnóstico usando `'imagen-3.0-generate'` como ejemplo real — con `RPM_LIMITS` vacío dejan de tener un modelo con límite fijo para ejercitar esa ruta. Se reescriben para inyectar una entrada sintética vía `patch.dict`, desacoplando el test del nombre de modelo real (así no hay que tocarlos de nuevo la próxima vez que cambie un modelo).

En `core/shared/tests/test_rate_limiter.py`, reemplaza estas 4 funciones completas:

```python
def test_throttle_allows_calls_within_limit(fake_redis):
    for _ in range(20):
        rate_limiter.throttle('imagen-3.0-generate-001')
    key = rate_limiter._minute_key('imagen-3.0-generate')
    assert fake_redis.store[key] == 20


@patch('core.shared.rate_limiter.time.sleep')
def test_throttle_waits_when_over_limit(mock_sleep, fake_redis):
    key = rate_limiter._minute_key('imagen-3.0-generate')
    for _ in range(20):
        rate_limiter.throttle('imagen-3.0-generate-001')
    mock_sleep.assert_not_called()

    # Simula que la ventana del minuto expiró mientras "esperábamos" — evita
    # un loop infinito en el test, ya que time.sleep está mockeado (no avanza el reloj real).
    mock_sleep.side_effect = lambda *a, **k: fake_redis.store.pop(key, None)

    # La petición 21 excede el límite de 20/min — debe esperar antes de continuar
    rate_limiter.throttle('imagen-3.0-generate-001')
    mock_sleep.assert_called_once()
    assert fake_redis.store[key] == 1  # la ventana se reinició, este es el primer conteo


def test_diagnose_429_confirms_when_over_limit(fake_redis):
    key = rate_limiter._minute_key('imagen-3.0-generate')
    fake_redis.store[key] = 20
    msg = rate_limiter.diagnose_429('imagen-3.0-generate-001')
    assert 'CONFIRMADO' in msg


def test_diagnose_429_rules_out_when_under_limit(fake_redis):
    key = rate_limiter._minute_key('imagen-3.0-generate')
    fake_redis.store[key] = 3
    msg = rate_limiter.diagnose_429('imagen-3.0-generate-001')
    assert 'CONFIRMADO' not in msg
    assert 'no se explica' in msg
```

por:

```python
def test_throttle_allows_calls_within_limit(fake_redis):
    with patch.dict(rate_limiter.RPM_LIMITS, {'test-model-generate': 20}):
        for _ in range(20):
            rate_limiter.throttle('test-model-generate-001')
        key = rate_limiter._minute_key('test-model-generate')
        assert fake_redis.store[key] == 20


@patch('core.shared.rate_limiter.time.sleep')
def test_throttle_waits_when_over_limit(mock_sleep, fake_redis):
    with patch.dict(rate_limiter.RPM_LIMITS, {'test-model-generate': 20}):
        key = rate_limiter._minute_key('test-model-generate')
        for _ in range(20):
            rate_limiter.throttle('test-model-generate-001')
        mock_sleep.assert_not_called()

        # Simula que la ventana del minuto expiró mientras "esperábamos" — evita
        # un loop infinito en el test, ya que time.sleep está mockeado (no avanza el reloj real).
        mock_sleep.side_effect = lambda *a, **k: fake_redis.store.pop(key, None)

        # La petición 21 excede el límite de 20/min — debe esperar antes de continuar
        rate_limiter.throttle('test-model-generate-001')
        mock_sleep.assert_called_once()
        assert fake_redis.store[key] == 1  # la ventana se reinició, este es el primer conteo


def test_diagnose_429_confirms_when_over_limit(fake_redis):
    with patch.dict(rate_limiter.RPM_LIMITS, {'test-model-generate': 20}):
        key = rate_limiter._minute_key('test-model-generate')
        fake_redis.store[key] = 20
        msg = rate_limiter.diagnose_429('test-model-generate-001')
        assert 'CONFIRMADO' in msg


def test_diagnose_429_rules_out_when_under_limit(fake_redis):
    with patch.dict(rate_limiter.RPM_LIMITS, {'test-model-generate': 20}):
        key = rate_limiter._minute_key('test-model-generate')
        fake_redis.store[key] = 3
        msg = rate_limiter.diagnose_429('test-model-generate-001')
        assert 'CONFIRMADO' not in msg
        assert 'no se explica' in msg
```

(`test_base_model_strips_version_suffix` y `test_throttle_noop_for_model_without_known_limit`/`test_diagnose_429_unknown_model_reports_dsq` no cambian — el primero prueba el regex de `_base_model` con cualquier string, el segundo y tercero ya prueban el caso "sin límite conocido", que sigue existiendo.)

- [ ] **Step 4: Ejecutar los tests de `rate_limiter.py`**

Run: `docker compose exec -T backend pytest core/shared/tests/test_rate_limiter.py -v`
Expected: 9 tests PASS (los 4 reescritos + los 5 que no cambiaron).

- [ ] **Step 5: Renombrar `record_imagen_generation` en `metrics_utils.py`**

En `core/shared/metrics_utils.py`, reemplaza:

```python
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
```

por:

```python
# ---------------------------------------------------------------------------
# Precios (microdólares para evitar floats en contadores)
# Gemini 2.5 Flash: $0.075/1M input tokens, $0.30/1M output tokens
# Gemini 3.1 Flash Image (1024x1024 estándar): $0.067/imagen — fuente:
#   documentación oficial de precios de Google (ai.google.dev/gemini-api/docs/pricing,
#   confirmada 2026-08-07). NO usar la tarifa de tokens de texto de arriba para
#   imágenes — los tokens de imagen de salida se facturan a una tarifa muy
#   distinta (1120 tokens = $0.067 ⇒ ≈$59.8/1M equivalente), usar el precio
#   plano por imagen de abajo.
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
_GEMINI_IMAGE_COST_PER_IMAGE = 67000    # $0.067 = 67,000 microdólares
```

Y reemplaza:

```python
def record_imagen_generation(imagen_type: str = 'generate'):
    """Registra una generación de Imagen 3 con su costo estimado."""
    _redis_inc(f'cosmic:prom:I:{imagen_type}')
    _redis_inc(f'cosmic:prom:IC:{imagen_type}', _IMAGEN_COST_PER_IMAGE)
```

por:

```python
def record_gemini_image_generation(imagen_type: str = 'generate'):
    """Registra una generación de imagen (Gemini 3.1 Flash Image) con su costo estimado.
    Mismas llaves de Redis que antes (cosmic:prom:I:*/IC:*) — core/shared/metrics.py
    las sigue leyendo tal cual, el nombre de métrica de Prometheus expuesto
    (cosmic_imagen_generations_by_type_total) no cambia (decisión de Anuar,
    2026-08-07: es el nombre real de panel, más disruptivo de renombrar que este
    label interno)."""
    _redis_inc(f'cosmic:prom:I:{imagen_type}')
    _redis_inc(f'cosmic:prom:IC:{imagen_type}', _GEMINI_IMAGE_COST_PER_IMAGE)
```

- [ ] **Step 6: Actualizar el import y el call site en `image_generator.py`**

En `core/content_pipeline/generators/image_generator.py:17`, reemplaza:

```python
from core.shared.metrics_utils import track_external_api, record_tokens, record_imagen_generation, vertex_labels
```

por:

```python
from core.shared.metrics_utils import track_external_api, record_tokens, record_gemini_image_generation, vertex_labels
```

Y en la línea ~800 (dentro de `_generate_with_vertex`, en la rama `if 'imagen' in model:` que la Tarea 2 va a eliminar por completo — este es solo un rename mecánico para que el archivo siga siendo válido mientras tanto):

```python
            if resp.generated_images:
                record_imagen_generation('generate')
                return resp.generated_images[0].image.image_bytes
```

por:

```python
            if resp.generated_images:
                record_gemini_image_generation('generate')
                return resp.generated_images[0].image.image_bytes
```

- [ ] **Step 7: Actualizar el import y el call site en `reel_generator.py`**

En `core/content_pipeline/generators/reel_generator.py`, reemplaza el bloque de import (líneas 21-27):

```python
from core.shared.metrics_utils import (
    track_external_api, record_tokens, record_veo_generation,
    record_lyria_generation, record_tts_generation,
    record_playwright_overlay_fallback, record_imagen_generation,
    record_hyperframes_generation, record_hyperframes_fallback,
    vertex_labels,
)
```

por:

```python
from core.shared.metrics_utils import (
    track_external_api, record_tokens, record_veo_generation,
    record_lyria_generation, record_tts_generation,
    record_playwright_overlay_fallback, record_gemini_image_generation,
    record_hyperframes_generation, record_hyperframes_fallback,
    vertex_labels,
)
```

Y en `_generate_scene_still` (línea ~706, dentro del código que la Tarea 3 reescribe por completo — mismo rename mecánico de paso):

```python
        if resp.generated_images:
            record_imagen_generation('reel_scene')
            return resp.generated_images[0].image.image_bytes
```

por:

```python
        if resp.generated_images:
            record_gemini_image_generation('reel_scene')
            return resp.generated_images[0].image.image_bytes
```

- [ ] **Step 8: Actualizar el `patch(...)` target en `test_reel_generator.py`**

En `core/content_pipeline/tests/test_reel_generator.py:467`, reemplaza:

```python
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc, \
             patch('core.content_pipeline.generators.reel_generator.record_imagen_generation') as mock_record:
```

por:

```python
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc, \
             patch('core.content_pipeline.generators.reel_generator.record_gemini_image_generation') as mock_record:
```

(El resto de ese test — que todavía asume `generate_images`/Imagen — se reescribe por completo en la Tarea 3. Este paso solo evita que el import roto tumbe la clase entera antes de llegar ahí.)

- [ ] **Step 9: Correr la suite completa para confirmar que sigue en verde**

Run: `docker compose exec -T backend pytest core/shared/ core/content_pipeline/tests/test_image_generator.py core/content_pipeline/tests/test_reel_generator.py -v 2>&1 | tail -40`
Expected: todos los tests PASS. La app entera sigue usando Imagen 3 internamente (Tareas 2/3 pendientes) — este task solo renombra símbolos y limpia configuración, no cambia comportamiento de generación de imagen todavía.

- [ ] **Step 10: Commit**

```bash
git add saas_chatbot/settings.py core/shared/rate_limiter.py core/shared/tests/test_rate_limiter.py core/shared/metrics_utils.py core/content_pipeline/generators/image_generator.py core/content_pipeline/generators/reel_generator.py core/content_pipeline/tests/test_reel_generator.py
GIT_EDITOR=true git commit -m "$(cat <<'EOF'
refactor: migra config/metricas a Gemini 3.1 Flash Image (Imagen 3 discontinuado 2026-08-17)

GOOGLE_CLOUD_LOCATION -> global, VERTEX_IMAGE_MODEL -> gemini-3.1-flash-image,
elimina VERTEX_IMAGE_EDIT_MODEL (config muerta). Limpia RPM_LIMITS de Imagen 3
(vacio, gemini-3.1-flash-image sin limite fijo conocido). Renombra
record_imagen_generation -> record_gemini_image_generation y actualiza costo
real (_GEMINI_IMAGE_COST_PER_IMAGE = 67000, antes 40000 de Imagen 3). Los
nombres de metrica de Prometheus en metrics.py no cambian (decision explicita).

Solo config/metricas -- la logica de generate_images -> generate_content en
image_generator.py y reel_generator.py se hace en tareas siguientes.
EOF
)"
```

---

### Task 2: Migrar `image_generator.py` a Gemini 3.1 Flash Image

**Files:**
- Modify: `core/content_pipeline/generators/image_generator.py:784-816` (`_generate_with_vertex`)
- Modify: `core/content_pipeline/tests/test_image_generator.py:36-52` (`test_generate_with_vertex_passes_negative_prompt_for_imagen`)

**Interfaces:**
- Consumes: `record_gemini_image_generation` (Tarea 1), `_IMAGE_NEGATIVE_PROMPT` (ya existe, `image_generator.py:33-37`), `types.ImageConfig` (de `google.genai.types`, ya importado en el archivo como `types`).
- Produces: `_generate_with_vertex(self, prompt: str) -> bytes` con la misma firma pública, ahora usando exclusivamente `generate_content`.

- [ ] **Step 1: Reemplazar `_generate_with_vertex` completo**

En `core/content_pipeline/generators/image_generator.py`, reemplaza el método completo (líneas 784-816):

```python
    def _generate_with_vertex(self, prompt: str) -> bytes:
        client = _vertex_client()
        model = settings.VERTEX_IMAGE_MODEL
        if 'imagen' in model:
            with track_external_api('imagen3', operation='image_generate'):
                resp = client.models.generate_images(
                    model=model,
                    prompt=prompt,
                    config=types.GenerateImagesConfig(
                        number_of_images=1,
                        aspect_ratio='1:1',
                        negative_prompt=_IMAGE_NEGATIVE_PROMPT,
                        labels=vertex_labels(),
                    ),
                )
            if resp.generated_images:
                record_gemini_image_generation('generate')
                return resp.generated_images[0].image.image_bytes
            raise ValueError("No image returned by Imagen")
        with track_external_api('gemini'):
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=['IMAGE', 'TEXT'],
                    labels=vertex_labels(),
                ),
            )
        record_tokens(resp)
        for part in resp.candidates[0].content.parts:
            if part.inline_data:
                return part.inline_data.data
        raise ValueError("No image returned by Vertex AI")
```

por:

```python
    def _generate_with_vertex(self, prompt: str) -> bytes:
        client = _vertex_client()
        model = settings.VERTEX_IMAGE_MODEL
        # Gemini no tiene parametro estructurado de negative_prompt (a diferencia de
        # Imagen 3.0) -- se dobla el texto dentro del prompt afirmativo. Verificado con
        # llamada real (2026-08-07): 2 generaciones del mismo prompt, con y sin este
        # texto doblado, ninguna mostro iconos/texto/logos alucinados. El QC posterior
        # (_validate_background) sigue como red de seguridad independiente de esto.
        full_prompt = f"{prompt}\n\nAvoid: {_IMAGE_NEGATIVE_PROMPT}"
        with track_external_api('gemini_image', operation='image_generate'):
            resp = client.models.generate_content(
                model=model,
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    response_modalities=['IMAGE', 'TEXT'],
                    image_config=types.ImageConfig(aspect_ratio='1:1'),
                    labels=vertex_labels(),
                ),
            )
        for part in resp.candidates[0].content.parts:
            if part.inline_data:
                record_gemini_image_generation('generate')
                return part.inline_data.data
        raise ValueError("No image returned by Vertex AI")
```

Nota: `record_tokens(resp)` se elimina de esta ruta a propósito — factura a tarifa de texto
($0.30/1M tokens), que no representa el costo real de tokens de imagen (~$59.8/1M
equivalente). `record_gemini_image_generation('generate')` (costo plano, $0.067/imagen) es
la métrica correcta aquí.

- [ ] **Step 2: Reescribir el test que cubre esta función**

En `core/content_pipeline/tests/test_image_generator.py`, reemplaza (líneas 36-52):

```python
@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
)
def test_generate_with_vertex_passes_negative_prompt_for_imagen():
    from core.content_pipeline.generators.image_generator import ImageGenerator, _IMAGE_NEGATIVE_PROMPT
    gen = ImageGenerator(bucket_name='test-bucket')
    mock_client = MagicMock()
    mock_client.models.generate_images.return_value = MagicMock(
        generated_images=[MagicMock(image=MagicMock(image_bytes=b'fake-png'))]
    )
    with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_client):
        gen._generate_with_vertex('a test prompt')

    call_kwargs = mock_client.models.generate_images.call_args.kwargs
    assert call_kwargs['config'].negative_prompt == _IMAGE_NEGATIVE_PROMPT
```

por:

```python
@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='global',
    VERTEX_IMAGE_MODEL='gemini-3.1-flash-image',
)
def test_generate_with_vertex_includes_negative_prompt_text_and_forces_square():
    from core.content_pipeline.generators.image_generator import ImageGenerator, _IMAGE_NEGATIVE_PROMPT
    gen = ImageGenerator(bucket_name='test-bucket')
    mock_client = MagicMock()
    mock_part = MagicMock()
    mock_part.inline_data.data = b'fake-png'
    mock_client.models.generate_content.return_value = MagicMock(
        candidates=[MagicMock(content=MagicMock(parts=[mock_part]))]
    )
    with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_client):
        result = gen._generate_with_vertex('a test prompt')

    assert result == b'fake-png'
    call_kwargs = mock_client.models.generate_content.call_args.kwargs
    # Gemini no tiene negative_prompt estructurado -- se dobla en el texto (decision
    # 2026-08-07, ver spec de migracion).
    assert _IMAGE_NEGATIVE_PROMPT in call_kwargs['contents']
    assert call_kwargs['config'].image_config.aspect_ratio == '1:1'


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='global',
    VERTEX_IMAGE_MODEL='gemini-3.1-flash-image',
)
def test_generate_with_vertex_records_image_cost_not_token_cost():
    from core.content_pipeline.generators.image_generator import ImageGenerator
    gen = ImageGenerator(bucket_name='test-bucket')
    mock_client = MagicMock()
    mock_part = MagicMock()
    mock_part.inline_data.data = b'fake-png'
    mock_client.models.generate_content.return_value = MagicMock(
        candidates=[MagicMock(content=MagicMock(parts=[mock_part]))]
    )
    with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_client), \
         patch('core.content_pipeline.generators.image_generator.record_gemini_image_generation') as mock_record, \
         patch('core.content_pipeline.generators.image_generator.record_tokens') as mock_tokens:
        gen._generate_with_vertex('a test prompt')

    mock_record.assert_called_once_with('generate')
    mock_tokens.assert_not_called()
```

- [ ] **Step 3: Correr los tests de este archivo**

Run: `docker compose exec -T backend pytest core/content_pipeline/tests/test_image_generator.py -v 2>&1 | tail -40`
Expected: todos PASS, incluyendo los 2 tests nuevos/reescritos de este paso.

- [ ] **Step 4: Commit**

```bash
git add core/content_pipeline/generators/image_generator.py core/content_pipeline/tests/test_image_generator.py
GIT_EDITOR=true git commit -m "$(cat <<'EOF'
feat: migra image_generator.py de Imagen 3 a Gemini 3.1 Flash Image

_generate_with_vertex usa generate_content (unica ruta ahora, sin la rama
Imagen). Fuerza 1:1 con image_config.aspect_ratio, dobla el negative prompt
en el texto (Gemini no tiene parametro estructurado), y registra costo real
por imagen en vez de la tarifa de tokens de texto (incorrecta para imagenes).
EOF
)"
```

---

### Task 3: Migrar `reel_generator.py:_generate_scene_still` a Gemini 3.1 Flash Image

**Files:**
- Modify: `core/content_pipeline/generators/reel_generator.py:691-718` (`_generate_scene_still`)
- Modify: `core/content_pipeline/tests/test_reel_generator.py:452-509` (`TestGenerateSceneStill`, 3 tests)

**Interfaces:**
- Consumes: `record_gemini_image_generation` (Tarea 1, ya importado), `self._VEO_SAFE_CONSTRAINTS` (ya existe, `reel_generator.py:384-397`), `types.ImageConfig`.
- Produces: `_generate_scene_still(self, prompt: str) -> bytes | None` con la misma firma pública.

- [ ] **Step 1: Reemplazar `_generate_scene_still` completo**

En `core/content_pipeline/generators/reel_generator.py`, reemplaza el método completo (líneas 691-718):

```python
    def _generate_scene_still(self, prompt: str) -> bytes | None:
        try:
            client = _vertex_client()
            with track_external_api('imagen3', operation='image_generate'):
                resp = client.models.generate_images(
                    model=settings.VERTEX_IMAGE_MODEL,
                    prompt=prompt,
                    config=types.GenerateImagesConfig(
                        number_of_images=1,
                        aspect_ratio='9:16',
                        negative_prompt=self._VEO_SAFE_CONSTRAINTS.strip(),
                        labels=vertex_labels(),
                    ),
                )
            if resp.generated_images:
                record_gemini_image_generation('reel_scene')
                return resp.generated_images[0].image.image_bytes
            # Motivo tipico: filtro de seguridad de Imagen bloqueo la generacion
            # (prompt rechazado) sin lanzar excepcion — solo devuelve la lista vacia.
            filter_reason = getattr(resp, 'positive_prompt_safety_attributes', None)
            logger.warning(
                f"Imagen scene: 0 imagenes generadas (posible filtro de seguridad) | "
                f"filter_reason={filter_reason} | prompt={prompt[:80]}"
            )
            return None
        except Exception as e:
            logger.warning(f"Imagen scene generation failed: {e}")
            return None
```

por:

```python
    def _generate_scene_still(self, prompt: str) -> bytes | None:
        try:
            client = _vertex_client()
            # Gemini no tiene parametro estructurado de negative_prompt -- se dobla
            # _VEO_SAFE_CONSTRAINTS en el texto afirmativo. Decision explicita de Anuar
            # (2026-08-07, ver spec de migracion) pese al riesgo historico documentado
            # arriba en la clase (icono de boton de play alucinado con Imagen pese a
            # prohibirlo en el prompt) -- verificado con llamada real de control sin
            # evidencia de que el problema se traslade a Gemini, y el QC posterior
            # (_validate_scene_still, mas abajo) ya rechaza+reintenta si de todos modos
            # aparecen iconos/UI/logos (has_screen_content).
            full_prompt = f"{prompt}\n\n{self._VEO_SAFE_CONSTRAINTS.strip()}"
            with track_external_api('gemini_image', operation='image_generate'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_IMAGE_MODEL,
                    contents=full_prompt,
                    config=types.GenerateContentConfig(
                        response_modalities=['IMAGE', 'TEXT'],
                        image_config=types.ImageConfig(aspect_ratio='9:16'),
                        labels=vertex_labels(),
                    ),
                )
            for part in resp.candidates[0].content.parts:
                if part.inline_data:
                    record_gemini_image_generation('reel_scene')
                    return part.inline_data.data
            # Motivo tipico: filtro de seguridad de Gemini bloqueo la generacion
            # (prompt rechazado) sin lanzar excepcion — solo devuelve partes sin imagen.
            logger.warning(
                f"Imagen scene: 0 imagenes generadas (posible filtro de seguridad) | "
                f"prompt={prompt[:80]}"
            )
            return None
        except Exception as e:
            logger.warning(f"Imagen scene generation failed: {e}")
            return None
```

- [ ] **Step 2: Reescribir los 3 tests de `TestGenerateSceneStill`**

En `core/content_pipeline/tests/test_reel_generator.py`, reemplaza la clase completa (líneas 452-509):

```python
class TestGenerateSceneStill:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
    )
    def test_returns_image_bytes_on_success(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_image = b'fake-image-bytes'
        mock_generated = MagicMock()
        mock_generated.image.image_bytes = fake_image
        mock_resp = MagicMock()
        mock_resp.generated_images = [mock_generated]
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc, \
             patch('core.content_pipeline.generators.reel_generator.record_gemini_image_generation') as mock_record:
            mock_vc.return_value.models.generate_images.return_value = mock_resp
            result = gen._generate_scene_still('a workshop scene')
        assert result == fake_image
        mock_record.assert_called_once_with('reel_scene')
        call_kwargs = mock_vc.return_value.models.generate_images.call_args.kwargs
        assert call_kwargs['model'] == 'imagen-3.0-generate-001'
        assert call_kwargs['config'].aspect_ratio == '9:16'
        # negative_prompt via el parametro dedicado de la API, NO concatenado al
        # prompt afirmativo (mencionar "icons"/"UI elements" en el prompt
        # principal, aunque sea para negarlos, puede hacer que Imagen los genere
        # de todos modos — alucinacion real: icono de boton de play incrustado).
        assert call_kwargs['prompt'] == 'a workshop scene'
        assert call_kwargs['config'].negative_prompt == gen._VEO_SAFE_CONSTRAINTS.strip()
        assert 'NO icons' not in call_kwargs['prompt']

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
    )
    def test_returns_none_on_api_error(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_images.side_effect = Exception('rejected')
            result = gen._generate_scene_still('a workshop scene')
        assert result is None

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
    )
    def test_returns_none_when_no_images_generated(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_resp = MagicMock()
        mock_resp.generated_images = []
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_images.return_value = mock_resp
            result = gen._generate_scene_still('a workshop scene')
        assert result is None
```

por:

```python
class TestGenerateSceneStill:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='global',
        VERTEX_IMAGE_MODEL='gemini-3.1-flash-image',
    )
    def test_returns_image_bytes_on_success(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_image = b'fake-image-bytes'
        mock_part = MagicMock()
        mock_part.inline_data.data = fake_image
        mock_resp = MagicMock(candidates=[MagicMock(content=MagicMock(parts=[mock_part]))])
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc, \
             patch('core.content_pipeline.generators.reel_generator.record_gemini_image_generation') as mock_record:
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._generate_scene_still('a workshop scene')
        assert result == fake_image
        mock_record.assert_called_once_with('reel_scene')
        call_kwargs = mock_vc.return_value.models.generate_content.call_args.kwargs
        assert call_kwargs['model'] == 'gemini-3.1-flash-image'
        assert call_kwargs['config'].image_config.aspect_ratio == '9:16'
        # Decision 2026-08-07 (ver spec de migracion): a diferencia de Imagen, Gemini
        # no tiene negative_prompt estructurado -- aqui SI se dobla el texto de
        # _VEO_SAFE_CONSTRAINTS en el prompt afirmativo (a proposito, pese al riesgo
        # historico documentado arriba en la clase), mitigado por el QC posterior.
        assert gen._VEO_SAFE_CONSTRAINTS.strip() in call_kwargs['contents']

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='global',
        VERTEX_IMAGE_MODEL='gemini-3.1-flash-image',
    )
    def test_returns_none_on_api_error(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('rejected')
            result = gen._generate_scene_still('a workshop scene')
        assert result is None

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='global',
        VERTEX_IMAGE_MODEL='gemini-3.1-flash-image',
    )
    def test_returns_none_when_no_image_part_in_response(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_part = MagicMock()
        mock_part.inline_data = None
        mock_resp = MagicMock(candidates=[MagicMock(content=MagicMock(parts=[mock_part]))])
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._generate_scene_still('a workshop scene')
        assert result is None
```

- [ ] **Step 3: Correr los tests de este archivo**

Run: `docker compose exec -T backend pytest core/content_pipeline/tests/test_reel_generator.py -v 2>&1 | tail -60`
Expected: todos PASS, incluyendo los 3 tests reescritos de `TestGenerateSceneStill` y `TestGenerateStillScenes`/`TestGenerateVideoOrImageScenes` (si existen, no deberían verse afectados — mockean `_generate_scene_still` directamente, no su implementación interna).

- [ ] **Step 4: Commit**

```bash
git add core/content_pipeline/generators/reel_generator.py core/content_pipeline/tests/test_reel_generator.py
GIT_EDITOR=true git commit -m "$(cat <<'EOF'
feat: migra reel_generator.py:_generate_scene_still a Gemini 3.1 Flash Image

generate_content en vez de generate_images, image_config.aspect_ratio='9:16'
en vez de GenerateImagesConfig, negative prompt doblado en el texto (decision
explicita pese al riesgo historico documentado en la clase -- mitigado por el
QC existente _validate_scene_still).
EOF
)"
```

---

### Task 4: Simplificar el cliente de Lyria en `_generate_music_attempt`

**Files:**
- Modify: `core/content_pipeline/generators/reel_generator.py:812-822`
- Modify: `core/content_pipeline/tests/test_reel_generator.py:897-986` (`TestGenerateMusic`, 4 tests)

**Interfaces:**
- Consumes: `_vertex_client()` (ya existe en el archivo, ahora apunta a `location='global'` desde la Tarea 1).

- [ ] **Step 1: Simplificar `_generate_music_attempt`**

En `core/content_pipeline/generators/reel_generator.py`, reemplaza (dentro de `_generate_music_attempt`):

```python
        try:
            # Lyria 3 solo esta disponible en la ubicacion 'global' de Vertex AI (no
            # en una region como us-central1) y rechaza la peticion si se especifica
            # response_modalities/response_format explicito — el modelo devuelve
            # audio implicitamente, sin necesidad de pedirlo.
            client = genai.Client(
                vertexai=True,
                project=settings.GOOGLE_CLOUD_PROJECT,
                location='global',
            )
```

por:

```python
        try:
            # Lyria 3 solo esta disponible en la ubicacion 'global' de Vertex AI y
            # rechaza la peticion si se especifica response_modalities/response_format
            # explicito — el modelo devuelve audio implicitamente, sin necesidad de
            # pedirlo. GOOGLE_CLOUD_LOCATION ya apunta a 'global' desde la migracion
            # Imagen -> Gemini 3.1 Flash Image (2026-08-07), asi que _vertex_client()
            # sirve igual sin necesidad de un cliente dedicado.
            client = _vertex_client()
```

- [ ] **Step 2: Reescribir los 4 tests de `TestGenerateMusic`**

En `core/content_pipeline/tests/test_reel_generator.py`, reemplaza la clase completa (líneas 897-986):

```python
class TestGenerateMusic:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_MUSIC_MODEL='lyria-3-clip-preview',
    )
    def test_returns_audio_bytes_on_success(self):
        import base64
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_audio = MagicMock()
        mock_audio.data = base64.b64encode(b'fake-music-bytes').decode()
        mock_interaction = MagicMock()
        mock_interaction.output_audio = mock_audio
        with patch('core.content_pipeline.generators.reel_generator.genai.Client') as mock_client_cls:
            mock_client_cls.return_value.interactions.create.return_value = mock_interaction
            result = gen._generate_music('upbeat corporate, optimistic')
        assert result == b'fake-music-bytes'
        mock_client_cls.assert_called_once_with(
            vertexai=True, project='agente-cosmic', location='global',
        )
        call_kwargs = mock_client_cls.return_value.interactions.create.call_args.kwargs
        assert 'response_modalities' not in call_kwargs
        assert 'response_format' not in call_kwargs

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_MUSIC_MODEL='lyria-3-clip-preview',
    )
    def test_retries_once_and_succeeds_on_second_attempt(self):
        # El filtro de contenido de Lyria 3 Clip (preview) es no-determinista —
        # confirmado en produccion reintentando el mismo prompt sin cambios.
        import base64
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_audio = MagicMock()
        mock_audio.data = base64.b64encode(b'fake-music-bytes').decode()
        mock_interaction = MagicMock()
        mock_interaction.output_audio = mock_audio
        with patch('core.content_pipeline.generators.reel_generator.genai.Client') as mock_client_cls:
            mock_client_cls.return_value.interactions.create.side_effect = [
                Exception('content_blocked'), mock_interaction,
            ]
            result = gen._generate_music('upbeat corporate, optimistic')
        assert result == b'fake-music-bytes'
        assert mock_client_cls.return_value.interactions.create.call_count == 2

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_MUSIC_MODEL='lyria-3-clip-preview',
    )
    def test_returns_none_on_api_error(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator.genai.Client') as mock_client_cls:
            mock_client_cls.return_value.interactions.create.side_effect = Exception('error')
            result = gen._generate_music('upbeat')
        assert result is None
        assert mock_client_cls.return_value.interactions.create.call_count == 3

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_MUSIC_MODEL='lyria-3-clip-preview',
    )
    def test_falls_back_to_generic_prompt_on_third_attempt(self):
        # Si el mood del guion falla 2 veces (posible bloqueo del filtro de
        # contenido), el 3er intento usa un prompt generico "corporate stock
        # music" que no depende del guion, para no perder la musica del todo.
        import base64
        from core.content_pipeline.generators.reel_generator import (
            ReelGenerator, _MUSIC_FALLBACK_PROMPT,
        )
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_audio = MagicMock()
        mock_audio.data = base64.b64encode(b'fallback-music-bytes').decode()
        mock_interaction = MagicMock()
        mock_interaction.output_audio = mock_audio
        with patch('core.content_pipeline.generators.reel_generator.genai.Client') as mock_client_cls:
            mock_client_cls.return_value.interactions.create.side_effect = [
                Exception('content_blocked'), Exception('content_blocked'), mock_interaction,
            ]
            result = gen._generate_music('upbeat corporate, optimistic')
        assert result == b'fallback-music-bytes'
        assert mock_client_cls.return_value.interactions.create.call_count == 3
        third_call_kwargs = mock_client_cls.return_value.interactions.create.call_args_list[2].kwargs
        assert third_call_kwargs['input'] == _MUSIC_FALLBACK_PROMPT
```

por:

```python
class TestGenerateMusic:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='global',
        VERTEX_MUSIC_MODEL='lyria-3-clip-preview',
    )
    def test_returns_audio_bytes_on_success(self):
        import base64
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_audio = MagicMock()
        mock_audio.data = base64.b64encode(b'fake-music-bytes').decode()
        mock_interaction = MagicMock()
        mock_interaction.output_audio = mock_audio
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.interactions.create.return_value = mock_interaction
            result = gen._generate_music('upbeat corporate, optimistic')
        assert result == b'fake-music-bytes'
        call_kwargs = mock_vc.return_value.interactions.create.call_args.kwargs
        assert 'response_modalities' not in call_kwargs
        assert 'response_format' not in call_kwargs

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='global',
        VERTEX_MUSIC_MODEL='lyria-3-clip-preview',
    )
    def test_retries_once_and_succeeds_on_second_attempt(self):
        # El filtro de contenido de Lyria 3 Clip (preview) es no-determinista —
        # confirmado en produccion reintentando el mismo prompt sin cambios.
        import base64
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_audio = MagicMock()
        mock_audio.data = base64.b64encode(b'fake-music-bytes').decode()
        mock_interaction = MagicMock()
        mock_interaction.output_audio = mock_audio
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.interactions.create.side_effect = [
                Exception('content_blocked'), mock_interaction,
            ]
            result = gen._generate_music('upbeat corporate, optimistic')
        assert result == b'fake-music-bytes'
        assert mock_vc.return_value.interactions.create.call_count == 2

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='global',
        VERTEX_MUSIC_MODEL='lyria-3-clip-preview',
    )
    def test_returns_none_on_api_error(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.interactions.create.side_effect = Exception('error')
            result = gen._generate_music('upbeat')
        assert result is None
        assert mock_vc.return_value.interactions.create.call_count == 3

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='global',
        VERTEX_MUSIC_MODEL='lyria-3-clip-preview',
    )
    def test_falls_back_to_generic_prompt_on_third_attempt(self):
        # Si el mood del guion falla 2 veces (posible bloqueo del filtro de
        # contenido), el 3er intento usa un prompt generico "corporate stock
        # music" que no depende del guion, para no perder la musica del todo.
        import base64
        from core.content_pipeline.generators.reel_generator import (
            ReelGenerator, _MUSIC_FALLBACK_PROMPT,
        )
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_audio = MagicMock()
        mock_audio.data = base64.b64encode(b'fallback-music-bytes').decode()
        mock_interaction = MagicMock()
        mock_interaction.output_audio = mock_audio
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.interactions.create.side_effect = [
                Exception('content_blocked'), Exception('content_blocked'), mock_interaction,
            ]
            result = gen._generate_music('upbeat corporate, optimistic')
        assert result == b'fallback-music-bytes'
        assert mock_vc.return_value.interactions.create.call_count == 3
        third_call_kwargs = mock_vc.return_value.interactions.create.call_args_list[2].kwargs
        assert third_call_kwargs['input'] == _MUSIC_FALLBACK_PROMPT
```

- [ ] **Step 3: Correr los tests de este archivo**

Run: `docker compose exec -T backend pytest core/content_pipeline/tests/test_reel_generator.py -v 2>&1 | tail -60`
Expected: todos PASS.

- [ ] **Step 4: Commit**

```bash
git add core/content_pipeline/generators/reel_generator.py core/content_pipeline/tests/test_reel_generator.py
GIT_EDITOR=true git commit -m "$(cat <<'EOF'
refactor: simplifica cliente de Lyria para reusar _vertex_client()

GOOGLE_CLOUD_LOCATION ya apunta a 'global' (migracion Imagen -> Gemini 3.1
Flash Image), mismo location que Lyria siempre necesito -- el cliente
dedicado que Lyria construia aparte queda redundante.
EOF
)"
```

---

### Task 5: Verificación real end-to-end y cierre de hallazgos

**Files:**
- Modify: `hallazgos.txt` (agrega HALLAZGO 90 y HALLAZGO 91 al final)

**Interfaces:**
- Consumes: todo lo construido en Tareas 1-4. No produce interfaz nueva — es el task de verificación y documentación final.

- [ ] **Step 1: Generar 1 imagen de post real vía el pipeline real (dentro de `rqworker`, tiene credenciales reales)**

Run:
```bash
docker compose exec -T backend python manage.py shell -c "
from core.content_pipeline.generators.image_generator import ImageGenerator
gen = ImageGenerator(bucket_name='agente-cosmic-assets')
img_bytes = gen._generate_with_vertex('A cozy modern kitchen counter with a fresh smoothie in a glass, morning light, product photography style.')
with open('/tmp/verify_post_image.png', 'wb') as f:
    f.write(img_bytes)
from PIL import Image
import io
img = Image.open(io.BytesIO(img_bytes))
print('Dimensiones:', img.size, 'ratio:', round(img.size[0]/img.size[1], 3))
"
docker compose cp backend:/tmp/verify_post_image.png /tmp/verify_post_image.png
```

Expected: corre sin excepción, imprime dimensiones con ratio ≈1.0 (cuadrado). Inspeccionar
`/tmp/verify_post_image.png` visualmente (leer el archivo con la herramienta de lectura de
imágenes disponible) — confirmar que no hay texto/logos/manos deformadas evidentes.

- [ ] **Step 2: Generar 1 reel completo real vía el pipeline real, con foto de prueba real**

Verificar primero que existe una foto de prueba real en el contenedor (ya usada en sesiones
anteriores de este proyecto para pruebas de showcase):

Run: `docker compose exec -T backend ls /app/.test-photos/ 2>/dev/null || echo "sin fotos de prueba, usar cualquier imagen JPEG real disponible"`

Luego generar 3 escenas de reel reales (suficiente para confirmar 9:16 sin gastar de más en Veo):

```bash
docker compose exec -T backend python manage.py shell -c "
from core.content_pipeline.generators.reel_generator import ReelGenerator
gen = ReelGenerator(bucket_name='agente-cosmic-assets')
still_bytes = gen._generate_scene_still('A small business workshop scene, warm lighting, entrepreneur working, photorealistic.')
assert still_bytes is not None, 'QC o generacion fallo -- revisar logs'
with open('/tmp/verify_reel_scene.png', 'wb') as f:
    f.write(still_bytes)
from PIL import Image
import io
img = Image.open(io.BytesIO(still_bytes))
print('Dimensiones:', img.size, 'ratio:', round(img.size[0]/img.size[1], 3))
"
docker compose cp backend:/tmp/verify_reel_scene.png /tmp/verify_reel_scene.png
```

Expected: corre sin excepción, imprime dimensiones con ratio ≈0.5625 (9:16). Inspeccionar
`/tmp/verify_reel_scene.png` visualmente — confirmar que no hay texto/íconos/UI/logos
alucinados (si aparece algo, no es necesariamente un fallo del plan: `_validate_scene_still`
ya lo rechazaría en el flujo real completo — este paso genera 1 still aislado, sin pasar por
ese QC, a propósito, para inspeccionar la salida cruda del modelo).

- [ ] **Step 3: Confirmar que el rate limiter no bloquea nada nuevo**

Run:
```bash
docker compose exec -T backend python manage.py shell -c "
from core.shared import rate_limiter
print('RPM_LIMITS:', rate_limiter.RPM_LIMITS)
print('base_model de gemini-3.1-flash-image:', rate_limiter._base_model('gemini-3.1-flash-image'))
"
```

Expected: `RPM_LIMITS` vacío (`{}`), y `throttle('gemini-3.1-flash-image')` no bloquearía
nada (no hay entrada que matchee) — mismo comportamiento no-op que `gemini-2.5-flash` hoy.

- [ ] **Step 4: Correr la suite completa de Python**

Run: `docker compose exec -T backend pytest 2>&1 | tail -30`
Expected: todos los tests PASS, sin regresiones (compara el conteo total contra la corrida
más reciente conocida antes de este plan — debe ser igual o mayor, nunca menor).

- [ ] **Step 5: Actualizar `hallazgos.txt` con el cierre de este trabajo**

Al final de `hallazgos.txt`, agrega:

```
=================================================================
HALLAZGO 90 — Migracion Imagen 3/4 -> Gemini 3.1 Flash Image
  (deadline real de Google: 2026-08-17)
Estado: ✅ RESUELTO — 2026-08-07
=================================================================
Contexto: Google Cloud notifico (2 correos, 2026-08-07) que la fecha limite
  de discontinuacion de los endpoints imagen-3.0-*/imagen-4.0-* es el
  17 de agosto de 2026 (un correo de correccion reemplazo una fecha
  anterior de 2027 por error tipografico de Google). Esto ya estaba
  documentado como HALLAZGO IMG-12 en el archivo hallazgosImagen.txt
  (2026-08-02, ya no existe en el repo -- este hallazgo continua esa
  numeracion aqui en hallazgos.txt), con severidad Baja ("no urgente,
  deadline 2027") -- quedo obsoleto por el nuevo deadline.

Cambios reales (spec completo:
  docs/superpowers/specs/2026-08-07-imagen-to-gemini-migration-design.md,
  plan: docs/superpowers/plans/2026-08-07-imagen-to-gemini-migration-plan.md):
  - VERTEX_IMAGE_MODEL: imagen-3.0-generate-001 -> gemini-3.1-flash-image
    (calidad completa, no flash-lite -- decision explicita de Anuar).
  - GOOGLE_CLOUD_LOCATION: us-central1 -> global (unico location donde
    los modelos de imagen nuevos responden -- verificado con llamada
    real, 404 en us-central1). Confirmado con llamadas reales que Veo y
    TTS tambien funcionan en 'global' sin riesgo antes de unificar.
  - image_generator.py y reel_generator.py migrados de generate_images
    a generate_content con image_config.aspect_ratio (1:1 y 9:16
    respectivamente). Negative prompt (sin equivalente estructurado en
    Gemini) doblado en el texto del prompt en ambos sitios -- decision
    explicita de Anuar en reel_generator.py pese al riesgo historico ahi
    documentado (icono alucinado con Imagen), mitigado por el QC visual
    ya existente (_validate_scene_still/_validate_background).
  - Corregido de paso: la rama Gemini de image_generator.py llamaba
    record_tokens(resp) (tarifa de texto, $0.30/1M tokens) para
    facturar imagenes -- subestimaba el costo real ~200x (los tokens de
    imagen se facturan a ~$59.8/1M equivalente). Ahora usa
    record_gemini_image_generation('generate') (costo plano real,
    $0.067/imagen, fuente: documentacion oficial de precios de Google).
  - VERTEX_IMAGE_EDIT_MODEL (imagen-3.0-capability-001) eliminado --
    config muerta, sin consumidor en el repo.
  - RPM_LIMITS de rate_limiter.py limpiado (las 2 entradas de Imagen 3
    ya no aplican; gemini-3.1-flash-image sin limite fijo conocido,
    igual que gemini-2.5-flash hoy).

Severidad: N/A -- migracion completada antes del deadline.
Prioridad: Cerrado.

=================================================================
HALLAZGO 91 — El costo/conteo de reel_scene nunca aparece en el
  dashboard de Prometheus (cosmic_imagen_generations_by_type_total)
Categoria: Gap de observabilidad, preexistente (no es regresion de
  HALLAZGO 90)
Estado: 🟡 REPORTADO — SIN FIX — 2026-08-07
=================================================================
Contexto: al leer core/shared/metrics.py durante la migracion del
  HALLAZGO 90, se confirmo que el collector de Prometheus itera sobre
  tuplas hardcodeadas de tipos: ('generate', 'bgswap', 'qc_retry') para
  el conteo (cosmic_imagen_generations_by_type_total) y
  ('generate', 'bgswap') para el costo
  (cosmic_imagen_cost_microdollars_total). El tipo 'reel_scene' (el que
  pasa reel_generator.py en cada still de escena generado) no esta en
  ninguna de las 2 tuplas -- los Redis keys cosmic:prom:I:reel_scene /
  cosmic:prom:IC:reel_scene se siguen escribiendo pero el collector
  nunca los lee, asi que nunca aparecen en el panel. Esto ya pasaba
  identico con Imagen 3 antes de esta migracion -- no es una regresion,
  es un gap preexistente que salio a la luz al revisar el archivo
  completo. 'bgswap' en las tuplas es ademas resto del pipeline ya
  eliminado (HALLAZGO 65).

Propuesta de fix (no implementada, fuera de alcance de HALLAZGO 90):
  agregar 'reel_scene' a ambas tuplas en metrics.py (y opcionalmente
  quitar 'bgswap', muerto) para que el costo/conteo real de generacion
  de stills de reel sea visible en el dashboard.

Severidad: Baja -- no afecta produccion, solo visibilidad de costo real
  en el panel de negocio.
Prioridad: Baja -- agrupar con la proxima limpieza de metricas.
```

- [ ] **Step 6: Commit**

```bash
git add hallazgos.txt
GIT_EDITOR=true git commit -m "$(cat <<'EOF'
docs: documenta HALLAZGO 90 (migracion Imagen->Gemini completada, cierra
IMG-12) y HALLAZGO 91 (gap de observabilidad reel_scene, preexistente)
EOF
)"
```
