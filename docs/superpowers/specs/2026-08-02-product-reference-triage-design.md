# Triage previo en ProductReferenceGenerator — Diseño

## Contexto

`ProductReferenceGenerator` (`core/content_pipeline/generators/product_reference_generator.py`)
es el pipeline experimental "producto real como referencia" (solo admin, `AnalysisJob.MODE_SAMPLE_PRODUCT_IMAGE`/
`MODE_SAMPLE_PRODUCT_REEL`): toma una foto real subida por el usuario y le pide a
Gemini que genere una escena nueva incorporando el producto, luego (en modo reel)
anima esa escena con Veo.

Tres hallazgos documentados en `hallazgosImagen.txt` muestran que una parte de los
rechazos no son bugs de prompt — son fotos que, por su naturaleza, nunca deberían
haber entrado al pipeline de regeneración con IA:

- **IMG-05**: la "foto de referencia" es en realidad una captura de pantalla
  completa de Instagram (interfaz del teléfono, texto de UI, captions). Rechazo
  correcto del QC, pero se llega a él tras gastar scene-gen + QC completos, y el
  mensaje de error no le dice al usuario qué subió mal. El mismo hallazgo señaló
  un sub-caso: fotos reales (no capturas) con una **marca de agua agresiva** que el
  negocio le puso a su propia foto para protegerla de robo — el modelo la hereda
  en la escena generada y el QC la rechaza en un loop de reintentos inútiles.
- **IMG-06**: el producto vendido ES el texto (globo con "Feliz Cumpleaños"
  impreso, dulces de marca reconocible). Pedirle al modelo que omita el texto
  destruye la identidad del producto — el rechazo de QC es "correcto" pero
  inevitable con el prompt actual.
- **IMG-04**: foto de una persona completa modelando el producto (ropa). Reto de
  composición/anclaje físico que el modelo no domina de forma confiable
  (`has_unrealistic_grounding`).

**Insight de Anuar** (2026-07-27, retomado 2026-08-02): en vez de seguir peleando
contra la naturaleza del input con más ajustes de prompt, para este tipo de fotos
la mejor estrategia es clasificarlas ANTES de gastar en generación, y para los
casos donde la foto ya es válida mostrar el producto tal cual, usarla directamente
(con mejora clásica de imagen, sin IA generativa) en vez de forzar una regeneración
que va a fallar o que es innecesaria: *"si suben una imagen con texto, pero es
profesional el triage lo debe detectar y permitir usar para generar el reel o el
post, por que una imagen profesional no necesita IA."*

## Objetivo

Agregar un paso de **triage** al inicio de `generate_image()`/`generate_reel()`
que clasifique la foto subida en 3 rutas — **RECHAZAR / MEJORAR / REGENERAR** —
antes de invocar `_generate_scene()`, evitando gasto de IA generativa en fotos que
no la necesitan o que no van a poder usarla con éxito.

## Alcance

Cambio acotado a `ProductReferenceGenerator` y su función de mejora clásica en
`image_utils.py`. **No toca** `image_generator.py` ni `reel_generator.py` (el
pipeline principal genera desde texto de marca, sin foto subida por el usuario —
no hay nada que triar ahí). **No toca** `tasks.py` — la interfaz externa de
`generate_image()`/`generate_reel()` no cambia (ver "Interfaz externa" abajo).

Fuera de alcance explícito: remoción de marca de agua (demasiado propenso a
artefactos), apertura de este pipeline a testers/users (sigue solo-admin, sin
cambios de acceso).

## Arquitectura

Nuevo método privado `_triage(self, photo_bytes: bytes) -> tuple[str, dict]`,
llamado como primer paso de `generate_image()` y `generate_reel()`, antes de
`_generate_scene()`. Usa el mismo patrón que el QC existente (`_validate_scene`):
1 llamada a `settings.VERTEX_TEXT_MODEL` con `response_schema` (Pydantic) pidiendo
directamente los flags de clasificación.

