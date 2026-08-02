# Triage previo en ProductReferenceGenerator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar un paso de triage (RECHAZAR / MEJORAR / REGENERAR) al inicio del
pipeline `ProductReferenceGenerator`, evitando gasto de IA generativa en fotos que
no la necesitan (ya profesionales, o cuyo producto depende del texto impreso) o
que nunca van a poder usarla con éxito (capturas de pantalla, marca de agua
agresiva).

**Architecture:** Nuevo método `_triage()` en `ProductReferenceGenerator` hace 1
llamada barata a Gemini con `response_schema` (mismo patrón que el QC existente),
clasifica en 3 rutas, y `generate_image()`/`generate_reel()` ramifican al inicio
según esa ruta. La ruta MEJORAR usa una nueva función `enhance_photo_classic()`
(recorte + nitidez + autocontraste, sin IA) y anima el reel con `ffmpeg`
(zoompan) en vez de Veo.

**Tech Stack:** Django, `google-genai` (Vertex AI), `pydantic` (`response_schema`),
Pillow (`PIL`), `ffmpeg` (subprocess).

## Global Constraints

- Alcance EXCLUSIVO: `core/content_pipeline/generators/product_reference_generator.py`
  y `core/content_pipeline/image_utils.py` (+ sus tests). NO tocar
  `image_generator.py`, `reel_generator.py`, ni `tasks.py`.
- Interfaz externa sin cambios: `generate_image()` sigue devolviendo
  `tuple[str, str]` (url, reason); `generate_reel()` sigue devolviendo
  `tuple[str, str, str]` (video_url, poster_url, reason).
- NO incluir pasos de `git commit` en ninguna tarea — el trabajo de esta sesión
  se commitea todo junto al final, a pedido explícito de Anuar.
- Seguir el patrón de duplicación deliberada ya usado en el archivo (prompts,
  schemas y lógica de negocio quedan en `product_reference_generator.py`, sin
  extraerlos a un módulo compartido). La única excepción es
  `enhance_photo_classic`, que va en `image_utils.py` por ser una utilidad de
  imagen genérica, igual que `normalize_image` ya existente ahí.
- Todos los mensajes de cara al usuario van en español, mismo tono/estilo que
  los mensajes de error ya existentes en `_describe_qc_failure` y los
  `return '', '...'` del archivo.

---

## Task 1: Lógica de triage (clasificación + ruteo + mensaje de rechazo)

**Files:**
- Modify: `core/content_pipeline/generators/product_reference_generator.py`
- Test: `core/content_pipeline/tests/test_product_reference_generator.py`

**Interfaces:**
- Produces:
  - Constantes de módulo `_TRIAGE_ROUTE_REJECT = 'reject'`,
    `_TRIAGE_ROUTE_ENHANCE = 'enhance'`, `_TRIAGE_ROUTE_REGENERATE = 'regenerate'`
  - `class TriageSchema(BaseModel)` con 5 campos `bool`
  - `_TRIAGE_PROMPT: str`
  - `_route_from_triage(data: dict) -> str` (función standalone)
  - `_describe_triage_rejection(data: dict) -> str` (función standalone)
  - `ProductReferenceGenerator._triage(self, photo_bytes: bytes) -> tuple[str, dict]`
    (método de instancia — todavía NO se llama desde `generate_image`/
    `generate_reel`, eso es Task 3)

Esta tarea NO modifica `generate_image()` ni `generate_reel()`.

- [ ] **Step 1: Escribir los tests de `_route_from_triage` (lógica pura, sin mocks)**

Agregar al final de `core/content_pipeline/tests/test_product_reference_generator.py`
(después de `TestDescribeQcFailure`, que es la última clase del archivo):

