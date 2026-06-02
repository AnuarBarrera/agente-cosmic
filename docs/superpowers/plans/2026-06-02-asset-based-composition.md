# Asset-Based Composition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace single Imagen 3 call with a 3-layer pipeline: (1) clean background with negative space, (2) 3D text asset on magenta chroma key, (3) PIL compositing after Gemini analyzes placement — eliminating AI-hallucinated text from images.

**Architecture:** `ImageGenerator.generate()` now orchestrates `_layered_pipeline()` which makes 2 Imagen 3 calls (background + text asset on magenta), 1 Gemini multimodal call for placement analysis, then PIL chroma key removal + compositing. Text asset generation retries up to 2 times independently without regenerating the background. A new standalone `layer_composer.py` module handles chroma key removal and PIL compositing, keeping `image_generator.py` focused on API orchestration.

**Tech Stack:** Pillow 12.2.0, numpy 2.4.6, `google.genai` SDK (Vertex AI), `imagen-3.0-generate-001`, `gemini-2.5-flash`, Django settings, Python 3.12

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `core/content_pipeline/generators/layer_composer.py` | CREATE | Chroma key removal + PIL layer compositing |
| `core/content_pipeline/generators/image_generator.py` | MODIFY | New private methods + `_layered_pipeline()` + swap in `generate()` |
| `core/content_pipeline/tests/test_layer_composer.py` | CREATE | Tests for layer_composer module |
| `core/content_pipeline/tests/test_image_generator.py` | MODIFY | Update mocks for changed `generate()`, add new method tests |

Files NOT touched: `tasks.py`, `views.py` — they call `image_gen.generate(...)` which keeps the same signature.

---

### Task 1: LayeredImageComposer — layer_composer.py

**Files:**
- Create: `core/content_pipeline/generators/layer_composer.py`
- Create: `core/content_pipeline/tests/test_layer_composer.py`

- [ ] **Step 1: Write the failing tests**

```python
# core/content_pipeline/tests/test_layer_composer.py
import io
import pytest
import numpy as np
from PIL import Image


def _solid_image_bytes(color: tuple, size=(64, 64)) -> bytes:
    img = Image.new('RGB', size, color)
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def _magenta_with_white_center(size=64) -> bytes:
    """Image with magenta background and a white 20x20 center square."""
    img = Image.new('RGB', (size, size), (255, 0, 255))
    center = size // 2
    half = 10
    for y in range(center - half, center + half):
        for x in range(center - half, center + half):
            img.putpixel((x, y), (255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


class TestRemoveChromaKey:
    def test_full_magenta_image_becomes_transparent(self):
        from core.content_pipeline.generators.layer_composer import remove_chroma_key
        magenta_bytes = _solid_image_bytes((255, 0, 255))
        result = remove_chroma_key(magenta_bytes)
        arr = np.array(result)
        assert arr.shape[2] == 4  # RGBA
        assert arr[:, :, 3].max() == 0  # all pixels transparent

    def test_non_magenta_pixels_keep_opacity(self):
        from core.content_pipeline.generators.layer_composer import remove_chroma_key
        asset_bytes = _magenta_with_white_center()
        result = remove_chroma_key(asset_bytes)
        arr = np.array(result)
        center = 32
        # The white center pixels should remain opaque
        assert arr[center, center, 3] == 255

    def test_returns_rgba_image(self):
        from core.content_pipeline.generators.layer_composer import remove_chroma_key
        result = remove_chroma_key(_solid_image_bytes((100, 150, 200)))
        assert result.mode == 'RGBA'


class TestCompositeLayers:
    def test_composite_returns_valid_png(self):
        from core.content_pipeline.generators.layer_composer import composite_layers
        bg_bytes = _solid_image_bytes((30, 30, 60), size=(128, 128))
        text_bytes = _magenta_with_white_center(size=64)
        result = composite_layers(bg_bytes, text_bytes, x=0.1, y=0.5, width=0.5)
        out = Image.open(io.BytesIO(result))
        assert out.size == (128, 128)
        assert out.mode == 'RGB'

    def test_composite_with_rotation_still_valid(self):
        from core.content_pipeline.generators.layer_composer import composite_layers
        bg_bytes = _solid_image_bytes((30, 30, 60), size=(128, 128))
        text_bytes = _magenta_with_white_center(size=64)
        result = composite_layers(bg_bytes, text_bytes, x=0.1, y=0.5, width=0.5, rotation_deg=5.0)
        out = Image.open(io.BytesIO(result))
        assert out.size == (128, 128)

    def test_composite_clamps_x_y_within_image(self):
        from core.content_pipeline.generators.layer_composer import composite_layers
        bg_bytes = _solid_image_bytes((30, 30, 60), size=(128, 128))
        text_bytes = _magenta_with_white_center(size=64)
        # Should not raise even with x=0.95 (text asset goes off edge)
        result = composite_layers(bg_bytes, text_bytes, x=0.95, y=0.95, width=0.8)
        assert len(result) > 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /home/anuarbarrera/agente-cosmic
docker exec agente-cosmic-backend-1 python -m pytest core/content_pipeline/tests/test_layer_composer.py -v 2>&1 | head -30
```

