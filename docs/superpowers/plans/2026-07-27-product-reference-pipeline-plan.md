# Pipeline de producto real como referencia (solo admin) — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir un pipeline nuevo y separado de producción que use una foto real de
producto como referencia para generar una imagen/reel de muestra con IA (Gemini nativo +
Veo, cadena validada hoy con llamadas reales), visible solo para el Plan Admin, reusando
por completo la UI de generación de muestra ya existente.

**Architecture:** 4 tareas secuenciales. Task 1 agrega el campo/modos al modelo. Task 2
construye el generador nuevo (`ProductReferenceGenerator`) de forma aislada y testeable
por sí sola. Task 3 agrega el campo de subida en el formulario + su manejo en la vista.
Task 4 conecta todo dentro de `generate_sample_task`, reusando `calendar_review.html` sin
ningún cambio.

**Tech Stack:** Django, Vertex AI (`gemini-2.5-flash-image` para la escena, Veo para la
animación), pytest + pytest-django, `unittest.mock`, ffmpeg (extracción de frames para QC).

## Global Constraints

- Repo: `/home/anuarbarrera/agente-cosmic/`, checkout normal de `main`, sin rama de feature.
- Todos los comandos de Django/pytest se ejecutan dentro del contenedor:
  `docker compose exec backend <comando>`.
- **No se modifica `image_generator.py` ni `reel_generator.py` de producción** salvo para
  importar sus funciones de módulo ya existentes (`_detect_mime`, `_vertex_client`) — cero
  riesgo para el pipeline que ya usan clientes reales.
- **Cadena técnica validada hoy con llamadas reales — usar tal cual, no reinventar**:
  imagen vía `client.models.generate_content(model='publishers/google/models/gemini-2.5-flash-image', ...)`,
  video vía `client.models.generate_videos(..., image=types.Image(image_bytes=..., mime_type='image/png'), ...)`
  (parámetro `image=` de primer frame clásico). **NUNCA usar `reference_images`/
  `VideoGenerationReferenceImage`/`ASSET`** — se probó hoy y produce casi una edición de la
  foto original, no una escena nueva.
- El auditor de QC (`_validate_scene`) es **obligatorio, no opcional** — se confirmó hoy
  una alucinación real de logo en este mismo mecanismo.
- Solo visible/usable con `Plan.allows_sample_generation=True` (hoy solo Plan Admin) — mismo
  gate ya establecido, no se crea un mecanismo de permisos nuevo.
- El resultado se muestra en `calendar_review.html`, sin ningún cambio a esa plantilla.
- Cada commit usa `GIT_EDITOR=true git commit -m "mensaje"` (nunca heredoc).
- Spec completa con el detalle de la validación técnica de hoy:
  `docs/superpowers/specs/2026-07-27-product-reference-pipeline-design.md`.

---

### Task 1: Modelo — 2 modos nuevos + campo de foto de referencia

**Files:**
- Modify: `core/brand_dna/models.py:29-35` (constantes `MODE_*` y `MODE_CHOICES`)
- Modify: `core/brand_dna/models.py:46` (agregar campo nuevo junto a `logo_file_path`)
- Create: migración de `brand_dna` (generada por `makemigrations`, no escribir a mano)
- Test: `core/brand_dna/tests/test_models.py`

**Interfaces:**
- Produce: `AnalysisJob.MODE_SAMPLE_PRODUCT_IMAGE = 'sample_product_image'`,
  `AnalysisJob.MODE_SAMPLE_PRODUCT_REEL = 'sample_product_reel'`,
  `AnalysisJob.product_reference_image_path` (`CharField(max_length=500, blank=True, default='')`)
  — consumidos por las tareas 3 y 4.

- [ ] **Step 1: Escribir el test que falla**

Revisar primero `core/brand_dna/tests/test_models.py` (leer el archivo completo antes de
escribir, para reusar el estilo/fixtures reales del archivo) y agregar:

```python
def test_analysis_job_product_reference_image_path_defaults_to_empty():
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://tuwebmx.com')
    assert job.product_reference_image_path == ''


def test_analysis_job_has_product_sample_modes():
    assert AnalysisJob.MODE_SAMPLE_PRODUCT_IMAGE == 'sample_product_image'
    assert AnalysisJob.MODE_SAMPLE_PRODUCT_REEL == 'sample_product_reel'
```

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_models.py::test_analysis_job_product_reference_image_path_defaults_to_empty core/brand_dna/tests/test_models.py::test_analysis_job_has_product_sample_modes -v`
Expected: FAIL — `AttributeError: type object 'AnalysisJob' has no attribute 'MODE_SAMPLE_PRODUCT_IMAGE'`.

- [ ] **Step 3: Agregar las constantes y el campo en `core/brand_dna/models.py`**

Cambiar (líneas 29-35):
```python
    MODE_FULL = 'full'
    MODE_SAMPLE_IMAGE = 'sample_image'
    MODE_SAMPLE_REEL = 'sample_reel'
    MODE_CHOICES = [
        (MODE_FULL, 'Calendario completo'),
        (MODE_SAMPLE_IMAGE, 'Muestra: imagen'),
        (MODE_SAMPLE_REEL, 'Muestra: reel'),
    ]