```python
_TRIAGE_ROUTE_REJECT = 'reject'
_TRIAGE_ROUTE_ENHANCE = 'enhance'
_TRIAGE_ROUTE_REGENERATE = 'regenerate'


class TriageSchema(BaseModel):
    is_screenshot_or_ui: bool
    has_aggressive_watermark: bool
    product_identity_is_text: bool
    has_full_person_subject: bool
    is_already_professional: bool
```

### Prompt de triage

```python
_TRIAGE_PROMPT = (
    "Analyze this product reference photo strictly. Reply ONLY with this JSON (no markdown):\n"
    "{\"is_screenshot_or_ui\": <bool>, \"has_aggressive_watermark\": <bool>, "
    "\"product_identity_is_text\": <bool>, \"has_full_person_subject\": <bool>, "
    "\"is_already_professional\": <bool>}\n\n"
    "is_screenshot_or_ui: true if this image is a screenshot of a phone or app interface "
    "(social media app chrome, status bar, buttons, captions/likes/comments overlay) rather "
    "than a direct photograph of a product — OR a meme, flyer, or graphic-design composition "
    "that is not a real photograph. Be strict: any visible phone status bar or app UI chrome "
    "counts.\n"
    "has_aggressive_watermark: true if a large, hard-to-miss watermark, stamp, or repeated "
    "diagonal text overlay (added on top of the photo to protect it from theft) covers a "
    "significant part of the image. Do NOT count a small, subtle logo tucked in a corner — "
    "only large/central/repeated overlays. Do NOT count text or branding that is physically "
    "printed on the product itself (that is a different signal).\n"
    "product_identity_is_text: true if removing or altering the visible text, printed message, "
    "or brand markings would fundamentally change what the product IS — for example a balloon "
    "printed with a specific message, or packaged candy where the visible assortment of brand "
    "names is the point of the product. False for a generic protective watermark overlay (that "
    "is has_aggressive_watermark, not this).\n"
    "has_full_person_subject: true if a full or majority human body is the main subject, "
    "wearing, holding, or modeling the product (e.g. a person modeling a garment) rather than "
    "the product photographed alone or in a still-life composition.\n"
    "is_already_professional: true if the photo already has good lighting, a clean or "
    "uncluttered background, sharp focus, and a considered composition — it looks usable in "
    "social media marketing without further AI editing."
)
```

### Lógica de ruteo

```python
def _route_from_triage(data: dict) -> str:
    if data.get('is_screenshot_or_ui'):
        return _TRIAGE_ROUTE_REJECT
    if data.get('has_aggressive_watermark'):
        return _TRIAGE_ROUTE_REJECT
    if (data.get('product_identity_is_text') or data.get('has_full_person_subject')
            or data.get('is_already_professional')):
        return _TRIAGE_ROUTE_ENHANCE
    return _TRIAGE_ROUTE_REGENERATE
```

Prioridad explícita: `is_screenshot_or_ui` y `has_aggressive_watermark` se evalúan
ANTES que los criterios de MEJORAR — una foto con marca de agua agresiva no debe
enrutarse a MEJORAR aunque también sea "ya profesional" o el producto dependa del
texto, porque publicar la marca de agua tal cual en el contenido generado no es un
buen resultado, y regenerar la hereda en un loop de rechazo inútil.

**Manejo de errores del triage:** si la llamada falla (excepción), se asume
`_TRIAGE_ROUTE_REGENERATE` — mismo criterio "fail open" que ya usa
`_validate_scene` hoy, para no bloquear el pipeline por un error transitorio de
red/modelo.

## Ruta RECHAZAR

`generate_image()`/`generate_reel()` retornan de inmediato tras el triage, sin
llamar a `_generate_scene()`. Mensaje específico según el flag que disparó el
rechazo (nueva función `_describe_triage_rejection(data: dict) -> str`, mismo
patrón que `_describe_qc_failure`):