Expected: ImportError or ModuleNotFoundError — `layer_composer` does not exist yet.

- [ ] **Step 3: Create layer_composer.py**

```python
# core/content_pipeline/generators/layer_composer.py
import io

import numpy as np
from PIL import Image

_CHROMA_KEY_COLOR = (255, 0, 255)  # magenta
_CHROMA_TOLERANCE = 40


def remove_chroma_key(
    image_bytes: bytes,
    key_color: tuple = _CHROMA_KEY_COLOR,
    tolerance: int = _CHROMA_TOLERANCE,
) -> Image.Image:
    """Remove solid magenta background, returning RGBA PIL Image."""
    img = Image.open(io.BytesIO(image_bytes)).convert('RGBA')
    arr = np.array(img, dtype=np.int32)
    kr, kg, kb = key_color
    dist = np.sqrt(
        (arr[:, :, 0] - kr) ** 2
        + (arr[:, :, 1] - kg) ** 2
        + (arr[:, :, 2] - kb) ** 2
    )
    arr[:, :, 3] = np.where(dist < tolerance, 0, arr[:, :, 3])
    return Image.fromarray(arr.astype(np.uint8), 'RGBA')


def composite_layers(
    background_bytes: bytes,
    text_asset_bytes: bytes,
    x: float,
    y: float,
    width: float,
    rotation_deg: float = 0.0,
) -> bytes:
    """Composite text asset (chroma-keyed) onto background at relative coords.

    x, y, width are in [0, 1] relative to background dimensions.
    Returns PNG bytes.
    """
    bg = Image.open(io.BytesIO(background_bytes)).convert('RGBA')
    bw, bh = bg.size

    text_layer = remove_chroma_key(text_asset_bytes)

    target_w = max(1, int(width * bw))
    orig_w, orig_h = text_layer.size
    scale = target_w / orig_w if orig_w > 0 else 1.0
    target_h = max(1, int(orig_h * scale))
    text_layer = text_layer.resize((target_w, target_h), Image.LANCZOS)

    if rotation_deg != 0.0:
        text_layer = text_layer.rotate(-rotation_deg, expand=True, resample=Image.BICUBIC)

    paste_x = int(x * bw)
    paste_y = int(y * bh)

    result = bg.copy()
    result.paste(text_layer, (paste_x, paste_y), text_layer)

    out = io.BytesIO()
    result.convert('RGB').save(out, format='PNG', optimize=True)
    return out.getvalue()
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/content_pipeline/tests/test_layer_composer.py -v
```

Expected: 6 tests PASSED.

- [ ] **Step 5: Commit**

```bash
GIT_EDITOR=true git add core/content_pipeline/generators/layer_composer.py core/content_pipeline/tests/test_layer_composer.py && GIT_EDITOR=true git commit -m "feat: add layer_composer — chroma key + PIL compositing"
```

---

### Task 2: Update ImageGenerator — layered pipeline methods + tests