```
por:
```python
    MODE_FULL = 'full'
    MODE_SAMPLE_IMAGE = 'sample_image'
    MODE_SAMPLE_REEL = 'sample_reel'
    MODE_SAMPLE_PRODUCT_IMAGE = 'sample_product_image'
    MODE_SAMPLE_PRODUCT_REEL = 'sample_product_reel'
    MODE_CHOICES = [
        (MODE_FULL, 'Calendario completo'),
        (MODE_SAMPLE_IMAGE, 'Muestra: imagen'),
        (MODE_SAMPLE_REEL, 'Muestra: reel'),
        (MODE_SAMPLE_PRODUCT_IMAGE, 'Muestra: imagen con producto real (solo admin)'),
        (MODE_SAMPLE_PRODUCT_REEL, 'Muestra: reel con producto real (solo admin)'),
    ]
```

Cambiar (línea 46, `logo_file_path`), agregar debajo:
```python
    logo_file_path = models.CharField(max_length=500, blank=True, default='')
```
por:
```python
    logo_file_path = models.CharField(max_length=500, blank=True, default='')
    product_reference_image_path = models.CharField(max_length=500, blank=True, default='')
```

- [ ] **Step 4: Generar y aplicar la migración real**

Run: `docker compose exec backend python manage.py makemigrations brand_dna`
Expected: Django crea 1 archivo nuevo en `core/brand_dna/migrations/` (siguiente número
disponible — usar el nombre que Django genere, no forzar uno distinto).

Run: `docker compose exec backend python manage.py migrate`
Expected: `Applying brand_dna.00XX_... OK`.

- [ ] **Step 5: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_models.py -v`
Expected: PASS — todos, sin regresiones en los tests de modelo existentes.

- [ ] **Step 6: Commit**

```bash
git add core/brand_dna/models.py core/brand_dna/migrations/ core/brand_dna/tests/test_models.py
GIT_EDITOR=true git commit -m "feat(producto-referencia): agregar modos y campo de foto de referencia a AnalysisJob"
```

---

### Task 2: Generador nuevo `ProductReferenceGenerator` (aislado, con QC obligatorio)

**Files:**
- Create: `core/content_pipeline/generators/product_reference_generator.py`
- Test: `core/content_pipeline/tests/test_product_reference_generator.py`

**Interfaces:**
- Consumes: nada de la Task 1 (este módulo no importa `AnalysisJob`, recibe bytes/strings
  directamente — mantiene el generador desacoplado del modelo, mismo patrón que
  `ImageGenerator`/`ReelGenerator`).
- Produce: `ProductReferenceGenerator(bucket_name: str)`, con
  `.generate_image(product_photo_bytes: bytes, business_name: str, filename: str) -> str`
  (URL o `''` si falla/QC rechaza) y
  `.generate_reel(product_photo_bytes: bytes, business_name: str, filename_prefix: str) -> tuple[str, str]`
  (`(video_url, poster_url)`, ambos `''` si falla/QC rechaza) — consumidos por la Task 4.

- [ ] **Step 1: Leer primero los tests existentes de referencia (no asumir contenido)**

Antes de escribir nada, leer completos `core/content_pipeline/tests/test_image_generator.py`
(clases `TestValidateBackground`, `TestUploadToStorage`) y
`core/content_pipeline/tests/test_reel_generator.py` (clase `TestGenerateSingleClip`, método
`_extract_poster_frame` si tiene tests propios) — son el patrón de mocking exacto a replicar
(`patch('...′_vertex_client')`, `mock_vc.return_value.models.generate_content.return_value.text`,
etc.).

- [ ] **Step 2: Escribir los tests que fallan — crear `core/content_pipeline/tests/test_product_reference_generator.py`**

```python
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings

pytestmark = pytest.mark.django_db


class TestGenerateImage:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_url_when_scene_and_qc_succeed(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')

        scene_resp = MagicMock()
        scene_part = MagicMock()
        scene_part.inline_data.data = b'fake-scene-png'
        scene_part.text = None
        scene_resp.candidates = [MagicMock(content=MagicMock(parts=[scene_part]))]

        qc_resp = MagicMock()
        qc_resp.text = '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "ok": true}'

        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc, \
             patch.object(ProductReferenceGenerator, '_upload_to_storage', return_value='https://storage.test/scene.png'):
            mock_vc.return_value.models.generate_content.side_effect = [scene_resp, qc_resp]
            result = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')

        assert result == 'https://storage.test/scene.png'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_empty_string_when_scene_generation_fails(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            result = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert result == ''

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_empty_string_when_qc_rejects_scene(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')

        scene_resp = MagicMock()
        scene_part = MagicMock()
        scene_part.inline_data.data = b'fake-scene-png'
        scene_part.text = None
        scene_resp.candidates = [MagicMock(content=MagicMock(parts=[scene_part]))]

        qc_resp = MagicMock()
        qc_resp.text = '{"has_text": true, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "ok": false}'

        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = [scene_resp, qc_resp]
            result = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')

        assert result == ''


class TestGenerateReel:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        VERTEX_VIDEO_MODEL='veo-3.1-fast-generate-001',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_video_and_poster_url_when_everything_succeeds(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')

        ok_qc = MagicMock()
        ok_qc.text = '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "ok": true}'

        with patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', return_value=True), \
             patch.object(gen, '_animate_scene', return_value=b'video-bytes'), \
             patch.object(gen, '_extract_frame', return_value=b'frame-bytes'), \
             patch.object(gen, '_upload_to_storage', side_effect=['https://storage.test/poster.png', 'https://storage.test/video.mp4']):
            video_url, poster_url = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')

        assert video_url == 'https://storage.test/video.mp4'
        assert poster_url == 'https://storage.test/poster.png'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        VERTEX_VIDEO_MODEL='veo-3.1-fast-generate-001',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_empty_strings_when_scene_fails(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_scene', return_value=None):
            video_url, poster_url = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == '' and poster_url == ''

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        VERTEX_VIDEO_MODEL='veo-3.1-fast-generate-001',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_empty_strings_when_video_generation_fails(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', return_value=True), \
             patch.object(gen, '_animate_scene', return_value=None):
            video_url, poster_url = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == '' and poster_url == ''

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        VERTEX_VIDEO_MODEL='veo-3.1-fast-generate-001',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_empty_strings_when_a_video_frame_fails_qc(self):
        """Reproduce el hallazgo real de hoy: un frame intermedio del video con un
        logo alucinado que no estaba en el frame inicial — debe rechazar el
        resultado completo, no solo advertir."""
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', side_effect=[True, True, False]), \
             patch.object(gen, '_animate_scene', return_value=b'video-bytes'), \
             patch.object(gen, '_extract_frame', return_value=b'frame-bytes'):
            video_url, poster_url = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == '' and poster_url == ''


class TestValidateScene:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_true_when_image_ok(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "ok": true}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._validate_scene(b'fake-png')
        assert result is True

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_false_when_has_text(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"has_text": true, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "ok": false}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._validate_scene(b'fake-png')
        assert result is False

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_true_on_api_error(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            result = gen._validate_scene(b'fake-png')
        assert result is True  # fail-open, mismo criterio que _validate_background
```