```python
def _describe_triage_rejection(data: dict) -> str:
    if data.get('is_screenshot_or_ui'):
        return (
            'La foto que subiste parece ser una captura de pantalla (de una app o red social), '
            'no una foto directa del producto. Sube una foto tomada directamente del producto, '
            'no una captura de pantalla.'
        )
    if data.get('has_aggressive_watermark'):
        return (
            'Tu foto tiene una marca de agua muy visible. Sube la misma foto sin la marca de '
            'agua para poder usarla.'
        )
    return 'La foto no pudo procesarse. Intenta con otra foto.'
```

## Ruta MEJORAR

Sin llamadas de IA generativa. Dos pasos:

**1. Mejora clásica** — nueva función en `core/content_pipeline/image_utils.py`:

```python
def enhance_photo_classic(image_bytes: bytes) -> bytes:
    """Recorte 1:1 centrado + nitidez suave + autocontraste — sin IA generativa.
    Usado por la ruta MEJORAR del triage de ProductReferenceGenerator: la foto
    original ya es válida, solo necesita quedar lista para publicarse."""
    img = Image.open(io.BytesIO(image_bytes))
    img = ImageOps.exif_transpose(img)
    img = img.convert('RGB')

    side = min(img.width, img.height)
    left = (img.width - side) // 2
    top = (img.height - side) // 2
    img = img.crop((left, top, left + side, top + side))

    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=3))
    img = ImageOps.autocontrast(img, cutoff=1)

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()
```

Recorte centrado (no top-biased — el top-biased del crop client-side existente es
para retratos/rostros; un producto suele estar centrado en el encuadre). Si la
mejora falla (excepción), se sube la foto original sin procesar — nunca bloquear
esta ruta por un fallo de post-procesamiento cosmético.

**2. Salida según modo:**
- **Imagen**: sube directo el resultado de `enhance_photo_classic()` vía
  `_upload_to_storage`, retorna `(url, '')`. Sin QC — no hay nada generado por IA
  que auditar, es la foto real del negocio.
- **Reel**: anima la foto mejorada con el mismo patrón `ffmpeg zoompan` (Ken
  Burns) que ya existe en `reel_generator.py::_animate_still_to_clip`, duplicado
  localmente en `product_reference_generator.py` (mismo criterio de duplicación
  deliberada que ya sigue el resto del archivo — cada generador es
  autocontenido). Nuevo método:

```python
def _animate_still_to_clip(self, image_bytes: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        image_path = os.path.join(tmp, 'still.png')
        with open(image_path, 'wb') as f:
            f.write(image_bytes)
        output_path = os.path.join(tmp, 'animated.mp4')
        subprocess.run(
            ['ffmpeg', '-y', '-loop', '1', '-i', image_path, '-t', '8',
             '-vf', (
                 "scale=8000:-1,"
                 "zoompan=z='min(zoom+0.0015,1.08)':d=1:"
                 "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                 "s=1080x1920:fps=24"
             ),
             '-c:v', 'libx264', '-pix_fmt', 'yuv420p', output_path],
            check=True, capture_output=True,
        )
        with open(output_path, 'rb') as f:
            return f.read()
```

  8 segundos / 1080x1920 / 24fps — mismos valores que usa hoy `_animate_scene`
  (Veo `duration_seconds=8`, `aspect_ratio='9:16'`) y que usa `reel_generator.py`
  como default cuando no hay clip real de Veo del cual medir (`_VIDEO_WIDTH`,
  `_VIDEO_HEIGHT`, `_DEFAULT_CLIP_FPS`). Si `ffmpeg` falla, se captura la
  excepción y se retorna `('', '', 'No se pudo generar el video a partir de la '
  'foto mejorada. Vuelve a intentar.')` — mismo estilo de mensaje que los demás
  fallos de generación.
  Sube tanto el poster (`enhance_photo_classic()` resultado) como el video,
  retorna `(video_url, poster_url, '')`.