**Context:** `ImageGenerator` is in `core/content_pipeline/generators/image_generator.py`. Current `generate()` calls `_generate_with_retry(prompt)` → `_generate_with_vertex(prompt)` → Imagen 3. After this task, `generate()` calls `_layered_pipeline()` which orchestrates 2 Imagen 3 + 1 Gemini call + PIL compositing. All callers (`tasks.py`, `views.py`) use the same `generate()` signature and need NO changes.

**Important model behavior:**
- `_generate_with_retry(prompt)` → `_generate_with_vertex(prompt)` → since `VERTEX_IMAGE_MODEL='imagen-3.0-generate-001'`, uses `client.models.generate_images()` with `GenerateImagesConfig`
- For Gemini text+image calls, use `client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, contents=[image_part, text_prompt])`
- `types.Part.from_bytes(data=bytes, mime_type='image/png')` is how to pass images to Gemini

**Files:**
- Modify: `core/content_pipeline/generators/image_generator.py`
- Modify: `core/content_pipeline/tests/test_image_generator.py`

- [ ] **Step 1: Write failing tests for new private methods**

Add these tests at the bottom of `core/content_pipeline/tests/test_image_generator.py` (keep all existing tests):

```python
# ---- Tests for new layered pipeline methods ----

class TestExtractHeadline:
    def test_returns_first_five_meaningful_words(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        headline = gen._extract_headline(
            "Descubre el sabor auténtico de nuestra panadería artesanal #panaderia #food"
        )
        assert headline == "Descubre el sabor auténtico de"

    def test_skips_hashtags(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        headline = gen._extract_headline("#promo #sale Gran oferta hoy en tu tienda favorita")
        assert '#' not in headline

    def test_short_caption(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        headline = gen._extract_headline("Hola mundo")
        assert len(headline) > 0


class TestAnalyzeNegativeSpace:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_fallback_on_api_error(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            result = gen._analyze_negative_space(b'fake-image-bytes')
        assert 'x' in result and 'y' in result and 'width' in result
        assert 0.0 <= result['x'] <= 1.0
        assert 0.0 <= result['y'] <= 1.0

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_parses_valid_gemini_response(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"x": 0.1, "y": 0.65, "width": 0.8}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._analyze_negative_space(b'fake-image-bytes')
        assert result == {'x': 0.1, 'y': 0.65, 'width': 0.8}


class TestLayeredPipeline:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_layered_pipeline_returns_bytes(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        import io
        from PIL import Image
        gen = ImageGenerator(bucket_name='test-bucket')
        # Create a valid PNG for background and text asset
        buf = io.BytesIO()
        Image.new('RGB', (64, 64), (30, 30, 60)).save(buf, format='PNG')
        fake_bg = buf.getvalue()
        buf2 = io.BytesIO()
        Image.new('RGB', (64, 64), (255, 0, 255)).save(buf2, format='PNG')
        fake_text = buf2.getvalue()

        with patch.object(gen, '_generate_background', return_value=fake_bg), \
             patch.object(gen, '_generate_text_asset', return_value=fake_text), \
             patch.object(gen, '_analyze_negative_space', return_value={'x': 0.1, 'y': 0.6, 'width': 0.8}):
            result = gen._layered_pipeline('Caption de prueba', ['#1a1a2e'], 'profesional')
        assert isinstance(result, bytes)
        assert len(result) > 0

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_layered_pipeline_falls_back_to_background_when_text_asset_fails(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        import io
        from PIL import Image
        gen = ImageGenerator(bucket_name='test-bucket')
        buf = io.BytesIO()
        Image.new('RGB', (64, 64), (30, 30, 60)).save(buf, format='PNG')
        fake_bg = buf.getvalue()

        with patch.object(gen, '_generate_background', return_value=fake_bg), \
             patch.object(gen, '_generate_text_asset', return_value=None), \
             patch.object(gen, '_analyze_negative_space', return_value={'x': 0.1, 'y': 0.6, 'width': 0.8}):
            result = gen._layered_pipeline('Caption', ['#1a1a2e'], 'profesional')
        assert result == fake_bg
```