- [ ] **Step 3: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_product_reference_generator.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'core.content_pipeline.generators.product_reference_generator'`.

- [ ] **Step 4: Crear `core/content_pipeline/generators/product_reference_generator.py`**

```python
import json
import logging
import os
import re
import subprocess
import tempfile
import time

from django.conf import settings
from google.cloud import storage
from google.genai import types

from core.content_pipeline.generators.image_generator import _detect_mime, _vertex_client
from core.shared.metrics import GCS_OPERATIONS
from core.shared.metrics_utils import track_external_api, record_tokens, vertex_labels

logger = logging.getLogger(__name__)

# gemini-3-pro-image / gemini-3.1-flash-image devuelven 404 (sin acceso) en el
# proyecto de Vertex AI de Cosmic al 2026-07-27 — gemini-2.5-flash-image confirmado
# funcional con llamada real, usar este hasta que se confirme acceso a los mas nuevos.
_REFERENCE_IMAGE_MODEL = 'publishers/google/models/gemini-2.5-flash-image'

_VEO_POLL_TIMEOUT_SECONDS = 300
_VEO_POLL_INTERVAL_SECONDS = 10

_SCENE_PROMPT_TEMPLATE = (
    "Using the product shown in this reference image, generate a brand-new professional "
    "product photograph for {business_name}: a completely new scene, new background, new "
    "lighting and composition — NOT an edit of the input image. Incorporate this exact "
    "product as it appears (same shape, color, texture, any visible branding) as the subject "
    "of the new photograph. Photorealistic, studio-quality, natural lighting."
)

_VIDEO_PROMPT_TEMPLATE = (
    "Cinematic slow push-in on this product photography scene for {business_name}. "
    "Gentle ambient motion (light shifting, soft background movement) — keep the product "
    "and composition stable. Photorealistic, 4k."
)

_QC_PROMPT = (
    "Analyze this image strictly. Reply ONLY with this JSON (no markdown):\n"
    "{\"has_text\": <bool>, \"is_abstract_3d\": <bool>, \"has_screen_content\": <bool>, "
    "\"has_malformed_object\": <bool>, \"has_unrealistic_grounding\": <bool>, \"ok\": <bool>}\n\n"
    "has_text: true if ANY readable letters, words, numbers or text appear ANYWHERE in the image — "
    "including text on signs, labels, books, packaging, walls, or any surface — OR any logo/brand "
    "mark of any kind, even a purely graphic symbol with no letters (real or invented). Even partial "
    "words or blurry text count. Be very strict.\n"
    "is_abstract_3d: true if the image has floating 3D geometric shapes, abstract CGI objects, or surreal renders.\n"
    "has_screen_content: true if any computer monitor, laptop screen, phone screen, TV, or digital display "
    "shows visible content — including websites, text, images, graphics, UI elements, or any non-blank content. "
    "A screen must be completely BLACK or clearly turned off to not count. Be very strict.\n"
    "has_malformed_object: true if any object, tool, instrument, hand, or mechanical item is anatomically or "
    "physically impossible or distorted — wrong number of parts, parts connected incorrectly, missing pieces "
    "a real version of the object would have, or a structurally implausible shape. Examine objects with "
    "multiple connected parts (tools, instruments, hands, machinery) closely. Only flag clear, obvious cases.\n"
    "has_unrealistic_grounding: true if the main subject (person, animal, or product) appears to float, "
    "hover, or is otherwise disconnected from the surface/floor/background it should be resting or "
    "standing on — no visible contact point, no matching contact shadow directly beneath it, wrong "
    "scale or perspective versus the background, or a dynamic mid-air pose (jumping, running) composited "
    "onto a background that implies the subject is stationary. This commonly happens when a subject's "
    "pose doesn't match its new background. Only flag clear, obvious cases where it looks physically wrong.\n"
    "ok: true ONLY if has_text=false AND is_abstract_3d=false AND has_screen_content=false "
    "AND has_malformed_object=false AND has_unrealistic_grounding=false."
)