```python
class TestRouteFromTriage:
    def test_screenshot_wins_over_everything(self):
        from core.content_pipeline.generators.product_reference_generator import (
            _route_from_triage, _TRIAGE_ROUTE_REJECT,
        )
        data = {
            'is_screenshot_or_ui': True, 'has_aggressive_watermark': False,
            'product_identity_is_text': True, 'has_full_person_subject': True,
            'is_already_professional': True,
        }
        assert _route_from_triage(data) == _TRIAGE_ROUTE_REJECT

    def test_aggressive_watermark_wins_over_enhance_criteria(self):
        # Caso de prioridad de la spec: aunque is_already_professional=True,
        # si has_aggressive_watermark=True tambien, debe RECHAZAR, no MEJORAR.
        from core.content_pipeline.generators.product_reference_generator import (
            _route_from_triage, _TRIAGE_ROUTE_REJECT,
        )
        data = {
            'is_screenshot_or_ui': False, 'has_aggressive_watermark': True,
            'product_identity_is_text': False, 'has_full_person_subject': False,
            'is_already_professional': True,
        }
        assert _route_from_triage(data) == _TRIAGE_ROUTE_REJECT

    def test_product_identity_is_text_routes_to_enhance(self):
        from core.content_pipeline.generators.product_reference_generator import (
            _route_from_triage, _TRIAGE_ROUTE_ENHANCE,
        )
        data = {
            'is_screenshot_or_ui': False, 'has_aggressive_watermark': False,
            'product_identity_is_text': True, 'has_full_person_subject': False,
            'is_already_professional': False,
        }
        assert _route_from_triage(data) == _TRIAGE_ROUTE_ENHANCE

    def test_full_person_subject_routes_to_enhance(self):
        from core.content_pipeline.generators.product_reference_generator import (
            _route_from_triage, _TRIAGE_ROUTE_ENHANCE,
        )
        data = {
            'is_screenshot_or_ui': False, 'has_aggressive_watermark': False,
            'product_identity_is_text': False, 'has_full_person_subject': True,
            'is_already_professional': False,
        }
        assert _route_from_triage(data) == _TRIAGE_ROUTE_ENHANCE

    def test_already_professional_routes_to_enhance(self):
        from core.content_pipeline.generators.product_reference_generator import (
            _route_from_triage, _TRIAGE_ROUTE_ENHANCE,
        )
        data = {
            'is_screenshot_or_ui': False, 'has_aggressive_watermark': False,
            'product_identity_is_text': False, 'has_full_person_subject': False,
            'is_already_professional': True,
        }
        assert _route_from_triage(data) == _TRIAGE_ROUTE_ENHANCE

    def test_no_flags_set_routes_to_regenerate(self):
        from core.content_pipeline.generators.product_reference_generator import (
            _route_from_triage, _TRIAGE_ROUTE_REGENERATE,
        )
        data = {
            'is_screenshot_or_ui': False, 'has_aggressive_watermark': False,
            'product_identity_is_text': False, 'has_full_person_subject': False,
            'is_already_professional': False,
        }
        assert _route_from_triage(data) == _TRIAGE_ROUTE_REGENERATE


class TestDescribeTriageRejection:
    def test_screenshot_message(self):
        from core.content_pipeline.generators.product_reference_generator import _describe_triage_rejection
        msg = _describe_triage_rejection({'is_screenshot_or_ui': True})
        assert 'captura de pantalla' in msg.lower()

    def test_watermark_message(self):
        from core.content_pipeline.generators.product_reference_generator import _describe_triage_rejection
        msg = _describe_triage_rejection({'has_aggressive_watermark': True})
        assert 'marca de agua' in msg.lower()

    def test_generic_message_when_no_flags(self):
        from core.content_pipeline.generators.product_reference_generator import _describe_triage_rejection
        msg = _describe_triage_rejection({})
        assert msg == 'La foto no pudo procesarse. Intenta con otra foto.'


class TestTriage:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_calls_gemini_and_returns_route_from_response(self):
        from core.content_pipeline.generators.product_reference_generator import (
            ProductReferenceGenerator, _TRIAGE_ROUTE_REGENERATE,
        )
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = (
                '{"is_screenshot_or_ui": false, "has_aggressive_watermark": false, '
                '"product_identity_is_text": false, "has_full_person_subject": false, '
                '"is_already_professional": false}'
            )
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            route, data = gen._triage(b'fake-photo-bytes')
        assert route == _TRIAGE_ROUTE_REGENERATE
        assert data.get('is_screenshot_or_ui') is False

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_fails_open_to_regenerate_on_exception(self):
        from core.content_pipeline.generators.product_reference_generator import (
            ProductReferenceGenerator, _TRIAGE_ROUTE_REGENERATE,
        )
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            route, data = gen._triage(b'fake-photo-bytes')
        assert route == _TRIAGE_ROUTE_REGENERATE
        assert data == {}
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `pytest core/content_pipeline/tests/test_product_reference_generator.py::TestRouteFromTriage core/content_pipeline/tests/test_product_reference_generator.py::TestDescribeTriageRejection core/content_pipeline/tests/test_product_reference_generator.py::TestTriage -v`

Expected: FAIL — `ImportError: cannot import name '_route_from_triage'` (o
equivalente para `_describe_triage_rejection`/`_TRIAGE_ROUTE_REGENERATE`).

- [ ] **Step 3: Implementar las constantes, el schema, el prompt y las funciones de ruteo/mensaje**

En `core/content_pipeline/generators/product_reference_generator.py`, justo
después de la definición de `_QC_FRAME_OFFSETS` (línea 99 del archivo actual) y
ANTES de `def _describe_qc_failure(data: dict) -> str:`, insertar:

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


def _route_from_triage(data: dict) -> str:
    if data.get('is_screenshot_or_ui'):
        return _TRIAGE_ROUTE_REJECT
    if data.get('has_aggressive_watermark'):
        return _TRIAGE_ROUTE_REJECT
    if (data.get('product_identity_is_text') or data.get('has_full_person_subject')
            or data.get('is_already_professional')):
        return _TRIAGE_ROUTE_ENHANCE
    return _TRIAGE_ROUTE_REGENERATE


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

Estos 4 nombres (`_route_from_triage`, `_describe_triage_rejection`,
`TriageSchema`, `_TRIAGE_PROMPT`) y las 3 constantes de ruta quedan a nivel de
módulo, mismo patrón que `_QC_PROMPT`/`ProductQCSchema`/`_describe_qc_failure`
que ya existen en el archivo.

- [ ] **Step 4: Implementar el método `_triage` en la clase `ProductReferenceGenerator`**

Dentro de `class ProductReferenceGenerator:`, agregar este método justo después
de `def __init__(self, bucket_name: str):` y ANTES de `def generate_image(...)`:

```python
    def _triage(self, photo_bytes: bytes) -> tuple[str, dict]:
        try:
            client = _vertex_client()
            mime = _detect_mime(photo_bytes)
            image_part = types.Part.from_bytes(data=photo_bytes, mime_type=mime)
            with track_external_api('gemini', operation='product_reference_triage'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[image_part, _TRIAGE_PROMPT],
                    config=types.GenerateContentConfig(
                        labels=vertex_labels(),
                        response_mime_type="application/json",
                        response_schema=TriageSchema,
                    ),
                )
            record_tokens(resp, operation='product_reference_triage',
                          prompt_preview=_TRIAGE_PROMPT[:500], response_preview=resp.text[:500] if resp.text else '')
            data = json.loads(resp.text)
            route = _route_from_triage(data)
            if route != _TRIAGE_ROUTE_REGENERATE:
                logger.info(f"ProductReferenceGenerator: triage -> {route} | {data}")
            return route, data
        except Exception as e:
            logger.warning(f"ProductReferenceGenerator._triage error (fail-open a regenerate): {e}")
        return _TRIAGE_ROUTE_REGENERATE, {}
