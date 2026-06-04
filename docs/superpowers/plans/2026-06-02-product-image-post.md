# Product Image Post — Camino A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow clients to upload a product photo that becomes the post background, with Gemini analyzing the product visually to generate tailored copy (headline, subtitle, CTA, tag).

**Architecture:** New optional `product_image_path` field on `AnalysisJob`. When present, `_layered_pipeline()` skips Imagen 3 and uses the product photo directly as background; `_generate_post_content()` receives the image bytes and makes a multimodal Gemini call so the model can "see" the product and write copy about it. When absent, the existing Imagen 3 flow runs unchanged.

**Tech Stack:** Django 5.2, Vertex AI Gemini multimodal (`types.Part.from_bytes`), Pillow (mime sniff), Playwright (unchanged), existing `instagram_post.html` template.

---

## File Map

| Action | File |
|--------|------|
| Modify | `core/brand_dna/models.py` |
| Create | `core/brand_dna/migrations/0003_analysisjob_product_image_path.py` |
| Modify | `core/brand_dna/templates/brand_dna/landing.html` |
| Modify | `core/brand_dna/views.py` |
| Modify | `core/content_pipeline/generators/image_generator.py` |
| Modify | `core/content_pipeline/tasks.py` |
| Modify | `core/content_pipeline/tests/test_image_generator.py` |

---

## Task 1: Model field + migration

**Files:**
- Modify: `core/brand_dna/models.py`
- Create: `core/brand_dna/migrations/0003_analysisjob_product_image_path.py`

- [ ] **Step 1: Add field to `AnalysisJob` in `models.py`**

Find the `AnalysisJob` class. After the `profile_url` field, add:

```python
product_image_path = models.CharField(max_length=500, blank=True, default='')
```

- [ ] **Step 2: Create migration file**

Create `core/brand_dna/migrations/0003_analysisjob_product_image_path.py`:

```python
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('brand_dna', '0002_add_user_to_analysisjob'),
    ]

    operations = [
        migrations.AddField(
            model_name='analysisjob',
            name='product_image_path',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
    ]
```

- [ ] **Step 3: Apply migration**

```bash
docker compose exec -T backend python manage.py migrate brand_dna
```

Expected output: `Applying brand_dna.0003_analysisjob_product_image_path... OK`

- [ ] **Step 4: Verify**

```bash
docker compose exec -T backend python manage.py shell -c "
from core.brand_dna.models import AnalysisJob
print(hasattr(AnalysisJob(), 'product_image_path'))
"
```

Expected: `True`

- [ ] **Step 5: Commit**

```bash
git add core/brand_dna/models.py core/brand_dna/migrations/0003_analysisjob_product_image_path.py
git commit -m "feat: add product_image_path field to AnalysisJob"
```

---

## Task 2: Landing form + analyze_submit view

**Files:**
- Modify: `core/brand_dna/templates/brand_dna/landing.html`
- Modify: `core/brand_dna/views.py`

- [ ] **Step 1: Add product image field to landing form**

In `landing.html`, find the line:
```html
      <button type="submit" class="btn">Analizar mi marca</button>
```

Insert BEFORE it:

```html
      <div class="section-title">Foto de tu producto <span class="optional-badge">opcional — genera posts con tu producto real</span></div>
      <div class="form-group">
        <label>Sube una foto de tu producto</label>
        <input type="file" name="product_image" accept="image/*">
        <small style="color:#666;font-size:0.8rem;margin-top:4px;display:block;">Ej: collar de plata, platillo del menú, prenda de ropa. La usaremos como fondo del post.</small>
      </div>
```

- [ ] **Step 2: Handle product image upload in `analyze_submit` view**

In `core/brand_dna/views.py`, find the `analyze_submit` function. After the block that handles `post_images` (around line 64), add:

```python
    if 'product_image' in request.FILES:
        prod_file = request.FILES['product_image']
        ext = prod_file.name.rsplit('.', 1)[-1].lower() if '.' in prod_file.name else 'jpg'
        prod_path = f'uploads/product_{job.id}.{ext}'
        full_path = os.path.join(settings.MEDIA_ROOT, prod_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'wb') as f:
            for chunk in prod_file.chunks():
                f.write(chunk)
        job.product_image_path = prod_path
        job.save(update_fields=['product_image_path'])
```

- [ ] **Step 3: Verify form renders correctly**

```bash
docker compose exec -T backend python manage.py check
```

Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 4: Commit**

```bash
git add core/brand_dna/templates/brand_dna/landing.html core/brand_dna/views.py
git commit -m "feat: add product image upload to landing form and analyze_submit"
```

---

## Task 3: `_generate_post_content()` multimodal + tests (TDD)

**Files:**
- Modify: `core/content_pipeline/generators/image_generator.py`
- Modify: `core/content_pipeline/tests/test_image_generator.py`