Also update the two existing tests that mock `generate()` internals (they must mock `_layered_pipeline` instead of `_vertex_client`):

```python
# Replace the existing test_generate_returns_url with:
@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
)
def test_generate_returns_url():
    from core.content_pipeline.generators.image_generator import ImageGenerator
    gen = ImageGenerator(bucket_name='test-bucket')
    with patch.object(gen, '_layered_pipeline', return_value=b'fake-png-bytes'), \
         patch.object(gen, '_upload_to_storage', return_value='https://storage.googleapis.com/test/img.jpg'):
        url = gen.generate(
            caption='Diseno web profesional',
            colors=['#1a1a2e'],
            tone='profesional',
            filename='test-img',
        )
    assert url.startswith('https://')


# Replace the existing test_generate_returns_fallback_on_error with:
@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
)
def test_generate_returns_fallback_on_error():
    from core.content_pipeline.generators.image_generator import ImageGenerator
    gen = ImageGenerator(bucket_name='test-bucket')
    with patch.object(gen, '_layered_pipeline', side_effect=Exception('Pipeline error')):
        url = gen.generate(
            caption='Diseno web profesional',
            colors=['#1a1a2e'],
            tone='profesional',
            filename='test-img',
        )
    assert url == ''
```

- [ ] **Step 2: Run tests to verify new tests fail**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/content_pipeline/tests/test_image_generator.py -v 2>&1 | tail -20
```

Expected: AttributeError on `_extract_headline`, `_analyze_negative_space`, `_layered_pipeline` — these don't exist yet.

- [ ] **Step 3: Add new private methods to ImageGenerator**

Replace the entire `core/content_pipeline/generators/image_generator.py` with:

```python
import io
import json
import logging
import random
import re
import textwrap
import time

import google.genai as genai
from google.cloud import storage
from google.genai import types
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAYS = [10, 20, 40]


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