```

No modificar `generate_image` ni `generate_reel` en este paso — `_triage` queda
definido pero sin llamarse todavía (Task 3 hace la integración).

- [ ] **Step 5: Correr los tests para verificar que pasan**

Run: `pytest core/content_pipeline/tests/test_product_reference_generator.py -v`

Expected: PASS — las 304+ líneas de tests preexistentes del archivo (que no
tocan `_triage`) siguen pasando sin cambios, más los tests nuevos de
`TestRouteFromTriage`, `TestDescribeTriageRejection` y `TestTriage`.

---

## Task 2: `enhance_photo_classic` — mejora clásica sin IA generativa

**Files:**
- Modify: `core/content_pipeline/image_utils.py`
- Test: `core/content_pipeline/tests/test_image_utils.py`

**Interfaces:**
- Consumes: nada de Task 1 (independiente).
- Produces: `enhance_photo_classic(image_bytes: bytes) -> bytes`, importable como
  `from core.content_pipeline.image_utils import enhance_photo_classic` — Task 3
  la usa dentro de `ProductReferenceGenerator`.

- [ ] **Step 1: Escribir los tests de `enhance_photo_classic`**

Reemplazar la línea de import en
`core/content_pipeline/tests/test_image_utils.py`:

```python
from core.content_pipeline.image_utils import normalize_image
```

por:

```python
from core.content_pipeline.image_utils import normalize_image, enhance_photo_classic
```

Y agregar al final del archivo (después de la última clase `TestNormalizeImage`):

```python
class TestEnhancePhotoClassic:
    def test_crops_rectangular_image_to_square(self):
        img = Image.new('RGB', (200, 100), color='green')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        result = enhance_photo_classic(buf.getvalue())
        out = Image.open(io.BytesIO(result))
        assert out.width == out.height == 100

    def test_square_image_keeps_dimensions(self):
        img = Image.new('RGB', (150, 150), color='blue')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        result = enhance_photo_classic(buf.getvalue())
        out = Image.open(io.BytesIO(result))
        assert out.width == out.height == 150

    def test_output_is_valid_png(self):
        img = Image.new('RGB', (120, 80), color='red')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        result = enhance_photo_classic(buf.getvalue())
        out = Image.open(io.BytesIO(result))
        assert out.format == 'PNG'

    def test_returns_original_bytes_when_processing_fails(self):
        garbage = b'not-a-real-image'
        result = enhance_photo_classic(garbage)
        assert result == garbage
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `pytest core/content_pipeline/tests/test_image_utils.py -v`