## Ruta REGENERAR

Sin cambios — pipeline actual completo (`_generate_scene` + `_validate_scene` +,
en reel, `_animate_scene`/Veo + QC de los 3 frames vía `_QC_FRAME_OFFSETS`).

## Flujo completo

```
generate_image(photo, business_name, filename):
    route, triage_data = self._triage(photo)
    if route == REJECT:
        return '', _describe_triage_rejection(triage_data)
    if route == ENHANCE:
        enhanced = enhance_photo_classic(photo)  # o foto original si falla
        url = self._upload_to_storage(enhanced, filename, 'image/png', 'product-samples')
        return url, ''
    # route == REGENERATE — comportamiento actual sin cambios
    scene_bytes = self._generate_scene(photo, business_name)
    ...

generate_reel(photo, business_name, filename_prefix):
    route, triage_data = self._triage(photo)
    if route == REJECT:
        return '', '', _describe_triage_rejection(triage_data)
    if route == ENHANCE:
        enhanced = enhance_photo_classic(photo)
        video_bytes = self._animate_still_to_clip(enhanced)  # ffmpeg, no Veo
        poster_url = self._upload_to_storage(enhanced, f'{filename_prefix}-poster', ...)
        video_url = self._upload_to_storage(video_bytes, filename_prefix, 'video/mp4', ...)
        return video_url, poster_url, ''
    # route == REGENERATE — comportamiento actual sin cambios
    scene_bytes = self._generate_scene(photo, business_name)
    ...
```

## Interfaz externa (sin cambios)

`generate_image()` sigue devolviendo `tuple[str, str]` (url, reason).
`generate_reel()` sigue devolviendo `tuple[str, str, str]` (video_url, poster_url,
reason). `tasks.py::_generate_product_reference_sample` no necesita ningún cambio
— ya maneja ambos contratos de tupla desde el plan round2 (2026-08-02), y las 3
rutas de triage son 100% internas a `ProductReferenceGenerator`.

## Costo

- **RECHAZAR**: 1 llamada barata de triage. Ahorra scene-gen + QC completo (y en
  reel, Veo) — el ahorro más grande de las 3 rutas.
- **MEJORAR**: 1 llamada barata de triage, cero llamadas de IA generativa después
  (ni Gemini de escena ni Veo) — coincide con el objetivo original de Anuar
  ("cero costo de IA generativa en esta ruta").
- **REGENERAR**: 1 llamada barata extra sobre el pipeline actual (el costo del
  triage mismo). Tradeoff aceptado: es la ruta que ya paga el costo completo de
  scene-gen + QC + Veo, el triage es marginal en comparación.

## Testing (cobertura esperada, detalle exacto en el plan)

- `_triage`: mapeo correcto de cada combinación de flags a la ruta esperada,
  incluyendo la prioridad watermark/screenshot sobre los criterios de MEJORAR;
  fail-open a REGENERATE si la llamada lanza excepción.
- `_describe_triage_rejection`: mensaje correcto por cada flag de rechazo.
- `enhance_photo_classic`: recorte a cuadrado, no lanza excepción con imágenes de
  distintas proporciones/orientación EXIF.
- `generate_image`/`generate_reel`: cada ruta (REJECT/ENHANCE/REGENERATE)
  verificada end-to-end con mocks, confirmando que REJECT y ENHANCE nunca llaman
  a `_generate_scene`, y que ENHANCE en modo reel nunca llama a `_animate_scene`
  (Veo).
- Actualizar los mocks existentes en `test_product_reference_generator.py` y los
  4 sitios de `test_tasks.py` que instancian `ProductReferenceGenerator` para que
  seteen explícitamente la ruta REGENERATE (vía mock de `_triage`) donde el test
  ya asume el comportamiento actual — de lo contrario el mock por defecto de
  `_triage` (si no se define) rompe esos tests al no pasar por `_generate_scene`.
