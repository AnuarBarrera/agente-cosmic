# Generación de imagen con foto real de producto (módulo 1 de 3) — Diseño

## Contexto

El campo `AnalysisJob.product_reference_image_path` ya existe y `analyze_submit`
ya acepta la subida de foto de producto para **cualquier** modo de generación
(`views.py:183`) — pero hoy solo `MODE_SAMPLE_PRODUCT_REEL` la lee. En los otros
3 modos (`MODE_FULL`, `MODE_SAMPLE_IMAGE`, `MODE_SAMPLE_REEL`) la foto se sube,
se guarda, y se ignora por completo.

`MODE_SAMPLE_PRODUCT_REEL` usa `ProductShowcaseGenerator`, el catálogo de
templates 3D (HyperFrames + Three.js: confetti-fall, frame-assembly,
glass-shatter-reveal). No comparte tecnología con la edición/composición
directa de fotos reales que ya se validó en el rediseño de nano banana
(`gemini-3.1-flash-image` viendo la foto real en la misma llamada multimodal
que la dirección creativa — "Enfoque A", ver
`project_nano_banana_pipeline_redesign_2026_08_13.md`).

Este documento cubre **solo el primer módulo** de un plan de 3: hacer que el
modo "solo imagen" (`MODE_SAMPLE_IMAGE`, gateado a `allows_sample_generation`,
hoy solo admin/tester) use la foto real de producto cuando el usuario la sube.
Los módulos de reel y pipeline de 7 días se brainstorman por separado, después
de ver los resultados reales de este módulo — decisión explícita de Anuar para
poder iterar sin afectar producción.

## Alcance

**Dentro de este módulo:**
1. Retirar `MODE_SAMPLE_PRODUCT_REEL`, `ProductShowcaseGenerator`, y los
   templates 3D asociados por completo.
2. Análisis de la foto de producto (`ProductPhotoAnalyzer`, nuevo) durante
   `analyze_brand_task`, guardado en `BrandDNA`.
3. Primera generación de imagen usando la foto real (modo `MODE_SAMPLE_IMAGE`
   con foto subida) — nano banana edita/compone la foto real directamente.
4. Regeneración (`calendar_post_action`, acción `regenerate`) usando la imagen
   actual + el análisis guardado + el feedback del usuario, cuando el post
   pertenece a un job con foto real.
5. Auditor de calidad específico para este camino (texto solo se rechaza si
   está mal escrito, no por su sola presencia).
6. Modelo `gemini-3.1-flash-lite-image` primero (el más económico), fácil de
   cambiar para comparar contra `gemini-3.1-flash-image` después.

**Fuera de alcance (explícitamente diferido):**
- Módulo de reel con foto real, y pipeline completo de 7 días — brainstorms
  separados después de validar este módulo.
- Usar el análisis de visión para generar imágenes "extra" que no usan la foto
  directamente (aplica cuando haya carrusel/semana completa, no en este módulo
  de una sola imagen).
- Cruzar/reutilizar análisis de fotos de OTROS negocios (matching entre
  negocios del mismo giro) — este módulo solo guarda el análisis bien
  clasificado; el consumo cruzado es el módulo de reutilización ya
  identificado en el rediseño (punto 4).
- El análisis de visión mejorando captions de `MODE_FULL` (producción real)
  — el análisis siempre se guarda (barato), pero `TextGenerator`/
  `ImageGenerator` solo lo usan cuando el modo es de prueba/admin, mientras se
  itera.

## Arquitectura

### 1. Análisis de foto de producto (nuevo)

`ProductPhotoAnalyzer` (`core/brand_dna/extractors/product_photo_analyzer.py`)
espeja exactamente el patrón de `LogoAnalyzer`
(`core/brand_dna/extractors/logo_analyzer.py`): una llamada a
`VERTEX_TEXT_MODEL` (`gemini-3.5-flash`) con la foto + un prompt pidiendo
descripción y clasificación. Fail-open (try/except, análisis vacío si falla —
no bloquea el resto del análisis de marca).

Devuelve:
- `description`: texto libre (tipo de producto, colores, materiales, estilo,
  detalles distintivos) — usado como contexto en generación/regeneración.
- `category`: clasificación normalizada corta (1-3 palabras, ej. "joyería",
  "repostería", "ropa") — para clasificar el registro en BD, sin lógica de
  consumo cruzado todavía.

Campos nuevos en `BrandDNA` (migración nueva):
- `product_photo_analysis = models.TextField(blank=True, default='')`
- `product_category = models.CharField(max_length=100, blank=True, default='')`

`analyze_brand_task` (`core/brand_dna/tasks.py`) llama a
`ProductPhotoAnalyzer` junto a `LogoAnalyzer`, solo si
`job.product_reference_image_path` existe. Corre y guarda siempre que haya
foto, sin importar el modo — es información real reusable a futuro, barata de
generar ahora que ya se está leyendo la imagen.

### 2. Primera generación con foto real

Nuevo método `ImageGenerator.generate_from_product_photo(photo_bytes,
mime_type, caption, colors, tone, ..., vision_context='')`
(`core/content_pipeline/generators/image_generator.py`). Una sola llamada
multimodal a nano banana: foto real + dirección creativa (mismo texto que hoy
construye `_analyze_brand_scene`/el prompt de escena) + instrucción explícita
de:
- extraer solo el producto real de la foto,
- eliminar cualquier texto/marca de agua/logo presente en la foto original,
- no agregar texto nuevo (headline/CTA) por el momento.