# Offsets de frames a auditar dentro del video (segundos) — inicio/medio/fin, mismo
# criterio que reproduce el hallazgo real del 2026-07-27 (logo alucinado en un frame
# intermedio que no estaba en el frame inicial).
_QC_FRAME_OFFSETS = (1.0, 4.0, 7.0)


class ProductReferenceGenerator:
    """Pipeline experimental, solo-admin: usa una foto real de producto como
    referencia para que Gemini/Veo generen una escena e reel NUEVOS que la
    incorporen — distinto de BGSWAP (HALLAZGO 65, eliminado), que editaba/rellenaba
    el fondo de la foto original. Cadena validada con llamadas reales el 2026-07-27
    (ver docs/superpowers/specs/2026-07-27-product-reference-pipeline-design.md)."""

    def __init__(self, bucket_name: str):
        self._bucket = bucket_name

    def generate_image(self, product_photo_bytes: bytes, business_name: str, filename: str) -> str:
        scene_bytes = self._generate_scene(product_photo_bytes, business_name)
        if scene_bytes is None:
            return ''
        if not self._validate_scene(scene_bytes):
            logger.warning("ProductReferenceGenerator: QC rechazo la escena generada (generate_image)")
            return ''
        return self._upload_to_storage(scene_bytes, filename, 'image/png', 'product-samples')

    def generate_reel(self, product_photo_bytes: bytes, business_name: str, filename_prefix: str) -> tuple[str, str]:
        scene_bytes = self._generate_scene(product_photo_bytes, business_name)
        if scene_bytes is None:
            return '', ''
        if not self._validate_scene(scene_bytes):
            logger.warning("ProductReferenceGenerator: QC rechazo la escena generada (generate_reel)")
            return '', ''

        video_bytes = self._animate_scene(scene_bytes, business_name)
        if video_bytes is None:
            return '', ''

        for offset in _QC_FRAME_OFFSETS:
            frame_bytes = self._extract_frame(video_bytes, offset_seconds=offset)
            if frame_bytes is not None and not self._validate_scene(frame_bytes):
                logger.warning(f"ProductReferenceGenerator: QC rechazo el frame en {offset}s del video")
                return '', ''

        poster_url = self._upload_to_storage(scene_bytes, f'{filename_prefix}-poster', 'image/png', 'product-samples')
        video_url = self._upload_to_storage(video_bytes, filename_prefix, 'video/mp4', 'product-samples')
        return video_url, poster_url

    def _generate_scene(self, product_photo_bytes: bytes, business_name: str) -> bytes | None:
        try:
            client = _vertex_client()
            mime = _detect_mime(product_photo_bytes)
            image_part = types.Part.from_bytes(data=product_photo_bytes, mime_type=mime)
            prompt = _SCENE_PROMPT_TEMPLATE.format(business_name=business_name)
            with track_external_api('gemini', operation='product_reference_scene'):
                resp = client.models.generate_content(
                    model=_REFERENCE_IMAGE_MODEL,
                    contents=[image_part, prompt],
                    config=types.GenerateContentConfig(response_modalities=['IMAGE', 'TEXT']),
                )
            record_tokens(resp, operation='product_reference_scene', prompt_preview=prompt[:500])
            for part in resp.candidates[0].content.parts:
                if part.inline_data:
                    return part.inline_data.data
            return None
        except Exception as e:
            logger.warning(f"ProductReferenceGenerator._generate_scene fallo: {e}")
            return None

    def _animate_scene(self, scene_bytes: bytes, business_name: str) -> bytes | None:
        try:
            client = _vertex_client()
            prompt = _VIDEO_PROMPT_TEMPLATE.format(business_name=business_name)
            with track_external_api('veo', operation='product_reference_video'):
                operation = client.models.generate_videos(
                    model=settings.VERTEX_VIDEO_MODEL,
                    prompt=prompt,
                    image=types.Image(image_bytes=scene_bytes, mime_type='image/png'),
                    config=types.GenerateVideosConfig(
                        aspect_ratio='9:16', duration_seconds=8, number_of_videos=1, generate_audio=False,
                    ),
                )
            poll_start = time.monotonic()
            while not operation.done:
                if time.monotonic() - poll_start > _VEO_POLL_TIMEOUT_SECONDS:
                    logger.warning("ProductReferenceGenerator._animate_scene: timeout esperando a Veo")
                    return None
                time.sleep(_VEO_POLL_INTERVAL_SECONDS)
                operation = client.operations.get(operation)
            if operation.error:
                logger.warning(f"ProductReferenceGenerator._animate_scene: Veo devolvio error: {operation.error}")
                return None
            generated = operation.result.generated_videos
            if not generated:
                return None
            return generated[0].video.video_bytes
        except Exception as e:
            logger.warning(f"ProductReferenceGenerator._animate_scene fallo: {e}")
            return None

    def _extract_frame(self, video_bytes: bytes, offset_seconds: float) -> bytes | None:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                video_path = os.path.join(tmp, 'video.mp4')
                with open(video_path, 'wb') as f:
                    f.write(video_bytes)
                frame_path = os.path.join(tmp, 'frame.png')
                subprocess.run(
                    ['ffmpeg', '-y', '-ss', str(offset_seconds), '-i', video_path, '-vframes', '1', frame_path],
                    check=True, capture_output=True,
                )
                with open(frame_path, 'rb') as f:
                    return f.read()
        except Exception as e:
            logger.warning(f"ProductReferenceGenerator._extract_frame fallo en offset {offset_seconds}s: {e}")
            return None

    def _validate_scene(self, image_bytes: bytes) -> bool:
        try:
            client = _vertex_client()
            image_part = types.Part.from_bytes(data=image_bytes, mime_type='image/png')
            with track_external_api('gemini', operation='product_reference_qc'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[image_part, _QC_PROMPT],
                    config=types.GenerateContentConfig(labels=vertex_labels()),
                )
            record_tokens(resp, operation='product_reference_qc', prompt_preview=_QC_PROMPT[:500])
            raw = resp.text.strip()
            match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
                return bool(data.get('ok', True))
        except Exception as e:
            logger.warning(f"ProductReferenceGenerator._validate_scene error (assuming ok): {e}")
        return True

    def _upload_to_storage(self, data: bytes, filename: str, content_type: str, folder: str) -> str:
        ext = 'mp4' if content_type == 'video/mp4' else 'png'
        with track_external_api('gcs'):
            client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
            bucket = client.bucket(self._bucket)
            blob = bucket.blob(f'{folder}/{filename}.{ext}')
            blob.upload_from_string(data, content_type=content_type)
        GCS_OPERATIONS.labels(operation='upload').inc()
        return f'{blob.public_url}?v={int(time.time())}'