Expected: FAIL con `ImportError: cannot import name 'enhance_photo_classic'`.

- [ ] **Step 3: Implementar `enhance_photo_classic`**

En `core/content_pipeline/image_utils.py`, cambiar la línea de import:

```python
from PIL import Image, ImageOps
```

por:

```python
from PIL import Image, ImageOps, ImageFilter
```

Y agregar al final del archivo:

```python
def enhance_photo_classic(image_bytes: bytes) -> bytes:
    """Recorte 1:1 centrado + nitidez suave + autocontraste — sin IA generativa.

    Usado por la ruta MEJORAR del triage de ProductReferenceGenerator: la foto
    original ya es válida, solo necesita quedar lista para publicarse.
    """
    try:
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
    except Exception as e:
        logger.warning(f"enhance_photo_classic falló (usando original): {e}")
        return image_bytes
```

- [ ] **Step 4: Correr los tests para verificar que pasan**

Run: `pytest core/content_pipeline/tests/test_image_utils.py -v`

Expected: PASS — los tests preexistentes de `TestNormalizeImage` siguen pasando
sin cambios, más los 4 nuevos de `TestEnhancePhotoClassic`.

---

## Task 3: Integración — wiring del triage + animación ffmpeg para la ruta MEJORAR

**Files:**
- Modify: `core/content_pipeline/generators/product_reference_generator.py`
- Test: `core/content_pipeline/tests/test_product_reference_generator.py`

**Interfaces:**
- Consumes: `_triage`, `_route_from_triage`, `_TRIAGE_ROUTE_REJECT`,
  `_TRIAGE_ROUTE_ENHANCE`, `_TRIAGE_ROUTE_REGENERATE`, `_describe_triage_rejection`
  (Task 1); `enhance_photo_classic` de `core.content_pipeline.image_utils` (Task 2).
- Produces: `ProductReferenceGenerator._animate_still_to_clip(self, image_bytes: bytes) -> bytes | None`;
  `generate_image()`/`generate_reel()` reescritos con las 3 rutas — la firma
  externa de ambos NO cambia.

