# Migración de Imagen 3/4 a Gemini 3.1 Flash Image — Diseño

## Contexto

Google Cloud notificó (2 correos reenviados por Anuar, 2026-08-07) que la fecha límite de
discontinuación de los endpoints `imagen-3.0-*` e `imagen-4.0-*` es el **17 de agosto de
2026** (10 días desde hoy) — un correo de corrección reemplazó una fecha anterior de 2027
por un error tipográfico de Google. Después de esa fecha, cualquier llamada a esos
endpoints devuelve 404. El proyecto `agente-cosmic` está en la lista de proyectos
afectados que Google adjuntó.

Esto ya estaba documentado como HALLAZGO IMG-12 en `hallazgosImagen.txt` (2026-08-02), con
severidad/prioridad Baja ("deadline no antes de 2027, agrupar con otra migración de SDK
cuando se haga"). Ese hallazgo queda obsoleto por el nuevo deadline y se marca RESUELTO
como parte de este trabajo.

Google recomienda migrar a `gemini-3.1-flash-image` o `gemini-3.1-flash-lite-image`.
Ambos modelos fueron verificados con llamadas reales contra el proyecto de Vertex AI de
Cosmic (no solo confirmados por `client.models.list()`, sino con una generación de imagen
real de cada uno) — ver sección "Decisiones confirmadas" para los datos exactos.

## Decisiones confirmadas (Anuar, este brainstorm, todas con evidencia real)

- **Modelo: `gemini-3.1-flash-image`** (calidad completa) en los 2 sitios que generan
  imágenes hoy, no `gemini-3.1-flash-lite-image`. Verificado con llamada real:
  `flash-image` devolvió un PNG de ~1MB en ~10s; `flash-lite-image` devolvió un JPEG de
  ~50KB en ~2.5s (más barato/rápido pero menor calidad). Anuar eligió calidad completa en
  ambos sitios (`image_generator.py` y `reel_generator.py`) en vez de diferenciar por sitio.
- **Location: `global` en todo el proyecto** (`GOOGLE_CLOUD_LOCATION`), no solo para
  imagen. Hallazgo real: los modelos de imagen nuevos SOLO responden en `location='global'`
  (404 en `us-central1`, donde vive el resto del pipeline hoy). Antes de asumir que había
  que aislar esto con un setting nuevo solo para imagen, se verificó con llamadas reales que
  Veo (`veo-3.1-fast-generate-001`, generó un video real de 1.2MB) y TTS
  (`gemini-2.5-flash-tts`, generó audio real) también funcionan sin error en `global` — no
  hay riesgo de romperlos. Con ese riesgo descartado, Anuar prefirió unificar todo a
  `global` en vez de mantener dos locations distintas.
- **Costo estimado real**: `gemini-3.1-flash-image` cuesta **$0.067 por imagen** a
  1024×1024 estándar (fuente: documentación oficial de precios de Google, confirmada por
  WebFetch), frente a los $0.04 que costaba Imagen 3 — sube ~68%. El constante de costo en
  `metrics_utils.py` se actualiza a este valor real, no se deja el de Imagen 3.
- **Métricas de Prometheus: renombrar solo el label genérico, no los nombres de métrica
  dedicados.** Hay 2 familias distintas, descubiertas al leer el código completo (no
  estaban diferenciadas en el brainstorm inicial):
  1. `track_external_api('imagen3', ...)` — un label dentro de la familia genérica
     `EXTERNAL_API_REQUESTS`/`EXTERNAL_API_DURATION`/`EXTERNAL_API_ERRORS` (compartida con
     `'veo'`, `'gemini'`, `'lyria'`, etc.). Se renombra a `'gemini_image'` — Anuar aceptó
     explícitamente que esto corta la continuidad de cualquier panel de Grafana que filtre
     por ese label.
  2. `cosmic_imagen_generations_by_type_total` y `cosmic_imagen_cost_microdollars_total`
     (`core/shared/metrics.py`) — nombres de métrica de Prometheus hardcodeados, expuestos
     directamente como paneles. **Estos NO se renombran** — Anuar decidió mantenerlos tal
     cual tras ver que son el nombre real de panel (más disruptivo cortar que el label
     genérico). Su función interna (`record_imagen_generation`) sí se renombra a
     `record_gemini_image_generation`, pero sigue escribiendo a las mismas llaves de Redis
     (`cosmic:prom:I:{tipo}` / `cosmic:prom:IC:{tipo}`) que ese archivo ya lee — cero
     cambio funcional en el dashboard.
- **Hallazgo nuevo, fuera de alcance de este plan**: el `img_type='reel_scene'` que pasa
  `reel_generator.py` a `record_imagen_generation` nunca aparece en el dashboard — el
  collector de `metrics.py` solo itera sobre los tipos hardcodeados
  `('generate', 'bgswap', 'qc_retry')` para el conteo y `('generate', 'bgswap')` para el
  costo; `'reel_scene'` no está en ninguna de las 2 tuplas (y `'bgswap'` es del pipeline ya
  eliminado, HALLAZGO 65). Esto ya pasaba con Imagen 3 y seguirá pasando igual con Gemini
  — no es una regresión de esta migración, se documenta como hallazgo nuevo en
  `hallazgosImagen.txt` pero no se corrige aquí (scope creep, no bloquea el deadline).
- **Simplificación de Lyria incluida**: el cliente dedicado de
  `_generate_music_attempt` (hardcodeado a `location='global'` porque Lyria nunca funcionó
  en `us-central1` — comentario ya existente en el código lo documenta) se vuelve idéntico a
  `_vertex_client()` una vez que ese cliente también apunta a `global`. Se simplifica a
  reusar `_vertex_client()`, eliminando la construcción de cliente duplicada.

## Estado real del código (verificado 2026-08-07 con lectura directa, no de memoria)

`settings.VERTEX_IMAGE_MODEL = 'imagen-3.0-generate-001'` (`saas_chatbot/settings.py:166`)
es la única fuente de verdad del modelo, usada en 2 sitios:

### `core/content_pipeline/generators/image_generator.py:_generate_with_vertex` (líneas ~784-816)

Ya tiene una rama condicional:

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
            record_imagen_generation('generate')
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

La rama `else` (Gemini) ya fue probada en un experimento real
(`core/content_pipeline/management/commands/test_product_reference_pipeline.py`, modelo
`gemini-2.5-flash-image`, confirmado con `client.models.list()` el 2026-07-27), pero
**le faltan 2 cosas** que la rama Imagen sí cubría y el pipeline de posts necesita
(cuadrado exacto para el formato de post, negative prompt contra manos deformes/anatomía
incorrecta):

- Gemini no fuerza 1:1 por default — verificado con llamada real: pasar
  `image_config=types.ImageConfig(aspect_ratio='9:16')` a `GenerateContentConfig` sí fuerza
  la proporción pedida (probado con `9:16`, devolvió 768×1376 ≈ 0.558, objetivo 0.5625).
  Para este archivo el valor correcto es `'1:1'`.
- Gemini no tiene parámetro estructurado de negative prompt — se dobla el texto de
  `_IMAGE_NEGATIVE_PROMPT` dentro del prompt.

### `core/content_pipeline/generators/reel_generator.py:_generate_scene_still` (líneas ~691-718)

Llama `generate_images` directo, sin ninguna rama Gemini:

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
            record_imagen_generation('reel_scene')
            return resp.generated_images[0].image.image_bytes
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

Este sitio necesita reescritura completa (no solo cambiar un string): API distinta
(`generate_content`), parámetros distintos (`image_config` en vez de
`GenerateImagesConfig`), forma de leer la respuesta distinta
(`resp.candidates[0].content.parts[].inline_data.data` en vez de
`resp.generated_images[0].image.image_bytes`), y el manejo de "0 imágenes por filtro de
seguridad" cambia: Imagen exponía `positive_prompt_safety_attributes`, Gemini reporta
bloqueo distinto (vía `finish_reason` del candidate, o ausencia de `inline_data` en las
partes) — el detalle exacto de qué inspeccionar se confirma durante la implementación con
una llamada real que fuerce un bloqueo de seguridad, no se asume aquí.

**Riesgo real evaluado y resuelto (Anuar, este brainstorm):** el comentario en
`reel_generator.py:375-383` documenta una alucinación real ya observada — mencionar
"icons"/"UI elements" dentro del prompt afirmativo, aunque sea para negarlos, hizo que
Imagen los generara de todos modos (un ícono de botón de play apareció incrustado pese a
que el prompt lo prohibía explícitamente). Por eso `_VEO_SAFE_CONSTRAINTS` se pasa hoy
SOLO vía el parámetro dedicado `negative_prompt` de `GenerateImagesConfig`, nunca
concatenado al texto — y existe un test explícito que lo blinda
(`test_reel_generator.py:481`, `assert 'NO icons' not in call_kwargs['prompt']`). Gemini
no tiene un parámetro estructurado equivalente, así que doblar el texto es la única forma
de aplicar esa restricción con la nueva API.

Antes de decidir, se hizo una prueba real de control (2 llamadas reales a
`gemini-3.1-flash-image`, un mismo prompt afirmativo con y sin el texto negativo doblado
encima) — ninguna de las 2 imágenes generadas mostró íconos, texto ni logos alucinados.
Muestra de tamaño 1, no concluyente por sí sola, pero sin evidencia de que el problema se
traslade de Imagen a Gemini. Con esa evidencia sobre la mesa, Anuar decidió doblar
`_VEO_SAFE_CONSTRAINTS` en el texto de todas formas (mismo patrón que `image_generator.py`
sección anterior), aceptando el riesgo residual. Como red de seguridad adicional (ya
existente, no es parte nueva de este plan), `_validate_scene_still` sigue corriendo
después de cada generación y ya rechaza+reintenta si `has_screen_content` detecta
íconos/UI/logos — cualquier alucinación que sí ocurra no llega a producción silenciosa.

El test `test_reel_generator.py:481` (`TestGenerateSceneStill.test_returns_image_bytes_on_success`)
y sus 2 vecinos en la misma clase deben reescribirse para reflejar esta decisión: ya no se
verifica `config.negative_prompt` (no existe en la API de Gemini) ni que `'NO icons' not in
prompt` (ahora sí está, deliberadamente) — se verifica en cambio que el texto de
`_VEO_SAFE_CONSTRAINTS` SÍ forma parte del `contents` enviado a `generate_content`.

### `core/shared/rate_limiter.py` (líneas 11-14)

```python
RPM_LIMITS = {
    'imagen-3.0-generate': 20,      # subido de 1 -> 20 el 2026-07-06
    'imagen-3.0-capability': 10,
}
```

`_base_model()` extrae `'imagen-3.0-generate-001'` → `'imagen-3.0-generate'` vía regex
`-\d+$`. Con el nuevo nombre de modelo (`gemini-3.1-flash-image`), ninguna entrada de
`RPM_LIMITS` hace match — `throttle()` ya maneja ese caso sin error (no-op cuando
`RPM_LIMITS.get(base_model)` es `None`, mismo comportamiento documentado hoy para
`gemini-2.5-flash`, que usa Dynamic Shared Quota). Las 2 entradas quedan muertas y se
eliminan; no se agrega ninguna entrada nueva para `gemini-3.1-flash-image` salvo que
aparezcan 429s reales en producción tras el cambio.

### `core/shared/metrics_utils.py` (líneas ~28-36, ~112-116)

```python
_IMAGEN_COST_PER_IMAGE = 40000          # $0.04 = 40,000 microdólares
...
def record_imagen_generation(imagen_type: str = 'generate'):
    """Registra una generación de Imagen 3 con su costo estimado."""
    _redis_inc(f'cosmic:prom:I:{imagen_type}')
    _redis_inc(f'cosmic:prom:IC:{imagen_type}', _IMAGEN_COST_PER_IMAGE)
```

`_IMAGEN_COST_PER_IMAGE` pasa a `67000` (µ$0.067, precio real confirmado de
`gemini-3.1-flash-image` a 1024×1024 estándar). `record_imagen_generation` se renombra a
`record_gemini_image_generation` (o nombre equivalente decidido en el plan) — mismos
call sites en `image_generator.py` y `reel_generator.py` se actualizan.

### `core/content_pipeline/generators/reel_generator.py:_generate_music_attempt` (líneas ~812-822)

```python
client = genai.Client(
    vertexai=True,
    project=settings.GOOGLE_CLOUD_PROJECT,
    location='global',
)
```

Con `GOOGLE_CLOUD_LOCATION` pasando a `'global'`, este cliente dedicado se vuelve idéntico
a `_vertex_client()` — se simplifica a reusar `_vertex_client()`, eliminando la
construcción de cliente duplicada y el comentario que explica por qué Lyria necesitaba un
cliente aparte (ya no aplica).

## Cambios de configuración

`saas_chatbot/settings.py`:
- `GOOGLE_CLOUD_LOCATION` default: `'us-central1'` → `'global'` (línea 159).
- `VERTEX_IMAGE_MODEL`: `'imagen-3.0-generate-001'` → `'gemini-3.1-flash-image'` (línea 166).
- `VERTEX_IMAGE_EDIT_MODEL = 'imagen-3.0-capability-001'` (línea 167) — **se elimina**.
  Verificado con grep en todo el repo: no tiene ningún consumidor (probable resto del
  pipeline BGSWAP ya eliminado, HALLAZGO 65). Coincide con la entrada muerta
  `'imagen-3.0-capability'` que también se elimina de `RPM_LIMITS` en `rate_limiter.py`.

## Fuera de alcance

- Veo, TTS y el modelo de texto (`VERTEX_TEXT_MODEL`) no cambian funcionalmente — solo
  comparten el nuevo `GOOGLE_CLOUD_LOCATION='global'`, confirmado sin riesgo con llamadas
  reales (ver "Decisiones confirmadas").
- HALLAZGO IMG-13 (triage de producto-referencia rechaza fotos legítimas) — tema
  distinto, no se toca aquí.
- Cualquier ajuste manual de dashboards de Grafana/Prometheus que dependan del nombre de
  métrica `imagen3` — Anuar fue notificado de que el rename corta la continuidad, pero
  revisar/actualizar esos dashboards (si existen) queda fuera de este plan.

## Testing

- Tests unitarios existentes que mockean `generate_images`/`resp.generated_images`
  (`test_image_generator.py`, `test_reel_generator.py`, y cualquier test de
  `rate_limiter.py`/`metrics_utils.py` que referencie los nombres viejos) se reescriben
  para mockear `generate_content`/`resp.candidates[...].inline_data`.
- Verificación real obligatoria vía `rqworker` (tiene las credenciales y el entorno real,
  no CLI suelto): generar 1 imagen de post real con `ImageGenerator` completo (confirmar
  1:1 exacto, inspección visual de que no hay artefactos evidentes) y 1 reel completo con
  foto de prueba real (confirmar stills en 9:16, sin regresión visual notoria frente a
  Imagen 3).
- Confirmar que el rate limiter no bloquea nada nuevo tras el cambio (no debería, sin
  entrada en `RPM_LIMITS` para el modelo nuevo).
- Actualizar HALLAZGO IMG-12 en `hallazgosImagen.txt` a RESUELTO, con referencia a este
  spec y al deadline real que lo disparó.
- Suite completa de tests de Python sin regresiones.