```

- [ ] **Step 5: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_product_reference_generator.py -v`
Expected: PASS — todos los 10 tests nuevos.

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/generators/product_reference_generator.py core/content_pipeline/tests/test_product_reference_generator.py
GIT_EDITOR=true git commit -m "feat(producto-referencia): generador nuevo ProductReferenceGenerator con QC obligatorio"
```

---

### Task 3: Formulario + vista — subida de foto de producto

**Files:**
- Modify: `core/brand_dna/templates/brand_dna/new_analysis.html:106-121` (radios + campo de subida)
- Modify: `core/brand_dna/templates/brand_dna/new_analysis.html` (bloque `<script>`, líneas 166-218)
- Modify: `core/brand_dna/views.py:150-172` (`analyze_submit`)
- Test: `core/brand_dna/tests/test_views.py`

**Interfaces:**
- Consumes: `AnalysisJob.MODE_SAMPLE_PRODUCT_IMAGE`/`MODE_SAMPLE_PRODUCT_REEL`,
  `AnalysisJob.product_reference_image_path` (Task 1).
- Produce: nada consumido por tareas futuras directamente — la Task 4 lee
  `job.product_reference_image_path` desde BD, no depende de código de esta tarea.

- [ ] **Step 1: Leer primero `core/brand_dna/tests/test_views.py` completo**

Ya se leyeron las líneas 1-150 en esta sesión (fixtures `free_plan`/`user`, patrón de
`test_analyze_submit_saves_sample_mode_when_permitted`) — releer el archivo completo antes
de escribir para confirmar que no cambió y para ver el resto de tests no vistos aún.

- [ ] **Step 2: Escribir los tests que fallan — agregar a `core/brand_dna/tests/test_views.py`**

```python
def _fake_product_photo():
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new('RGB', (10, 10), color='orange').save(buf, format='PNG')
    return SimpleUploadedFile('producto.png', buf.getvalue(), content_type='image/png')


def test_analyze_submit_saves_product_reference_photo_when_permitted(user, free_plan):
    free_plan.allows_sample_generation = True
    free_plan.save(update_fields=['allows_sample_generation'])
    c = Client()
    c.force_login(user)
    with patch('core.brand_dna.views.django_rq'), \
         patch('core.brand_dna.moderation.check_business_legitimacy', return_value=(True, '')), \
         patch('core.brand_dna.views.save_upload') as mock_save:
        c.post('/analizar/', {
            'business_name': 'Gelatinas Marba',
            'business_description': 'Gelatinas artesanales.',
            'generation_mode': 'sample_product_reel',
            'product_reference_photo': _fake_product_photo(),
        })
    job = AnalysisJob.objects.filter(user=user).latest('created_at')
    assert job.generation_mode == AnalysisJob.MODE_SAMPLE_PRODUCT_REEL
    assert job.product_reference_image_path != ''
    mock_save.assert_called_once()


def test_analyze_submit_rejects_invalid_product_reference_photo(user, free_plan):
    free_plan.allows_sample_generation = True
    free_plan.save(update_fields=['allows_sample_generation'])
    c = Client()
    c.force_login(user)
    from django.core.files.uploadedfile import SimpleUploadedFile
    bad_file = SimpleUploadedFile('producto.png', b'no es una imagen real', content_type='image/png')
    with patch('core.brand_dna.views.django_rq'), \
         patch('core.brand_dna.moderation.check_business_legitimacy', return_value=(True, '')):
        response = c.post('/analizar/', {
            'business_name': 'Gelatinas Marba',
            'business_description': 'Gelatinas artesanales.',
            'generation_mode': 'sample_product_reel',
            'product_reference_photo': bad_file,
        })
    assert response.status_code == 200
    assert b'no es una imagen v\xc3\xa1lida' in response.content