### Parte A — Actualizar los 10 tests existentes para que sigan probando la ruta REGENERAR

Estos 10 tests llaman a `gen.generate_image(...)`/`gen.generate_reel(...)`
directo sin mockear `_triage` (que hoy no existe). Una vez que Parte B conecte
`_triage` como primer paso real, hay que fijar la ruta a `'regenerate'` en cada
uno para que seguir probando exactamente lo que probaban antes, sin acoplarse al
comportamiento interno de `_triage`.

- [ ] **Step 1: Aplicar los 10 parches, uno por test, en `test_product_reference_generator.py`**

En `class TestGenerateImage`, agregar `patch.object(gen, '_triage', return_value=('regenerate', {})), \` como la PRIMERA línea de cada bloque `with` (antes del resto de patches ya existentes) en estos 5 tests:

1. `test_returns_url_when_scene_and_qc_succeed`:

```python
        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc, \
             patch.object(ProductReferenceGenerator, '_upload_to_storage', return_value='https://storage.test/scene.png'):
            mock_vc.return_value.models.generate_content.side_effect = [scene_resp, qc_resp]
            url, reason = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
```

2. `test_returns_empty_string_when_scene_generation_fails`:

```python
        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            url, reason = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
```

3. `test_returns_watermark_message_when_qc_rejects_for_text`:

```python
        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = [scene_resp, qc_resp]
            url, reason = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
```

4. `test_returns_screenshot_message_when_text_and_screen_content`:

```python
        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = [scene_resp, qc_resp]
            url, reason = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
```

5. `test_generate_image_returns_empty_string_when_upload_fails`:

```python
        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', return_value=(True, {'ok': True})), \
             patch.object(gen, '_upload_to_storage', side_effect=Exception('GCS down')):
            url, reason = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
```

En `class TestGenerateReel`, mismo criterio en estos 5 tests:

6. `test_returns_video_and_poster_url_when_everything_succeeds`:

```python
        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', return_value=(True, {'ok': True})), \
             patch.object(gen, '_animate_scene', return_value=b'video-bytes'), \
             patch.object(gen, '_extract_frame', return_value=b'frame-bytes'), \
             patch.object(gen, '_upload_to_storage', side_effect=['https://storage.test/poster.png', 'https://storage.test/video.mp4']):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
```

7. `test_returns_empty_strings_when_scene_fails`:

```python
        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch.object(gen, '_generate_scene', return_value=None):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
```

8. `test_returns_empty_strings_when_video_generation_fails`:

```python
        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', return_value=(True, {'ok': True})), \
             patch.object(gen, '_animate_scene', return_value=None):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
```

9. `test_returns_empty_strings_when_a_video_frame_fails_qc`:

```python
        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', side_effect=[
                 (True, {'ok': True}), (True, {'ok': True}), (False, {'has_text': True, 'ok': False}),
             ]), \
             patch.object(gen, '_animate_scene', return_value=b'video-bytes'), \
             patch.object(gen, '_extract_frame', return_value=b'frame-bytes'):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
```

10. `test_returns_empty_strings_when_frame_extraction_fails`:

```python
        with patch.object(gen, '_triage', return_value=('regenerate', {})), \
             patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', return_value=(True, {'ok': True})), \
             patch.object(gen, '_animate_scene', return_value=b'video-bytes'), \
             patch.object(gen, '_extract_frame', return_value=None):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