class ImageGenerator:
    def __init__(self, bucket_name: str):
        self._bucket = bucket_name

    def generate(self, caption: str, colors: list[str], tone: str, filename: str) -> str:
        try:
            image_bytes = self._layered_pipeline(caption, colors, tone)
            return self._upload_to_storage(image_bytes, filename)
        except Exception as e:
            logger.error(f"ImageGenerator error: {e}")
            return ''

    # ------------------------------------------------------------------
    # Layered pipeline
    # ------------------------------------------------------------------

    def _layered_pipeline(self, caption: str, colors: list[str], tone: str) -> bytes:
        from core.content_pipeline.generators.layer_composer import composite_layers

        background_bytes = self._generate_background(caption, colors, tone)

        headline = self._extract_headline(caption)
        text_asset_bytes = self._generate_text_asset(headline, colors)

        placement = self._analyze_negative_space(background_bytes)

        rotation = random.uniform(-5.0, 5.0) if random.random() < 0.4 else 0.0

        if text_asset_bytes:
            return composite_layers(
                background_bytes=background_bytes,
                text_asset_bytes=text_asset_bytes,
                x=placement['x'],
                y=placement['y'],
                width=placement['width'],
                rotation_deg=rotation,
            )

        logger.warning("Text asset failed all retries — returning background only")
        return background_bytes

    def _generate_background(self, caption: str, colors: list[str], tone: str) -> bytes:
        color_str = ', '.join(colors[:3]) if colors else 'modern vibrant colors'
        prompt = (
            f"Minimalist abstract background for social media. "
            f"Visual concept: {caption[:80]}. "
            f"Brand colors: {color_str}. Style: {tone}. "
            f"CRITICAL: absolutely no text, no words, no letters, no numbers anywhere. "
            f"Leave a significant area of flat or low-complexity negative space for text overlay. "
            f"Square format 1:1, professional, high quality."
        )
        return self._generate_with_retry(prompt)

    def _extract_headline(self, caption: str) -> str:
        words = [w for w in caption.split() if len(w) > 2 and not w.startswith('#')]
        return ' '.join(words[:5]) or caption[:30]

    def _generate_text_asset(self, headline: str, colors: list[str], max_retries: int = 2) -> bytes | None:
        color_str = colors[0] if colors else '#ffffff'
        prompt = (
            f"High quality 3D rendered bold text saying exactly '{headline}' "
            f"on a solid magenta background color #FF00FF. "
            f"Text color: white or {color_str}. Modern bold font, centered. "
            f"Nothing else in the image — only the text and the magenta background. "
            f"High contrast, sharp edges. Square image."
        )
        for attempt in range(max_retries):
            try:
                return self._generate_with_vertex(prompt)
            except Exception as e:
                logger.warning(f"Text asset attempt {attempt + 1}/{max_retries} failed: {e}")
        return None

    def _analyze_negative_space(self, background_bytes: bytes) -> dict:
        _FALLBACK = {'x': 0.1, 'y': 0.6, 'width': 0.8}
        try:
            client = _vertex_client()
            image_part = types.Part.from_bytes(data=background_bytes, mime_type='image/png')
            prompt = (
                "Analyze this image and find the best rectangular area for a text overlay. "
                "The area should have low visual complexity (flat color, negative space, or blur). "
                "Return ONLY a JSON object — no explanation, no markdown: "
                '{"x": <left edge 0.0-1.0>, "y": <top edge 0.0-1.0>, "width": <width 0.4-0.9>}'
            )
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL,
                contents=[image_part, prompt],
            )
            raw = resp.text.strip()
            match = re.search(r'\{[^}]+\}', raw)
            if match:
                data = json.loads(match.group())
                return {
                    'x': max(0.0, min(0.9, float(data.get('x', 0.1)))),
                    'y': max(0.0, min(0.85, float(data.get('y', 0.6)))),
                    'width': max(0.4, min(0.95, float(data.get('width', 0.8)))),
                }
        except Exception as e:
            logger.warning(f"Negative space analysis failed, using fallback: {e}")
        return _FALLBACK

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _build_prompt(self, caption: str, colors: list[str], tone: str) -> str:
        color_str = ', '.join(colors[:3]) if colors else 'modern vibrant colors'
        return (
            f"Professional social media post image. Concept: {caption[:120]}. "
            f"Use brand colors: {color_str}. Visual style: {tone}, clean, "
            f"high quality, square format 1:1, photographic or illustrated."
        )

    def _overlay_text(self, image_bytes: bytes, caption: str) -> bytes:
        if not caption or not caption.strip():
            return image_bytes

        img = Image.open(io.BytesIO(image_bytes)).convert('RGBA')
        w, h = img.size

        if w == 0 or h == 0:
            return image_bytes

        bar_h = int(h * 0.25)
        overlay = Image.new('RGBA', (w, bar_h), (0, 0, 0, 0))
        draw_overlay = ImageDraw.Draw(overlay)
        for y in range(bar_h):
            alpha = int(180 * (1 - y / bar_h))
            draw_overlay.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))

        img.paste(overlay, (0, h - bar_h), overlay)

        draw = ImageDraw.Draw(img)
        font_size = max(22, w // 28)
        _DEJAVU = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
        try:
            font = ImageFont.truetype(_DEJAVU, size=font_size)
        except (OSError, IOError):
            try:
                font = ImageFont.load_default(size=font_size)
            except TypeError:
                font = ImageFont.load_default()

        padding = w // 20
        max_chars = max(20, w // (font_size // 2))
        lines = textwrap.wrap(caption[:240], width=max_chars)[:4]
        text = '\n'.join(lines)

        text_y = h - bar_h + padding
        draw.text((padding + 2, text_y + 2), text, font=font, fill=(0, 0, 0, 200))
        draw.text((padding, text_y), text, font=font, fill=(255, 255, 255, 255))

        out = io.BytesIO()
        img.convert('RGB').save(out, format='PNG', optimize=True)
        return out.getvalue()

    def _generate_with_retry(self, prompt: str) -> bytes:
        last_error = None
        for attempt in range(_MAX_RETRIES):
            try:
                return self._generate_with_vertex(prompt)
            except Exception as e:
                last_error = e
                if '429' in str(e) and attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_DELAYS[attempt]
                    logger.warning(f"Rate limit, reintento {attempt + 1} en {delay}s")
                    time.sleep(delay)
                else:
                    raise
        raise last_error

    def _generate_with_vertex(self, prompt: str) -> bytes:
        client = _vertex_client()
        model = settings.VERTEX_IMAGE_MODEL
        if 'imagen' in model:
            resp = client.models.generate_images(
                model=model,
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio='1:1',
                ),
            )
            if resp.generated_images:
                return resp.generated_images[0].image.image_bytes
            raise ValueError("No image returned by Imagen")
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=['IMAGE', 'TEXT']
            ),
        )
        for part in resp.candidates[0].content.parts:
            if part.inline_data:
                return part.inline_data.data
        raise ValueError("No image returned by Vertex AI")

    def _upload_to_storage(self, image_bytes: bytes, filename: str) -> str:
        client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
        bucket = client.bucket(self._bucket)
        blob = bucket.blob(f'posts/{filename}.png')
        blob.upload_from_string(image_bytes, content_type='image/png')
        blob.make_public()
        return blob.public_url