def test_analyze_submit_ignores_product_mode_without_permission(user):
    c = Client()
    c.force_login(user)
    with patch('core.brand_dna.views.django_rq'), \
         patch('core.brand_dna.moderation.check_business_legitimacy', return_value=(True, '')):
        c.post('/analizar/', {
            'business_name': 'Gelatinas Marba',
            'business_description': 'Gelatinas artesanales.',
            'generation_mode': 'sample_product_reel',
        })
    job = AnalysisJob.objects.filter(user=user).latest('created_at')
    assert job.generation_mode == AnalysisJob.MODE_FULL
```

- [ ] **Step 3: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_views.py -k product_reference -v`
Expected: FAIL — `job.generation_mode == AnalysisJob.MODE_FULL` en vez de
`MODE_SAMPLE_PRODUCT_REEL` (el modo nuevo todavía no está en `valid_modes`), y
`product_reference_image_path` sigue vacío.

- [ ] **Step 4: Actualizar `valid_modes` y agregar el manejo del upload en `analyze_submit`**

En `core/brand_dna/views.py`, cambiar (línea 150-153):
```python
    requested_mode = request.POST.get('generation_mode', AnalysisJob.MODE_FULL)
    valid_modes = {AnalysisJob.MODE_FULL, AnalysisJob.MODE_SAMPLE_IMAGE, AnalysisJob.MODE_SAMPLE_REEL}
    if requested_mode not in valid_modes or not get_user_plan(request.user).allows_sample_generation:
        requested_mode = AnalysisJob.MODE_FULL
```
por:
```python
    requested_mode = request.POST.get('generation_mode', AnalysisJob.MODE_FULL)
    valid_modes = {
        AnalysisJob.MODE_FULL, AnalysisJob.MODE_SAMPLE_IMAGE, AnalysisJob.MODE_SAMPLE_REEL,
        AnalysisJob.MODE_SAMPLE_PRODUCT_IMAGE, AnalysisJob.MODE_SAMPLE_PRODUCT_REEL,
    }
    if requested_mode not in valid_modes or not get_user_plan(request.user).allows_sample_generation:
        requested_mode = AnalysisJob.MODE_FULL
```

Después del bloque que maneja `request.FILES['logo']` (línea 163-172), agregar:
```python
    if 'product_reference_photo' in request.FILES:
        photo_file = request.FILES['product_reference_photo']
        photo_bytes = photo_file.read()
        if not _validate_image_bytes(photo_bytes):
            return render(request, 'brand_dna/new_analysis.html', {'error': 'La foto del producto no es una imagen válida.'})
        ext = _safe_extension(photo_file.name)
        photo_path = f'uploads/product_ref_{job.id}.{ext}'
        save_upload(photo_bytes, photo_path)
        job.product_reference_image_path = photo_path
        job.save(update_fields=['product_reference_image_path'])
```

- [ ] **Step 5: Actualizar `core/brand_dna/templates/brand_dna/new_analysis.html`**

Cambiar el bloque de radios (líneas 106-121):
```html
      {% if allows_sample_generation %}
      <div class="form-group">
        <label>¿Qué quieres generar?</label>
        <div style="display:flex;flex-direction:column;gap:8px;margin-top:6px;">
          <label style="display:flex;align-items:center;gap:8px;font-weight:normal;">
            <input type="radio" name="generation_mode" value="full" checked> Calendario completo (7 días)
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-weight:normal;">
            <input type="radio" name="generation_mode" value="sample_image"> Solo 1 imagen de muestra
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-weight:normal;">
            <input type="radio" name="generation_mode" value="sample_reel"> Solo 1 reel de muestra
          </label>
        </div>
      </div>
      {% endif %}
```
por:
```html
      {% if allows_sample_generation %}
      <div class="form-group">
        <label>¿Qué quieres generar?</label>
        <div style="display:flex;flex-direction:column;gap:8px;margin-top:6px;">
          <label style="display:flex;align-items:center;gap:8px;font-weight:normal;">
            <input type="radio" name="generation_mode" value="full" checked> Calendario completo (7 días)
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-weight:normal;">
            <input type="radio" name="generation_mode" value="sample_image"> Solo 1 imagen de muestra
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-weight:normal;">
            <input type="radio" name="generation_mode" value="sample_reel"> Solo 1 reel de muestra
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-weight:normal;">
            <input type="radio" name="generation_mode" value="sample_product_image" class="mode-product"> [ADMIN] Imagen con producto real
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-weight:normal;">
            <input type="radio" name="generation_mode" value="sample_product_reel" class="mode-product"> [ADMIN] Reel con producto real
          </label>
        </div>
      </div>
      <div class="form-group" id="productPhotoGroup" style="display:none;">
        <label>Foto real del producto</label>
        <input type="file" name="product_reference_photo" accept="image/*" id="productPhotoInput">
      </div>
      {% endif %}
```

En el mismo archivo, dentro del `<script>` (después de la línea 165, `var MAX_SIDE = 1200;`
y antes de `document.getElementById('analyzeForm').addEventListener(...)`), agregar:
```javascript
    var productModeRadios = document.querySelectorAll('.mode-product');
    var allModeRadios = document.querySelectorAll('[name="generation_mode"]');
    var productPhotoGroup = document.getElementById('productPhotoGroup');
    if (productPhotoGroup) {
      allModeRadios.forEach(function(radio) {
        radio.addEventListener('change', function() {
          var isProductMode = Array.prototype.some.call(productModeRadios, function(r) { return r.checked; });
          productPhotoGroup.style.display = isProductMode ? 'block' : 'none';
        });
      });
    }
```