Usa `settings.VERTEX_IMAGE_MODEL_LITE` (nuevo setting,
`gemini-3.1-flash-lite-image`) — constante fácil de cambiar a
`VERTEX_IMAGE_MODEL` para comparar una vez validado con el modelo económico.

**Rate limit**: `gemini-3.1-flash-lite-image` es un `base_model` distinto a
`gemini-3.1-flash-image` — sin una entrada propia en
`RPM_LIMITS['vertex']`, el throttle de `rate_limiter.py` sería un no-op para
este modelo (mismo tipo de hueco silencioso que causó el incidente original
de rate limit). Agregar `RPM_LIMITS['vertex']['gemini-3.1-flash-lite-image']
= 1` como valor conservador de partida (no hay dato empírico propio para
lite en Vertex todavía) y pasar por `call_with_429_retry` igual que el resto.
Este método es admin/prueba únicamente (bajo volumen), así que el impacto de
un límite conservador es mínimo.

`generate_sample_task` (`core/content_pipeline/tasks.py`) rutea a este método
en vez del camino de generación desde texto cuando `job.generation_mode ==
MODE_SAMPLE_IMAGE` y `job.product_reference_image_path` está presente.

### 3. Regeneración con imagen de referencia

Nuevo método `ImageGenerator.regenerate_with_reference(current_image_bytes,
vision_context, feedback, ...)`. Distinto del método anterior: no manda la
foto original, manda la **imagen actual** (lo que el usuario está viendo) +
el `product_photo_analysis` guardado (contexto del producto real, para no
perder fidelidad en regeneraciones sucesivas) + el feedback del usuario.

Confirmado con código real: el mecanismo de regeneración de hoy
(`calendar_post_action`, acción `regenerate`, `views.py:527`) NO tiene ningún
camino de edición basado en imagen — regenera el caption con
`_regenerate_caption` y vuelve a generar desde cero con el camino de texto
normal. Este método nuevo es un camino genuinamente distinto, no una extensión
del existente.

`calendar_post_action` rutea a `regenerate_with_reference` cuando
`post.calendar.brand_dna.job.product_reference_image_path` está presente;
para posts sin foto real, el comportamiento de hoy no cambia.

### 4. Auditor de calidad

Nuevo método de QC (mismo patrón que `_validate_background`/`ImageQCSchema`,
pero NO modifica ese método existente — lo usa el pipeline de templates
actual, que sí depende de "cero texto"). Schema extendido:

```python
class ProductPhotoQCSchema(BaseModel):
    has_text: bool
    text_is_correct_spanish: bool  # true si has_text=false (no aplica)
    is_abstract_3d: bool
    has_screen_content: bool
    has_malformed_object: bool
    has_unrealistic_grounding: bool
    has_suggestive_or_exposed_content: bool
    ok: bool  # ok = (not has_text or text_is_correct_spanish) and NOT(resto)
```

Si `ok=False` (texto mal escrito u otro criterio existente), dispara el mismo
mecanismo de reintento (`max_qc_retries`) que ya usa `_generate_background`.

### 5. Retiro de MODE_SAMPLE_PRODUCT_REEL

- Borrar el modo del `MODE_CHOICES` de `AnalysisJob` y de `valid_modes` en
  `analyze_submit`.
- Borrar `ProductShowcaseGenerator` y su uso en
  `_generate_product_reference_sample` (`tasks.py`).
- Borrar los templates 3D del catálogo (`confetti-fall.html`,
  `frame-assembly.html`, `glass-shatter-reveal.html` y sus assets) — confirmar
  con grep que nada más los referencia antes de borrar.
- Actualizar el template de selección de modo en el admin
  (`new_analysis.html`) para quitar la opción.

## Manejo de errores

- `ProductPhotoAnalyzer`: fail-open, igual que `LogoAnalyzer` — análisis vacío
  si falla, no bloquea `analyze_brand_task`.
- `generate_from_product_photo` / `regenerate_with_reference`: mismo patrón
  que hoy — excepción atrapada, logueada, no rompe el flujo. En regeneración,
  si falla, se conserva la imagen anterior (igual que el comportamiento actual
  de `calendar_post_action`).
- Auditor: mismo mecanismo de reintento ya existente (`max_qc_retries`), sin
  cambios de comportamiento fuera del criterio de texto.

## Testing

- `ProductPhotoAnalyzer`: éxito (mockeado) y fail-open ante excepción.
- `analyze_brand_task`: llama a `ProductPhotoAnalyzer` solo si hay foto,
  guarda los campos nuevos en `BrandDNA`.
- `generate_from_product_photo`: prompt incluye instrucción de quitar
  texto/marca de agua original y no agregar texto nuevo; usa
  `VERTEX_IMAGE_MODEL_LITE`.
- `regenerate_with_reference`: manda la imagen actual (no la foto original) +
  contexto guardado + feedback.
- Auditor nuevo: texto correcto pasa, texto incorrecto rechaza y dispara
  reintento; ausencia de texto pasa igual que hoy.
- `generate_sample_task` / `calendar_post_action`: ruteo correcto según
  presencia de `product_reference_image_path`, sin tocar el camino sin foto.
- Retiro de `MODE_SAMPLE_PRODUCT_REEL`: confirmar que no queda ninguna
  referencia rota (grep de nombres de archivo/clase antes de borrar).
- Prueba manual real (Anuar, vía admin): subir foto real de producto, generar
  con `MODE_SAMPLE_IMAGE`, inspeccionar resultado, regenerar con feedback,
  inspeccionar de nuevo.