```

- [ ] **Step 4: Update the test file**

Replace `core/content_pipeline/tests/test_image_generator.py` entirely with the version that has updated `test_generate_returns_url`, `test_generate_returns_fallback_on_error`, and all the new test classes:

```python
import io
from unittest.mock import patch, MagicMock
from django.test import override_settings
from PIL import Image


# ---- Helpers ----

def _png_bytes(color=(30, 30, 60), size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, format='PNG')
    return buf.getvalue()


# ---- Existing tests (updated) ----

def test_build_prompt_includes_colors():
    from core.content_pipeline.generators.image_generator import ImageGenerator
    gen = ImageGenerator(bucket_name='test-bucket')
    prompt = gen._build_prompt(
        caption='Diseno web profesional para tu empresa',
        colors=['#1a1a2e', '#e94560'],
        tone='profesional',
    )
    assert '#1a1a2e' in prompt
    assert 'profesional' in prompt


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
)
def test_generate_returns_url():
    from core.content_pipeline.generators.image_generator import ImageGenerator
    gen = ImageGenerator(bucket_name='test-bucket')
    with patch.object(gen, '_layered_pipeline', return_value=b'fake-png-bytes'), \
         patch.object(gen, '_upload_to_storage', return_value='https://storage.googleapis.com/test/img.jpg'):
        url = gen.generate(
            caption='Diseno web profesional',
            colors=['#1a1a2e'],
            tone='profesional',
            filename='test-img',
        )
    assert url.startswith('https://')


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
)
def test_generate_returns_fallback_on_error():
    from core.content_pipeline.generators.image_generator import ImageGenerator
    gen = ImageGenerator(bucket_name='test-bucket')
    with patch.object(gen, '_layered_pipeline', side_effect=Exception('Pipeline error')):
        url = gen.generate(
            caption='Diseno web profesional',
            colors=['#1a1a2e'],
            tone='profesional',
            filename='test-img',
        )
    assert url == ''