Cambiar (línea 177, agregar debajo de `logoInput`):
```javascript
      var logoInput = form.querySelector('[name="logo"]');
```
por:
```javascript
      var logoInput = form.querySelector('[name="logo"]');
      var productPhotoInput = form.querySelector('[name="product_reference_photo"]');
```

Cambiar (línea 179-180):
```javascript
      Promise.all([
        logoInput.files.length ? compressAll(logoInput.files) : Promise.resolve([]),
      ]).then(function(results) {
```
por:
```javascript
      Promise.all([
        logoInput.files.length ? compressAll(logoInput.files) : Promise.resolve([]),
        productPhotoInput && productPhotoInput.files.length ? compressAll(productPhotoInput.files) : Promise.resolve([]),
      ]).then(function(results) {
```

Cambiar (línea 194):
```javascript
        results[0].forEach(function(f) { fd.append('logo', f); });
```
por:
```javascript
        results[0].forEach(function(f) { fd.append('logo', f); });
        results[1].forEach(function(f) { fd.append('product_reference_photo', f); });
```

- [ ] **Step 6: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_views.py -v`
Expected: PASS — todos, incluyendo los 3 nuevos, sin regresiones en los tests existentes
de `analyze_submit`.

- [ ] **Step 7: Commit**

```bash
git add core/brand_dna/templates/brand_dna/new_analysis.html core/brand_dna/views.py core/brand_dna/tests/test_views.py
GIT_EDITOR=true git commit -m "feat(producto-referencia): campo de subida de foto de producto en el formulario de analisis"
```

---

### Task 4: Conectar todo en `generate_sample_task`

**Files:**
- Modify: `core/content_pipeline/tasks.py` (imports + rama nueva en `generate_sample_task`)
- Test: `core/content_pipeline/tests/test_tasks.py`

**Interfaces:**
- Consumes: `AnalysisJob.MODE_SAMPLE_PRODUCT_IMAGE`/`MODE_SAMPLE_PRODUCT_REEL`,
  `AnalysisJob.product_reference_image_path` (Task 1);
  `ProductReferenceGenerator.generate_image`/`generate_reel` (Task 2).
- Produce: nada consumido por tareas futuras — última tarea del plan.

- [ ] **Step 1: Escribir los tests que fallan — agregar a `core/content_pipeline/tests/test_tasks.py`**

Agregar fixtures (junto a `job_with_dna_sample_image`/`job_with_dna_sample_reel` ya
existentes, mismo patrón):

```python
@pytest.fixture
def job_with_dna_sample_product_image():
    job = AnalysisJob.objects.create(
        email='t@t.com', business_url='https://tuwebmx.com',
        status=AnalysisJob.STATUS_PROCESSING, stage=AnalysisJob.STAGE_CONTENT, progress=78,
        generation_mode=AnalysisJob.MODE_SAMPLE_PRODUCT_IMAGE,
        product_reference_image_path='uploads/product_ref_test.jpg',
    )
    BrandDNA.objects.create(
        job=job, business_name='Gelatinas Marba', business_url='https://tuwebmx.com',
        description='Gelatinas artesanales', keywords=['gelatinas'], audience='Familias',
        tone='alegre', primary_colors=['#e94560'],
    )
    return job


@pytest.fixture
def job_with_dna_sample_product_reel():
    job = AnalysisJob.objects.create(
        email='t@t.com', business_url='https://tuwebmx.com',
        status=AnalysisJob.STATUS_PROCESSING, stage=AnalysisJob.STAGE_CONTENT, progress=78,
        generation_mode=AnalysisJob.MODE_SAMPLE_PRODUCT_REEL,
        product_reference_image_path='uploads/product_ref_test.jpg',
    )
    BrandDNA.objects.create(
        job=job, business_name='Gelatinas Marba', business_url='https://tuwebmx.com',
        description='Gelatinas artesanales', keywords=['gelatinas'], audience='Familias',
        tone='alegre', primary_colors=['#e94560'],
    )
    return job
```

Agregar los tests:

```python
@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
)
def test_generate_sample_task_product_image_mode_creates_post(job_with_dna_sample_product_image):
    with patch('core.content_pipeline.tasks.read_upload', return_value=b'fake-photo-bytes') as mock_read, \
         patch('core.content_pipeline.tasks.ProductReferenceGenerator') as MockGen:
        MockGen.return_value.generate_image.return_value = 'https://storage.test/product-scene.png'

        from core.content_pipeline.tasks import generate_sample_task
        generate_sample_task(str(job_with_dna_sample_product_image.id))

    mock_read.assert_called_once_with('uploads/product_ref_test.jpg')
    MockGen.return_value.generate_image.assert_called_once()
    call_kwargs = MockGen.return_value.generate_image.call_args
    assert call_kwargs.args[0] == b'fake-photo-bytes'
    assert call_kwargs.args[1] == 'Gelatinas Marba'

    posts = ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna_sample_product_image)
    assert posts.count() == 1
    post = posts.first()
    assert post.format == ContentPost.FORMAT_SINGLE
    assert post.image_url == 'https://storage.test/product-scene.png'
    job_with_dna_sample_product_image.refresh_from_db()
    assert job_with_dna_sample_product_image.status == AnalysisJob.STATUS_DONE


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
)
def test_generate_sample_task_product_reel_mode_creates_post(job_with_dna_sample_product_reel):
    with patch('core.content_pipeline.tasks.read_upload', return_value=b'fake-photo-bytes'), \
         patch('core.content_pipeline.tasks.ProductReferenceGenerator') as MockGen:
        MockGen.return_value.generate_reel.return_value = ('https://storage.test/video.mp4', 'https://storage.test/poster.png')

        from core.content_pipeline.tasks import generate_sample_task
        generate_sample_task(str(job_with_dna_sample_product_reel.id))

    posts = ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna_sample_product_reel)
    assert posts.count() == 1
    post = posts.first()
    assert post.format == ContentPost.FORMAT_REEL
    assert post.video_url == 'https://storage.test/video.mp4'
    assert post.image_url == 'https://storage.test/poster.png'