The method must support two modes:
- `product_image_bytes=None` → existing text-only Gemini call (unchanged)
- `product_image_bytes=<bytes>` → multimodal call: Gemini sees the product image + caption

- [ ] **Step 1: Write failing tests**

Add this class to `test_image_generator.py` after `TestGeneratePostContent`:

```python
class TestGeneratePostContentWithProduct:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_multimodal_call_when_product_image_provided(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_image = _png_bytes()
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"headline":"Brilla distinto","subtitle":"Plata 925 hecha a mano para ti","cta":"Cómpralo ahora","tag":"JOYERÍA"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._generate_post_content('Collar artesanal de plata', product_image_bytes=fake_image)
        # Verify multimodal call: contents must be a list (image + prompt), not a string
        call_args = mock_vc.return_value.models.generate_content.call_args
        contents = call_args.kwargs.get('contents') or call_args.args[1] if call_args.args else call_args.kwargs['contents']
        assert isinstance(contents, list), "Multimodal call must pass contents as list [image_part, prompt]"
        assert result['headline'] == 'Brilla distinto'
        assert result['tag'] == 'JOYERÍA'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_text_only_call_when_no_product_image(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"headline":"Impulsa tu negocio","subtitle":"Tecnología que funciona para ti","cta":"Empieza hoy","tag":"TECNOLOGÍA"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._generate_post_content('Soluciones tecnológicas', product_image_bytes=None)
        call_args = mock_vc.return_value.models.generate_content.call_args
        contents = call_args.kwargs.get('contents') or (call_args.args[1] if len(call_args.args) > 1 else None)
        # Text-only: contents is a string, not a list
        assert isinstance(contents, str), "Text-only call must pass contents as string"
        assert result['headline'] == 'Impulsa tu negocio'
```

- [ ] **Step 2: Run tests — must fail**

```bash
docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_image_generator.py::TestGeneratePostContentWithProduct -v
```