class TestOverlayText:
    def test_overlay_produces_valid_png(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        result = gen._overlay_text(_png_bytes(size=(1024, 1024)), "Caption de prueba")
        out = Image.open(io.BytesIO(result))
        assert out.size == (1024, 1024)
        assert result != _png_bytes(size=(1024, 1024))

    def test_overlay_handles_long_caption(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        result = gen._overlay_text(_png_bytes(size=(1024, 1024)), "A" * 300)
        assert len(result) > 0
        assert Image.open(io.BytesIO(result)).size == (1024, 1024)


# ---- New tests ----

class TestExtractHeadline:
    def test_returns_first_five_meaningful_words(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        headline = gen._extract_headline(
            "Descubre el sabor auténtico de nuestra panadería artesanal #panaderia #food"
        )
        assert headline == "Descubre el sabor auténtico de"

    def test_skips_hashtags(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        headline = gen._extract_headline("#promo #sale Gran oferta hoy en tu tienda favorita")
        assert '#' not in headline

    def test_short_caption(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        assert len(gen._extract_headline("Hola mundo")) > 0


class TestAnalyzeNegativeSpace:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_fallback_on_api_error(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            result = gen._analyze_negative_space(b'fake')
        assert 'x' in result and 'y' in result and 'width' in result
        assert 0.0 <= result['x'] <= 1.0

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_parses_valid_gemini_response(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"x": 0.1, "y": 0.65, "width": 0.8}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._analyze_negative_space(b'fake')
        assert result == {'x': 0.1, 'y': 0.65, 'width': 0.8}


class TestLayeredPipeline:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_pipeline_composites_when_text_asset_succeeds(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes((30, 30, 60))
        fake_text = _png_bytes((255, 0, 255))

        with patch.object(gen, '_generate_background', return_value=fake_bg), \
             patch.object(gen, '_generate_text_asset', return_value=fake_text), \
             patch.object(gen, '_analyze_negative_space', return_value={'x': 0.1, 'y': 0.6, 'width': 0.8}):
            result = gen._layered_pipeline('Caption de prueba', ['#1a1a2e'], 'profesional')

        assert isinstance(result, bytes)
        assert len(result) > 0

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_pipeline_returns_background_when_text_asset_fails(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes((30, 30, 60))

        with patch.object(gen, '_generate_background', return_value=fake_bg), \
             patch.object(gen, '_generate_text_asset', return_value=None), \
             patch.object(gen, '_analyze_negative_space', return_value={'x': 0.1, 'y': 0.6, 'width': 0.8}):
            result = gen._layered_pipeline('Caption', ['#1a1a2e'], 'profesional')

        assert result == fake_bg
```

- [ ] **Step 5: Run all image generator tests**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/content_pipeline/tests/test_image_generator.py -v
```

Expected: All tests PASSED (including existing `TestOverlayText` tests).

- [ ] **Step 6: Run the full test suite to check for regressions**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/ -v --tb=short 2>&1 | tail -30
```

Expected: No new failures. Existing tests that were passing should still pass.

- [ ] **Step 7: Restart backend to pick up the Python changes**

```bash
docker compose restart backend rqworker
```

Expected: Both services restart cleanly (no ImportError).

- [ ] **Step 8: Commit**

```bash
GIT_EDITOR=true git add core/content_pipeline/generators/image_generator.py core/content_pipeline/tests/test_image_generator.py && GIT_EDITOR=true git commit -m "feat: layered image pipeline — background + 3D text asset + Gemini placement"
```

---

## Self-Review

**Spec coverage:**
- ✅ Imagen 3 background with `no text` in prompt → `_generate_background()`
- ✅ Imagen 3 text asset on magenta `#FF00FF` → `_generate_text_asset()`
- ✅ Gemini multimodal placement → `_analyze_negative_space()`
- ✅ PIL chroma key removal → `layer_composer.remove_chroma_key()`
- ✅ PIL compositing → `layer_composer.composite_layers()`
- ✅ Rotation `random(-5, +5)` in 40% of images → `random.random() < 0.4` in `_layered_pipeline()`
- ✅ Retry only text asset on failure → `_generate_text_asset(max_retries=2)` retries independently
- ✅ Fallback to background-only if text asset fails → `if text_asset_bytes:` guard
- ✅ Same `generate()` signature → callers in `tasks.py` and `views.py` unchanged
- ✅ Subtitles/hashtags: `_overlay_text()` kept (not called currently, available if needed)

**Placeholder scan:** No TBDs or TODOs in plan.

**Type consistency:**
- `composite_layers(background_bytes, text_asset_bytes, x, y, width, rotation_deg)` — matches all call sites
- `_analyze_negative_space()` returns `{'x': float, 'y': float, 'width': float}` — matches destructuring in `_layered_pipeline()`
- `_generate_text_asset()` returns `bytes | None` — checked with `if text_asset_bytes:` guard