def test_generate_sample_task_product_mode_fails_without_photo():
    job = AnalysisJob.objects.create(
        email='t@t.com', business_url='https://tuwebmx.com',
        generation_mode=AnalysisJob.MODE_SAMPLE_PRODUCT_IMAGE,
        product_reference_image_path='',
    )
    BrandDNA.objects.create(
        job=job, business_name='Gelatinas Marba', business_url='https://tuwebmx.com',
        description='Gelatinas artesanales', keywords=[], audience='Familias',
        tone='alegre', primary_colors=[],
    )

    from core.content_pipeline.tasks import generate_sample_task
    generate_sample_task(str(job.id))

    job.refresh_from_db()
    assert job.status == AnalysisJob.STATUS_FAILED
    assert 'foto' in job.error_message.lower()
```

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -k product -v`
Expected: FAIL — `ImportError: cannot import name 'ProductReferenceGenerator'` o
`AttributeError` similar (la rama nueva todavía no existe en `generate_sample_task`).

- [ ] **Step 3: Agregar los imports nuevos a `core/content_pipeline/tasks.py`**

Agregar junto a los imports de generadores ya existentes (cerca de
`from core.content_pipeline.generators.reel_generator import ReelGenerator`):
```python
from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
from core.shared.gcs_uploads import read_upload
```

- [ ] **Step 4: Bifurcar `generate_sample_task` para los modos nuevos**

En `core/content_pipeline/tasks.py`, dentro de `generate_sample_task` (línea 97), cambiar:
```python
    try:
        job.update_progress(AnalysisJob.STAGE_CONTENT, 80)

        text_gen = TextGenerator()
```
por:
```python
    try:
        job.update_progress(AnalysisJob.STAGE_CONTENT, 80)

        if job.generation_mode in (AnalysisJob.MODE_SAMPLE_PRODUCT_IMAGE, AnalysisJob.MODE_SAMPLE_PRODUCT_REEL):
            _generate_product_reference_sample(job, brand_dna)
            return

        text_gen = TextGenerator()
```

Agregar la función nueva justo antes de `generate_sample_task` (después de
`_generate_post_media`, línea 46):
```python
def _generate_product_reference_sample(job, brand_dna) -> None:
    if not job.product_reference_image_path:
        job.mark_failed('Modo de producto real seleccionado pero no se subió ninguna foto.')
        return

    photo_bytes = read_upload(job.product_reference_image_path)
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    product_gen = ProductReferenceGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)

    if job.generation_mode == AnalysisJob.MODE_SAMPLE_PRODUCT_REEL:
        video_url, poster_url = product_gen.generate_reel(
            photo_bytes, brand_dna.business_name, filename_prefix=f"{job.id}-product-sample",
        )
        image_url, fmt = poster_url, ContentPost.FORMAT_REEL
    else:
        image_url = product_gen.generate_image(
            photo_bytes, brand_dna.business_name, filename=f"{job.id}-product-sample",
        )
        video_url, fmt = '', ContentPost.FORMAT_SINGLE

    ContentPost.objects.create(
        calendar=calendar,
        day_number=1,
        caption='Prueba: producto real como referencia (solo admin)',
        image_url=image_url,
        image_urls=[],
        video_url=video_url,
        format=fmt,
        suggested_time='09:00',
        hashtags=[],
        scheduled_at=timezone.now(),
    )

    job.stage = AnalysisJob.STAGE_COMPLETE
    job.progress = 100
    job.status = AnalysisJob.STATUS_DONE
    job.save(update_fields=['stage', 'progress', 'status'])
    logger.info(f"Muestra de producto real generada para job {job.id}")
```

- [ ] **Step 5: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -v`
Expected: PASS — todos, incluyendo los 3 nuevos, sin regresiones en
`test_generate_sample_task_*` existentes.

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/tasks.py core/content_pipeline/tests/test_tasks.py
GIT_EDITOR=true git commit -m "feat(producto-referencia): conectar ProductReferenceGenerator en generate_sample_task"
```

---

## Verificación final

Después de completar las 4 tareas, correr la suite completa del proyecto:

Run: `docker compose exec backend pytest core/ -v`
Expected: solo los fallos preexistentes ya documentados (HALLAZGO 80, flake intermitente de
`PasswordSecurityTestCase`, no relacionado a este plan); todo lo demás en verde.

## Pendiente fuera de este plan (no bloquea el cierre)

El script exploratorio de hoy (`core/content_pipeline/management/commands/test_product_reference_pipeline.py`)
queda superado por este plan — considerar eliminarlo en un commit aparte una vez que el
flujo de UI esté funcionando y verificado en vivo por Anuar.