Expected: `FAILED` (method signature doesn't accept `product_image_bytes` yet)

- [ ] **Step 3: Update `_generate_post_content()` in `image_generator.py`**

Replace the entire `_generate_post_content` method with:

```python
def _generate_post_content(self, caption: str, product_image_bytes: bytes = None) -> dict:
    """Gemini generates {headline, subtitle, cta, tag}. Multimodal if product_image_bytes provided."""
    _FALLBACK = {
        'headline': self._extract_headline(caption),
        'subtitle': (caption[:120] if caption else '').strip(),
        'cta': 'Contáctanos hoy',
        'tag': 'DESTACADO',
    }
    try:
        client = _vertex_client()
        if product_image_bytes:
            mime = 'image/png' if product_image_bytes[:4] == b'\x89PNG' else 'image/jpeg'
            image_part = types.Part.from_bytes(data=product_image_bytes, mime_type=mime)
            prompt = (
                f"Caption del post: \"{caption[:300]}\"\n\n"
                "Observa este producto en la imagen y genera el contenido para un post de Instagram:\n"
                "1. headline: 3-5 palabras. Frase gancho sobre este producto específico. Sin nombres de marca.\n"
                "2. subtitle: 8-15 palabras. Describe el atractivo o beneficio de este producto.\n"
                "3. cta: 2-4 palabras. Llamada a la acción. (Ej: 'Cómpralo ahora', 'Pide el tuyo')\n"
                "4. tag: 1-3 palabras EN MAYÚSCULAS. Categoría del producto. (Ej: 'JOYERÍA', 'MODA', 'GASTRONOMÍA')\n\n"
                "REGLAS: Español impecable. Sin inventar palabras.\n"
                "Responde ÚNICAMENTE este JSON (sin markdown):\n"
                "{\"headline\":\"...\",\"subtitle\":\"...\",\"cta\":\"...\",\"tag\":\"...\"}"
            )
            contents = [image_part, prompt]
        else:
            prompt = (
                f"Caption del post: \"{caption[:300]}\"\n\n"
                "Genera el contenido para un post de Instagram con estos 4 elementos:\n"
                "1. headline: 3-5 palabras. Frase gancho, memorable. Sin nombres de marca, URLs, hashtags.\n"
                "2. subtitle: 8-15 palabras. Amplía el headline con el beneficio clave. Español correcto.\n"
                "3. cta: 2-4 palabras. Llamada a la acción directa. (Ej: 'Empieza hoy', 'Solicita tu demo')\n"
                "4. tag: 1-3 palabras EN MAYÚSCULAS. Categoría del sector. (Ej: 'DISEÑO WEB', 'NUTRICIÓN')\n\n"
                "REGLAS: Español impecable. Sin inventar palabras. Sin duplicar letras.\n"
                "Responde ÚNICAMENTE este JSON (sin markdown):\n"
                "{\"headline\":\"...\",\"subtitle\":\"...\",\"cta\":\"...\",\"tag\":\"...\"}"
            )
            contents = prompt
        resp = client.models.generate_content(
            model=settings.VERTEX_TEXT_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "Eres 'Cosmic', Director Creativo de Agente Cosmic. "
                    "Generas contenido de marketing para redes sociales. "
                    "Español impecable. Cero errores ortográficos. Nunca inventes palabras. "
                    "Frases para imagen: cortas, impactantes, máximo 5 palabras."
                ),
            ),
        )
        raw = resp.text.strip()
        match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
        if match:
            data = json.loads(match.group())
            return {
                'headline': str(data.get('headline', '')).strip() or _FALLBACK['headline'],
                'subtitle': str(data.get('subtitle', '')).strip() or _FALLBACK['subtitle'],
                'cta': str(data.get('cta', '')).strip() or _FALLBACK['cta'],
                'tag': str(data.get('tag', '')).strip().upper() or _FALLBACK['tag'],
            }
    except Exception as e:
        logger.warning(f"Post content generation failed, using fallback: {e}")
    return _FALLBACK
```

- [ ] **Step 4: Run tests — must pass**

```bash
docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_image_generator.py::TestGeneratePostContentWithProduct -v
```

Expected: `2 passed`

- [ ] **Step 5: Run full suite — no regressions**

```bash
docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_image_generator.py -v
```

Expected: All existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/generators/image_generator.py core/content_pipeline/tests/test_image_generator.py
git commit -m "feat: _generate_post_content multimodal support for product images"
```

---

## Task 4: `_layered_pipeline()` + `generate()` + callers (TDD)

**Files:**
- Modify: `core/content_pipeline/generators/image_generator.py`
- Modify: `core/content_pipeline/tasks.py`
- Modify: `core/brand_dna/views.py`
- Modify: `core/content_pipeline/tests/test_image_generator.py`

When `product_image_bytes` is provided: skip Imagen 3, use product photo as background, call `_generate_post_content` with image. When absent: existing flow.

- [ ] **Step 1: Write failing tests**

Add this class to `test_image_generator.py`:

```python
class TestLayeredPipelineWithProduct:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_product_path_skips_imagen3_uses_product_as_background(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        product_img = _png_bytes((200, 150, 100))
        fake_content = {'headline': 'Brilla distinto', 'subtitle': 'Plata artesanal', 'cta': 'Cómpralo', 'tag': 'JOYERÍA'}
        fake_shot = _png_bytes((100, 100, 100), size=(1080, 1080))

        with patch.object(gen, '_generate_background') as mock_bg, \
             patch.object(gen, '_generate_post_content', return_value=fake_content) as mock_content, \
             patch.object(gen, '_render_html_template', return_value=fake_shot) as mock_render:
            result = gen._layered_pipeline('Collar artesanal', ['#c0c0c0'], 'elegante', product_image_bytes=product_img)

        mock_bg.assert_not_called()
        mock_content.assert_called_once_with('Collar artesanal', product_image_bytes=product_img)
        mock_render.assert_called_once_with(product_img, fake_content, ['#c0c0c0'])
        assert result == fake_shot

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_no_product_uses_imagen3_flow(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes((30, 30, 60))
        fake_content = {'headline': 'Hola mundo', 'subtitle': 'Mundo', 'cta': 'Ver', 'tag': 'TEST'}
        fake_shot = _png_bytes((100, 100, 100), size=(1080, 1080))

        with patch.object(gen, '_generate_background', return_value=fake_bg) as mock_bg, \
             patch.object(gen, '_generate_post_content', return_value=fake_content) as mock_content, \
             patch.object(gen, '_render_html_template', return_value=fake_shot):
            gen._layered_pipeline('Caption', ['#1a1a2e'], 'profesional', product_image_bytes=None)

        mock_bg.assert_called_once()
        mock_content.assert_called_once_with('Caption', product_image_bytes=None)
```

- [ ] **Step 2: Run tests — must fail**

```bash
docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_image_generator.py::TestLayeredPipelineWithProduct -v
```

Expected: `FAILED` (`_layered_pipeline` doesn't accept `product_image_bytes` yet)

- [ ] **Step 3: Update `generate()` and `_layered_pipeline()` in `image_generator.py`**

Replace `generate()`:

```python
def generate(self, caption: str, colors: list[str], tone: str, filename: str, brand_name: str = '', keywords: list[str] = None, description: str = '', product_image_bytes: bytes = None) -> str:
    try:
        image_bytes = self._layered_pipeline(caption, colors, tone, keywords or [], description, product_image_bytes=product_image_bytes)
        return self._upload_to_storage(image_bytes, filename)
    except Exception as e:
        logger.error(f"ImageGenerator error: {e}")
        return ''
```

Replace `_layered_pipeline()`:

```python
def _layered_pipeline(self, caption: str, colors: list[str], tone: str, keywords: list[str] = None, description: str = '', product_image_bytes: bytes = None) -> bytes:
    if product_image_bytes:
        background_bytes = product_image_bytes
        content = self._generate_post_content(caption, product_image_bytes=product_image_bytes)
    else:
        background_bytes = self._generate_background(caption, colors, tone, keywords or [], description)
        content = self._generate_post_content(caption, product_image_bytes=None)
    return self._render_html_template(background_bytes, content, colors)
```

- [ ] **Step 4: Run new tests — must pass**

```bash
docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_image_generator.py::TestLayeredPipelineWithProduct -v
```

Expected: `2 passed`

- [ ] **Step 5: Run full test suite**

```bash
docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_image_generator.py -v
```

Expected: All tests pass (20+ tests).

- [ ] **Step 6: Update `content_generation_task` in `tasks.py`**

In `content_generation_task`, find where `image_gen` is created (around line 30). Add product image loading BEFORE the `for` loop:

```python
        # Load product image bytes if client uploaded one
        product_image_bytes = None
        if job.product_image_path:
            prod_full = os.path.join(settings.MEDIA_ROOT, job.product_image_path)
            if os.path.exists(prod_full):
                with open(prod_full, 'rb') as _f:
                    product_image_bytes = _f.read()
```

Then update the Day 1 `image_gen.generate()` call to add `product_image_bytes=product_image_bytes`:

```python
                image_url = image_gen.generate(
                    caption=post_data['caption'],
                    colors=brand_dna.primary_colors,
                    tone=brand_dna.tone,
                    filename=f"{job_id}-day{i}",
                    brand_name=brand_dna.business_name,
                    keywords=brand_dna.keywords,
                    description=brand_dna.description,
                    product_image_bytes=product_image_bytes,
                )
```

- [ ] **Step 7: Update `send_daily_email_task` in `tasks.py`**

In `send_daily_email_task`, after `brand_dna = post.calendar.brand_dna`, add product image loading:

```python
            product_image_bytes = None
            if brand_dna.job.product_image_path:
                prod_full = os.path.join(settings.MEDIA_ROOT, brand_dna.job.product_image_path)
                if os.path.exists(prod_full):
                    with open(prod_full, 'rb') as _f:
                        product_image_bytes = _f.read()
```

Then update the `image_gen.generate()` call to add `product_image_bytes=product_image_bytes`:

```python
            post.image_url = image_gen.generate(
                caption=post.caption,
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                filename=f"{job_id}-day{post.day_number}",
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                product_image_bytes=product_image_bytes,
            )
```

- [ ] **Step 8: Update `post_action_api` regeneration in `views.py`**

In `views.py`, find the `post_action_api` regeneration block. After `brand_dna = post.calendar.brand_dna`, add:

```python
            product_image_bytes = None
            if brand_dna.job.product_image_path:
                import os as _os
                from django.conf import settings as _settings
                prod_full = _os.path.join(_settings.MEDIA_ROOT, brand_dna.job.product_image_path)
                if _os.path.exists(prod_full):
                    with open(prod_full, 'rb') as _f:
                        product_image_bytes = _f.read()
```

Then add `product_image_bytes=product_image_bytes` to the `image_gen.generate()` call.

- [ ] **Step 9: Run system check**

```bash
docker compose exec -T backend python manage.py check
```

Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 10: Commit all**

```bash
git add core/content_pipeline/generators/image_generator.py \
        core/content_pipeline/tests/test_image_generator.py \
        core/content_pipeline/tasks.py \
        core/brand_dna/views.py
git commit -m "feat: product image as post background, multimodal Gemini copy generation"
```

- [ ] **Step 11: Deploy and restart**

```bash
docker compose restart backend rqworker && git push origin main
```

---

## Self-Review

**Spec coverage:**
- ✅ `product_image_path` field on `AnalysisJob` → Task 1
- ✅ Product photo upload on landing form → Task 2
- ✅ File saved to `media/uploads/product_{job_id}.{ext}` → Task 2
- ✅ `_generate_post_content()` multimodal when product image present → Task 3
- ✅ `_layered_pipeline()` skips Imagen 3 when product image present → Task 4
- ✅ `content_generation_task` loads and passes product image → Task 4
- ✅ `send_daily_email_task` loads and passes product image (all 7 days) → Task 4
- ✅ Regeneration in `post_action_api` uses product image → Task 4

**Placeholder scan:** None found.

**Type consistency:**
- `product_image_bytes: bytes = None` used consistently in `generate()`, `_layered_pipeline()`, `_generate_post_content()` ✅
- `product_image_path: str` on model, loaded as bytes before passing to generator ✅
- PNG detection: `product_image_bytes[:4] == b'\x89PNG'` — correct PNG magic bytes ✅