```

No cambiar el cuerpo del `with` en ningún otro aspecto — solo agregar la línea
de `patch.object(gen, '_triage', ...)` al principio de cada uno, y ajustar la
indentación (`\`) de continuación de las líneas siguientes.

- [ ] **Step 2: Confirmar que estos 10 tests aún FALLAN (correcto en este punto — `_triage` todavía no se llama desde `generate_image`/`generate_reel`, así que el mock queda sin usar, lo cual no rompe nada, pero antes de la Parte B el objetivo es solo dejar los tests listos)**

Este paso es informativo: correr
`pytest core/content_pipeline/tests/test_product_reference_generator.py::TestGenerateImage core/content_pipeline/tests/test_product_reference_generator.py::TestGenerateReel -v`
debe seguir dando PASS igual que antes (el mock de `_triage` que se agregó no
se usa todavía porque `generate_image`/`generate_reel` no lo llaman hasta la
Parte B). Si algo falla aquí, revisar que el `patch.object` se haya insertado
sin romper la sintaxis del `with`.

### Parte B — Wiring del triage y la ruta MEJORAR en `generate_image`/`generate_reel`

- [ ] **Step 3: Escribir los tests nuevos de las rutas RECHAZAR y MEJORAR**

Agregar estos 3 tests al final de `class TestGenerateImage` (después de
`test_generate_image_returns_empty_string_when_upload_fails`):

```python
    def test_returns_reject_message_without_calling_generate_scene(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_triage', return_value=('reject', {'is_screenshot_or_ui': True})), \
             patch.object(gen, '_generate_scene') as mock_generate_scene:
            url, reason = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert url == ''
        assert 'captura de pantalla' in reason.lower()
        mock_generate_scene.assert_not_called()

    def test_enhance_route_uploads_enhanced_photo_without_calling_generate_scene(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_triage', return_value=('enhance', {'is_already_professional': True})), \
             patch('core.content_pipeline.generators.product_reference_generator.enhance_photo_classic',
                   return_value=b'enhanced-bytes') as mock_enhance, \
             patch.object(gen, '_generate_scene') as mock_generate_scene, \
             patch.object(gen, '_upload_to_storage', return_value='https://storage.test/enhanced.png') as mock_upload:
            url, reason = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert url == 'https://storage.test/enhanced.png'
        assert reason == ''
        mock_generate_scene.assert_not_called()
        mock_enhance.assert_called_once_with(b'fake-photo-bytes')
        mock_upload.assert_called_once_with(b'enhanced-bytes', 'job123-sample', 'image/png', 'product-samples')
```

Agregar estos 3 tests al final de `class TestGenerateReel` (después de
`test_returns_empty_strings_when_frame_extraction_fails`):

```python
    def test_returns_reject_message_without_calling_generate_scene(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_triage', return_value=('reject', {'has_aggressive_watermark': True})), \
             patch.object(gen, '_generate_scene') as mock_generate_scene:
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == '' and poster_url == ''
        assert 'marca de agua' in reason.lower()
        mock_generate_scene.assert_not_called()

    def test_enhance_route_animates_with_ffmpeg_without_calling_veo(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_triage', return_value=('enhance', {'has_full_person_subject': True})), \
             patch('core.content_pipeline.generators.product_reference_generator.enhance_photo_classic',
                   return_value=b'enhanced-bytes'), \
             patch.object(gen, '_generate_scene') as mock_generate_scene, \
             patch.object(gen, '_animate_scene') as mock_animate_scene, \
             patch.object(gen, '_animate_still_to_clip', return_value=b'ffmpeg-video-bytes') as mock_animate_clip, \
             patch.object(gen, '_upload_to_storage', side_effect=[
                 'https://storage.test/poster.png', 'https://storage.test/video.mp4',
             ]):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == 'https://storage.test/video.mp4'
        assert poster_url == 'https://storage.test/poster.png'
        assert reason == ''
        mock_generate_scene.assert_not_called()
        mock_animate_scene.assert_not_called()
        mock_animate_clip.assert_called_once_with(b'enhanced-bytes')

    def test_enhance_route_returns_message_when_ffmpeg_animation_fails(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_triage', return_value=('enhance', {'is_already_professional': True})), \
             patch('core.content_pipeline.generators.product_reference_generator.enhance_photo_classic',
                   return_value=b'enhanced-bytes'), \
             patch.object(gen, '_animate_still_to_clip', return_value=None):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == '' and poster_url == ''
        assert 'foto mejorada' in reason.lower()
```

- [ ] **Step 4: Correr los tests nuevos para verificar que fallan**

Run: `pytest core/content_pipeline/tests/test_product_reference_generator.py -v -k "reject or enhance_route"`

Expected: FAIL — `generate_image`/`generate_reel` todavía no llaman a
`_triage`, así que `_generate_scene` SÍ se llama (con datos falsos que no
soportan estos escenarios) y las aserciones de ruta fallan.

- [ ] **Step 5: Agregar el import de `enhance_photo_classic` y el método `_animate_still_to_clip`**

En `core/content_pipeline/generators/product_reference_generator.py`, agregar
esta línea al bloque de imports existente (junto a la línea
`from core.content_pipeline.generators.image_generator import _detect_mime, _vertex_client`):

```python
from core.content_pipeline.image_utils import enhance_photo_classic
```

Agregar el método `_animate_still_to_clip` dentro de la clase
`ProductReferenceGenerator`, justo después de `_animate_scene` y ANTES de
`_extract_frame`:

```python
    def _animate_still_to_clip(self, image_bytes: bytes) -> bytes | None:
        try:
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
        except Exception as e:
            logger.warning(f"ProductReferenceGenerator._animate_still_to_clip fallo: {e}")
            return None
```

`_animate_still_to_clip` devuelve `None` en vez de propagar la excepción —
mismo patrón que `_generate_scene`/`_animate_scene`/`_extract_frame`, que ya
capturan sus propias excepciones y dejan que `generate_image`/`generate_reel`
decidan el mensaje al usuario. 8 segundos / 1080x1920 / 24fps son los mismos
valores que usa hoy `_animate_scene` (Veo `duration_seconds=8`,
`aspect_ratio='9:16'`).

- [ ] **Step 6: Reescribir `generate_image` y `generate_reel` con las 3 rutas**

Reemplazar el método `generate_image` completo por:

```python
    def generate_image(self, product_photo_bytes: bytes, business_name: str, filename: str) -> tuple[str, str]:
        try:
            route, triage_data = self._triage(product_photo_bytes)
            if route == _TRIAGE_ROUTE_REJECT:
                return '', _describe_triage_rejection(triage_data)
            if route == _TRIAGE_ROUTE_ENHANCE:
                enhanced_bytes = enhance_photo_classic(product_photo_bytes)
                url = self._upload_to_storage(enhanced_bytes, filename, 'image/png', 'product-samples')
                return url, ''

            scene_bytes = self._generate_scene(product_photo_bytes, business_name)
            if scene_bytes is None:
                return '', 'No se pudo generar la escena a partir de la foto (el modelo se nego a procesarla).'
            ok, qc_data = self._validate_scene(scene_bytes)
            if not ok:
                logger.warning("ProductReferenceGenerator: QC rechazo la escena generada (generate_image)")
                return '', _describe_qc_failure(qc_data)
            url = self._upload_to_storage(scene_bytes, filename, 'image/png', 'product-samples')
            return url, ''
        except Exception as e:
            logger.warning(f"ProductReferenceGenerator.generate_image fallo: {e}")
            return '', 'Ocurrio un error inesperado generando la imagen. Vuelve a intentar.'
```

Reemplazar el método `generate_reel` completo por:

```python
    def generate_reel(self, product_photo_bytes: bytes, business_name: str, filename_prefix: str) -> tuple[str, str, str]:
        try:
            route, triage_data = self._triage(product_photo_bytes)
            if route == _TRIAGE_ROUTE_REJECT:
                return '', '', _describe_triage_rejection(triage_data)
            if route == _TRIAGE_ROUTE_ENHANCE:
                enhanced_bytes = enhance_photo_classic(product_photo_bytes)
                video_bytes = self._animate_still_to_clip(enhanced_bytes)
                if video_bytes is None:
                    return '', '', 'No se pudo generar el video a partir de la foto mejorada. Vuelve a intentar.'
                poster_url = self._upload_to_storage(enhanced_bytes, f'{filename_prefix}-poster', 'image/png', 'product-samples')
                video_url = self._upload_to_storage(video_bytes, filename_prefix, 'video/mp4', 'product-samples')
                return video_url, poster_url, ''

            scene_bytes = self._generate_scene(product_photo_bytes, business_name)
            if scene_bytes is None:
                return '', '', 'No se pudo generar la escena a partir de la foto (el modelo se nego a procesarla).'
            ok, qc_data = self._validate_scene(scene_bytes)
            if not ok:
                logger.warning("ProductReferenceGenerator: QC rechazo la escena generada (generate_reel)")
                return '', '', _describe_qc_failure(qc_data)

            video_bytes = self._animate_scene(scene_bytes)
            if video_bytes is None:
                return '', '', 'No se pudo generar el video a partir de la escena. Vuelve a intentar.'

            for offset in _QC_FRAME_OFFSETS:
                frame_bytes = self._extract_frame(video_bytes, offset_seconds=offset)
                if frame_bytes is None:
                    logger.warning(f"ProductReferenceGenerator: no se pudo extraer el frame en {offset}s para QC — se rechaza el resultado")
                    return '', '', 'No se pudo verificar uno de los frames del video generado. Vuelve a intentar.'
                ok, qc_data = self._validate_scene(frame_bytes)
                if not ok:
                    logger.warning(f"ProductReferenceGenerator: QC rechazo el frame en {offset}s del video")
                    return '', '', _describe_qc_failure(qc_data)

            poster_url = self._upload_to_storage(scene_bytes, f'{filename_prefix}-poster', 'image/png', 'product-samples')
            video_url = self._upload_to_storage(video_bytes, filename_prefix, 'video/mp4', 'product-samples')
            return video_url, poster_url, ''
        except Exception as e:
            logger.warning(f"ProductReferenceGenerator.generate_reel fallo: {e}")
            return '', '', 'Ocurrio un error inesperado generando el reel. Vuelve a intentar.'
```

La parte de la ruta REGENERAR queda idéntica al código actual — solo se le
agregó el triage y el `if`/`if` de las otras 2 rutas antes.

- [ ] **Step 7: Correr TODO el archivo de test para verificar que pasa completo**

Run: `pytest core/content_pipeline/tests/test_product_reference_generator.py -v`

Expected: PASS — los 10 tests actualizados de la Parte A, los 6 tests nuevos de
la Parte B (3 en `TestGenerateImage`, 3 en `TestGenerateReel`), y todos los
tests de `TestValidateScene`/`TestDescribeQcFailure`/`TestRouteFromTriage`/
`TestDescribeTriageRejection`/`TestTriage` de Task 1, todos en verde.

- [ ] **Step 8: Correr también `test_image_utils.py` para confirmar que Task 2 sigue intacta**

Run: `pytest core/content_pipeline/tests/test_image_utils.py core/content_pipeline/tests/test_product_reference_generator.py -v`

Expected: PASS — ningún test roto entre los 2 archivos que este plan toca.

---

## Self-Review (verificado antes de entregar el plan)

**Cobertura del spec:** las 4 rutas (RECHAZAR/MEJORAR/REGENERAR + fail-open) están
cubiertas en Task 1; `enhance_photo_classic` en Task 2; el wiring completo +
`_animate_still_to_clip` + actualización de los 10 tests existentes + 6 tests
nuevos de ruta en Task 3. El único ajuste respecto al código de ejemplo de la
spec: `_animate_still_to_clip` captura su propia excepción y devuelve
`bytes | None` en vez de dejar que `ffmpeg` propague — así `generate_reel` puede
dar el mensaje específico de fallo de animación que la spec pide en prosa
("Si `ffmpeg` falla... retorna... 'No se pudo generar el video a partir de la
foto mejorada'"), siguiendo el mismo patrón que ya usan
`_generate_scene`/`_animate_scene`/`_extract_frame` en este archivo.

**Placeholders:** ninguno — cada paso tiene el código completo a escribir.

**Consistencia de tipos:** `_triage` devuelve `tuple[str, dict]` en Task 1 y se
consume así en Task 3; `enhance_photo_classic` devuelve `bytes` en Task 2 y se
usa así en Task 3; `_animate_still_to_clip` devuelve `bytes | None` y Task 3 lo
revisa con `if video_bytes is None`. Las 3 constantes de ruta se definen una
sola vez en Task 1 y se importan/usan igual en Task 3.
