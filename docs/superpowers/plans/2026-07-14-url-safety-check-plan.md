# No inventar sitio web en el copy generado — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ningún post (imagen individual, carrusel, ni caption) debe invitar a "visitar nuestra web" cuando el negocio nunca proporcionó una URL durante el análisis de Brand DNA.

**Architecture:** Chequeo determinístico (regex + reemplazo por fallbacks ya existentes) para CTA/headline/subtítulo en `image_generator.py`, sin llamadas nuevas a Gemini. Extensión del QC de seguridad ya existente (`_validate_caption_safety`/`_SAFETY_FIX_PROMPT`) en `text_generator.py` para el caption, disparado también cuando falta la URL (no solo en nichos sensibles).

**Tech Stack:** Django, Gemini vía `google-genai` (Vertex AI), pytest + `django.test.override_settings`.

## Global Constraints

- `BrandDNA.business_url` es el dato fuente — vacío (`''`) significa "no proporcionó URL", nunca inventar que existe.
- CTA/headline/subtítulo (posts individuales y carrusel): **sin llamadas nuevas a Gemini** — chequeo por regex, reemplazo por valores deterministas ya existentes en el código (`'Contáctanos hoy'`, `_extract_headline(caption)`, `_truncate_at_word_boundary(caption)`).
- Caption principal: extiende `_validate_caption_safety`/`_SAFETY_FIX_PROMPT` ya existentes — el nuevo chequeo corre siempre que `business_url` esté vacío, sin importar el nicho (antes solo nichos sensibles disparaban el chequeo).
- No modificar la firma de `_generate_post_media` en `tasks.py` — ya reenvía `**kwargs` automáticamente.

---

### Task 1: Sanitización determinística en `image_generator.py`

**Files:**
- Modify: `core/content_pipeline/generators/image_generator.py`
- Test: `core/content_pipeline/tests/test_image_generator.py`

**Interfaces:**
- Produces: `_sanitize_web_visit_mention(text: str, business_url: str, fallback: str) -> str` (función de módulo). `ImageGenerator._generate_post_content(self, caption, product_image_bytes=None, brand_context='', business_url='') -> dict` y `ImageGenerator._generate_carousel_slides_content(self, caption, brand_context='', num_slides=4, business_url='') -> list[dict]` — ambas ganan el parámetro `business_url`. `ImageGenerator.generate(...)` y `ImageGenerator.generate_carousel(...)` ganan el mismo parámetro — consumido por Task 2.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `core/content_pipeline/tests/test_image_generator.py`:

```python
class TestSanitizeWebVisitMention:
    def test_no_url_and_mentions_website_returns_fallback(self):
        from core.content_pipeline.generators.image_generator import _sanitize_web_visit_mention
        result = _sanitize_web_visit_mention('Visita nuestra web hoy', '', 'Contáctanos hoy')
        assert result == 'Contáctanos hoy'

    def test_no_url_and_no_mention_returns_original(self):
        from core.content_pipeline.generators.image_generator import _sanitize_web_visit_mention
        result = _sanitize_web_visit_mention('Compra ahora', '', 'Contáctanos hoy')
        assert result == 'Compra ahora'

    def test_has_url_and_mentions_website_returns_original(self):
        from core.content_pipeline.generators.image_generator import _sanitize_web_visit_mention
        result = _sanitize_web_visit_mention('Visita nuestra web hoy', 'https://ejemplo.com', 'Contáctanos hoy')
        assert result == 'Visita nuestra web hoy'
```

Agregar al final de la clase `TestGeneratePostContent` en el mismo archivo (después del último método existente, `test_fallback_subtitle_not_truncated_when_short`):

```python
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_sanitizes_cta_when_no_business_url(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"headline":"H","subtitle":"S","cta":"Visita nuestra web","tag":"T"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._generate_post_content('Caption de prueba', business_url='')
        assert result['cta'] == 'Contáctanos hoy'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_keeps_cta_when_business_url_present(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"headline":"H","subtitle":"S","cta":"Visita nuestra web","tag":"T"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._generate_post_content('Caption de prueba', business_url='https://ejemplo.com')
        assert result['cta'] == 'Visita nuestra web'
```

Agregar al final de la clase `TestGenerateCarouselSlidesContent` (después del último método existente, `test_fallback_subtitle_truncates_at_word_boundary`):

```python
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_sanitizes_cta_when_no_business_url(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '[{"headline":"H","subtitle":"S","cta":"Visita nuestra pagina web","tag":"T"}]'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            slides = gen._generate_carousel_slides_content('Caption', num_slides=1, business_url='')
        assert slides[0]['cta'] == 'Contáctanos hoy'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_keeps_cta_when_business_url_present(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '[{"headline":"H","subtitle":"S","cta":"Visita nuestra pagina web","tag":"T"}]'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            slides = gen._generate_carousel_slides_content('Caption', num_slides=1, business_url='https://ejemplo.com')
        assert slides[0]['cta'] == 'Visita nuestra pagina web'
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_image_generator.py -k "SanitizeWebVisitMention or sanitizes_cta or keeps_cta" -v`
Expected: FAIL — `ImportError: cannot import name '_sanitize_web_visit_mention'` (los tests de `_generate_post_content`/`_generate_carousel_slides_content` fallan con `TypeError: unexpected keyword argument 'business_url'`)

- [ ] **Step 3: Implementar `_sanitize_web_visit_mention`**

En `core/content_pipeline/generators/image_generator.py`, agregar inmediatamente después de `_truncate_at_word_boundary`:

```python
_WEB_VISIT_PATTERN = re.compile(
    r'visita(?:nos)?|entra a|nuestr[oa]s?\s+(?:sitio|p[aá]gina)|sitio\s+web|p[aá]gina\s+web|www\.',
    re.IGNORECASE,
)


def _sanitize_web_visit_mention(text: str, business_url: str, fallback: str) -> str:
    """Si no hay business_url y el texto invita a visitar un sitio web, lo
    reemplaza por un fallback seguro — evita prometer un sitio que no existe."""
    if not business_url and _WEB_VISIT_PATTERN.search(text):
        return fallback
    return text
```

- [ ] **Step 4: Aplicar la sanitización en `_generate_post_content`**

El bloque actual (dentro de `_generate_post_content`, tras parsear el JSON de Gemini) es:

```python
            if match:
                data = json.loads(match.group())
                return {
                    'headline': str(data.get('headline', '')).strip() or _FALLBACK['headline'],
                    'subtitle': str(data.get('subtitle', '')).strip() or _FALLBACK['subtitle'],
                    'cta': str(data.get('cta', '')).strip() or _FALLBACK['cta'],
                    'tag': str(data.get('tag', '')).strip().upper() or _FALLBACK['tag'],
                }
```

Reemplazarlo por:

```python
            if match:
                data = json.loads(match.group())
                return {
                    'headline': _sanitize_web_visit_mention(
                        str(data.get('headline', '')).strip() or _FALLBACK['headline'],
                        business_url, self._extract_headline(caption),
                    ),
                    'subtitle': _sanitize_web_visit_mention(
                        str(data.get('subtitle', '')).strip() or _FALLBACK['subtitle'],
                        business_url, _truncate_at_word_boundary(caption.strip()) if caption else '',
                    ),
                    'cta': _sanitize_web_visit_mention(
                        str(data.get('cta', '')).strip() or _FALLBACK['cta'],
                        business_url, 'Contáctanos hoy',
                    ),
                    'tag': str(data.get('tag', '')).strip().upper() or _FALLBACK['tag'],
                }
```

Y agregar el parámetro `business_url: str = ''` a la firma del método:

```python
    def _generate_post_content(self, caption: str, product_image_bytes: bytes = None, brand_context: str = '', business_url: str = '') -> dict:
```

- [ ] **Step 5: Aplicar la sanitización en `_generate_carousel_slides_content`**

El bloque actual (dentro de `_generate_carousel_slides_content`, tras parsear el JSON de Gemini) es:

```python
            if match:
                data = json.loads(match.group())
                slides = []
                for i in range(num_slides):
                    item = data[i] if i < len(data) else {}
                    slides.append({
                        'headline': str(item.get('headline', '')).strip() or fallback[i]['headline'],
                        'subtitle': str(item.get('subtitle', '')).strip() or fallback[i]['subtitle'],
                        'cta': str(item.get('cta', '')).strip() or fallback[i]['cta'],
                        'tag': str(item.get('tag', '')).strip().upper() or fallback[i]['tag'],
                    })
                return slides
```

Reemplazarlo por:

```python
            if match:
                data = json.loads(match.group())
                slides = []
                for i in range(num_slides):
                    item = data[i] if i < len(data) else {}
                    slides.append({
                        'headline': _sanitize_web_visit_mention(
                            str(item.get('headline', '')).strip() or fallback[i]['headline'],
                            business_url, fallback[i]['headline'],
                        ),
                        'subtitle': _sanitize_web_visit_mention(
                            str(item.get('subtitle', '')).strip() or fallback[i]['subtitle'],
                            business_url, fallback[i]['subtitle'],
                        ),
                        'cta': _sanitize_web_visit_mention(
                            str(item.get('cta', '')).strip() or fallback[i]['cta'],
                            business_url, fallback[i]['cta'],
                        ),
                        'tag': str(item.get('tag', '')).strip().upper() or fallback[i]['tag'],
                    })
                return slides
```

Y agregar el parámetro `business_url: str = ''` a la firma del método:

```python
    def _generate_carousel_slides_content(self, caption: str, brand_context: str = '', num_slides: int = 4, business_url: str = '') -> list[dict]:
```

- [ ] **Step 6: Enhebrar `business_url` por `generate()`, `generate_carousel()` y `_layered_pipeline()`**

Firma actual de `generate()`:

```python
    def generate(self, caption: str, colors: list[str], tone: str, filename: str, brand_name: str = '', keywords: list[str] = None, description: str = '', audience: str = '', product_image_bytes: bytes = None, max_qc_retries: int = 2) -> str:
        try:
            # job_id (sin el sufijo "-dayN") como seed de fuente — asi las 7 imagenes
            # de una semana comparten tipografia, incluso si se regenera un solo post.
            font_seed = filename.rsplit('-day', 1)[0] if '-day' in filename else filename
            image_bytes = self._layered_pipeline(caption, colors, tone, keywords or [], description, audience=audience, product_image_bytes=product_image_bytes, max_qc_retries=max_qc_retries, font_seed=font_seed)
            return self._upload_to_storage(image_bytes, filename)
        except Exception as e:
            logger.error(f"ImageGenerator error: {e}")
            return ''
```

Reemplazarla por:

```python
    def generate(self, caption: str, colors: list[str], tone: str, filename: str, brand_name: str = '', keywords: list[str] = None, description: str = '', audience: str = '', product_image_bytes: bytes = None, max_qc_retries: int = 2, business_url: str = '') -> str:
        try:
            # job_id (sin el sufijo "-dayN") como seed de fuente — asi las 7 imagenes
            # de una semana comparten tipografia, incluso si se regenera un solo post.
            font_seed = filename.rsplit('-day', 1)[0] if '-day' in filename else filename
            image_bytes = self._layered_pipeline(caption, colors, tone, keywords or [], description, audience=audience, product_image_bytes=product_image_bytes, max_qc_retries=max_qc_retries, font_seed=font_seed, business_url=business_url)
            return self._upload_to_storage(image_bytes, filename)
        except Exception as e:
            logger.error(f"ImageGenerator error: {e}")
            return ''
```

Firma actual de `generate_carousel()` (solo la línea de la firma y la llamada a `_generate_carousel_slides_content`):

```python
    def generate_carousel(self, caption: str, colors: list[str], tone: str, filename_prefix: str, brand_name: str = '', keywords: list[str] = None, description: str = '', audience: str = '', product_image_bytes: bytes = None, max_qc_retries: int = 2, num_slides: int = 4) -> list[str]:
```
```python
            slides_content = self._generate_carousel_slides_content(caption, brand_ctx, num_slides=num_slides)
```

Cambian a:

```python
    def generate_carousel(self, caption: str, colors: list[str], tone: str, filename_prefix: str, brand_name: str = '', keywords: list[str] = None, description: str = '', audience: str = '', product_image_bytes: bytes = None, max_qc_retries: int = 2, num_slides: int = 4, business_url: str = '') -> list[str]:
```
```python
            slides_content = self._generate_carousel_slides_content(caption, brand_ctx, num_slides=num_slides, business_url=business_url)
```

Firma actual de `_layered_pipeline()` y sus 2 llamadas a `_generate_post_content`:

```python
    def _layered_pipeline(self, caption: str, colors: list[str], tone: str, keywords: list[str] = None, description: str = '', audience: str = '', product_image_bytes: bytes = None, max_qc_retries: int = 2, font_seed: str = '') -> bytes:
        if product_image_bytes:
            kw_str = ', '.join((keywords or [])[:3])
            brand_context = f"{description[:150]}. Tono: {tone}. Palabras clave: {kw_str}." if description else f"Tono: {tone}."
            background_bytes, svg_overlay = self._generate_product_scene(
                product_image_bytes, caption, colors, tone, max_qc_retries=max_qc_retries
            )
            content = self._generate_post_content(caption, product_image_bytes=product_image_bytes, brand_context=brand_context)
            result = self._render_html_template(background_bytes, content, colors, svg_overlay=svg_overlay, font_seed=font_seed)
            if max_qc_retries > 0 and svg_overlay and not self._validate_final_image(result):
                logger.warning("Final QC falló — reintentando sin SVG overlay")
                result = self._render_html_template(background_bytes, content, colors, svg_overlay='', font_seed=font_seed)
            return result
        else:
            background_bytes = self._generate_background(caption, colors, tone, keywords or [], description, audience=audience, max_qc_retries=max_qc_retries)
            kw_str = ', '.join((keywords or [])[:4])
            brand_ctx = f"{description[:150]}. Tono: {tone}. Palabras clave: {kw_str}." if description else f"Tono: {tone}."
            content = self._generate_post_content(caption, product_image_bytes=None, brand_context=brand_ctx)
            svg_overlay = ''
        return self._render_html_template(background_bytes, content, colors, svg_overlay=svg_overlay, font_seed=font_seed)
```

Cambia a:

```python
    def _layered_pipeline(self, caption: str, colors: list[str], tone: str, keywords: list[str] = None, description: str = '', audience: str = '', product_image_bytes: bytes = None, max_qc_retries: int = 2, font_seed: str = '', business_url: str = '') -> bytes:
        if product_image_bytes:
            kw_str = ', '.join((keywords or [])[:3])
            brand_context = f"{description[:150]}. Tono: {tone}. Palabras clave: {kw_str}." if description else f"Tono: {tone}."
            background_bytes, svg_overlay = self._generate_product_scene(
                product_image_bytes, caption, colors, tone, max_qc_retries=max_qc_retries
            )
            content = self._generate_post_content(caption, product_image_bytes=product_image_bytes, brand_context=brand_context, business_url=business_url)
            result = self._render_html_template(background_bytes, content, colors, svg_overlay=svg_overlay, font_seed=font_seed)
            if max_qc_retries > 0 and svg_overlay and not self._validate_final_image(result):
                logger.warning("Final QC falló — reintentando sin SVG overlay")
                result = self._render_html_template(background_bytes, content, colors, svg_overlay='', font_seed=font_seed)
            return result
        else:
            background_bytes = self._generate_background(caption, colors, tone, keywords or [], description, audience=audience, max_qc_retries=max_qc_retries)
            kw_str = ', '.join((keywords or [])[:4])
            brand_ctx = f"{description[:150]}. Tono: {tone}. Palabras clave: {kw_str}." if description else f"Tono: {tone}."
            content = self._generate_post_content(caption, product_image_bytes=None, brand_context=brand_ctx, business_url=business_url)
            svg_overlay = ''
        return self._render_html_template(background_bytes, content, colors, svg_overlay=svg_overlay, font_seed=font_seed)
```

- [ ] **Step 7: Correr los tests y verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_image_generator.py -k "SanitizeWebVisitMention or sanitizes_cta or keeps_cta" -v`
Expected: PASS (7 passed)

- [ ] **Step 8: Correr toda la suite de `test_image_generator.py`**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_image_generator.py -v`
Expected: todos los tests pasan — los tests existentes no pasan `business_url` (usa el default `''`), y ninguno de sus captions/CTAs de prueba menciona un sitio web, así que la sanitización nunca se dispara para ellos.

- [ ] **Step 9: Commit**

```bash
git add core/content_pipeline/generators/image_generator.py core/content_pipeline/tests/test_image_generator.py
git commit -m "feat(seguridad): no inventar sitio web en CTA/headline/subtitulo cuando falta business_url"
```

---

### Task 2: Pasar `business_url` desde `tasks.py`

**Files:**
- Modify: `core/content_pipeline/tasks.py`
- Test: `core/content_pipeline/tests/test_tasks.py`

**Interfaces:**
- Consumes: `ImageGenerator.generate(..., business_url='')` / `generate_carousel(..., business_url='')` (Task 1).
- Produces: ninguna interfaz nueva — solo wiring, `_generate_post_media` no cambia de firma (ya reenvía `**kwargs`).

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `core/content_pipeline/tests/test_tasks.py`:

```python
@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    DEFAULT_FROM_EMAIL='noreply@test.com',
)
def test_content_generation_passes_business_url_to_image_gen(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender') as MockEmail, \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    call_kwargs = MockImage.return_value.generate.call_args_list[0].kwargs
    assert call_kwargs['business_url'] == 'https://tuwebmx.com'
```

`job_with_dna` (fixture ya existente en este archivo) crea la marca con
`business_url='https://tuwebmx.com'` — ver la definición del fixture al
inicio del archivo si necesitas confirmarlo.

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_tasks.py::test_content_generation_passes_business_url_to_image_gen -v`
Expected: FAIL — `KeyError: 'business_url'`

- [ ] **Step 3: Agregar `business_url` en los 3 call sites de `_generate_post_media`**

**Sitio 1** — dentro de `content_generation_task`, este bloque:

```python
            image_url, image_urls, video_url = _generate_post_media(
                image_gen, reel_script_gen, reel_gen,
                fmt=post_data.get('format', ContentPost.FORMAT_SINGLE),
                filename=f"{job_id}-day{i}",
                caption=post_data['caption'],
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                audience=brand_dna.audience,
                product_image_bytes=day_product,
                brand_dna=brand_dna,
                post_data=post_data,
            )
```

Cambia a (se agrega `business_url=brand_dna.business_url,` después de `audience=brand_dna.audience,`):

```python
            image_url, image_urls, video_url = _generate_post_media(
                image_gen, reel_script_gen, reel_gen,
                fmt=post_data.get('format', ContentPost.FORMAT_SINGLE),
                filename=f"{job_id}-day{i}",
                caption=post_data['caption'],
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                audience=brand_dna.audience,
                business_url=brand_dna.business_url,
                product_image_bytes=day_product,
                brand_dna=brand_dna,
                post_data=post_data,
            )
```

**Sitio 2** — dentro de `_generate_missing_image`, este bloque:

```python
        post.image_url, post.image_urls, post.video_url = _generate_post_media(
            image_gen, ReelScriptGenerator(), ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET),
            fmt=post.format,
            filename=f"{job_id}-day{post.day_number}",
            caption=post.caption,
            colors=brand_dna.primary_colors,
            tone=brand_dna.tone,
            brand_name=brand_dna.business_name,
            keywords=brand_dna.keywords,
            description=brand_dna.description,
            audience=brand_dna.audience,
            product_image_bytes=product_image_bytes,
            brand_dna=brand_dna,
            post_data={'caption': post.caption},
        )
```

Cambia a:

```python
        post.image_url, post.image_urls, post.video_url = _generate_post_media(
            image_gen, ReelScriptGenerator(), ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET),
            fmt=post.format,
            filename=f"{job_id}-day{post.day_number}",
            caption=post.caption,
            colors=brand_dna.primary_colors,
            tone=brand_dna.tone,
            brand_name=brand_dna.business_name,
            keywords=brand_dna.keywords,
            description=brand_dna.description,
            audience=brand_dna.audience,
            business_url=brand_dna.business_url,
            product_image_bytes=product_image_bytes,
            brand_dna=brand_dna,
            post_data={'caption': post.caption},
        )
```

**Sitio 3** — dentro de `generate_next_week`, este bloque:

```python
            image_url, image_urls, video_url = _generate_post_media(
                image_gen, reel_script_gen, reel_gen,
                fmt=post_data.get('format', ContentPost.FORMAT_SINGLE),
                filename=f"{job_id}-day{base_day + i}",
                caption=post_data['caption'],
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                audience=brand_dna.audience,
                product_image_bytes=day_product,
                brand_dna=brand_dna,
                post_data=post_data,
            )
```

Cambia a:

```python
            image_url, image_urls, video_url = _generate_post_media(
                image_gen, reel_script_gen, reel_gen,
                fmt=post_data.get('format', ContentPost.FORMAT_SINGLE),
                filename=f"{job_id}-day{base_day + i}",
                caption=post_data['caption'],
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                audience=brand_dna.audience,
                business_url=brand_dna.business_url,
                product_image_bytes=day_product,
                brand_dna=brand_dna,
                post_data=post_data,
            )
```

- [ ] **Step 4: Correr el test y verificar que pasa**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_tasks.py::test_content_generation_passes_business_url_to_image_gen -v`
Expected: PASS

- [ ] **Step 5: Correr toda la suite de `test_tasks.py`**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_tasks.py -v`
Expected: todos los tests pasan.

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/tasks.py core/content_pipeline/tests/test_tasks.py
git commit -m "feat(seguridad): pasar business_url hacia los generadores de imagen"
```

---

### Task 3: Extender el QC de seguridad del caption en `text_generator.py`

**Files:**
- Modify: `core/content_pipeline/generators/text_generator.py`
- Test: `core/content_pipeline/tests/test_text_generator.py`

**Interfaces:**
- Consumes: ninguna de las tareas anteriores — archivo y generador completamente distintos de `image_generator.py`.
- Produces: ninguna interfaz nueva consumida por otro código — es el generador de captions, ya integrado en `TextGenerator.generate()`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `core/content_pipeline/tests/test_text_generator.py`:

```python
@pytest.fixture
def brand_dna_no_url():
    job = AnalysisJob.objects.create(email='sinurl@t.com', business_url='')
    return BrandDNA.objects.create(
        job=job, business_name='Perro Rebelde', business_url='',
        description='Ropa para perros hecha de ropa reciclada',
        keywords=['ropa para perros', 'reciclaje'],
        audience='Dueños de mascotas', tone='casual', primary_colors=['#1a1a2e'],
    )


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_runs_safety_qc_when_no_business_url(brand_dna_no_url):
    from core.content_pipeline.generators.text_generator import TextGenerator
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc, \
         patch.object(TextGenerator, '_validate_caption_safety', return_value=True) as mock_qc, \
         patch.object(TextGenerator, '_regenerate_safe_caption') as mock_fix:
        mock_vc.return_value = _mock_vertex_client(MOCK_VERTEX_RESPONSE)
        gen = TextGenerator()
        result = gen.generate(brand_dna_no_url)

    assert mock_qc.call_count == 7
    mock_fix.assert_not_called()
    assert len(result) == 7


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_validate_caption_safety_rejects_website_mention_when_no_url(brand_dna_no_url):
    from core.content_pipeline.generators.text_generator import TextGenerator
    gen = TextGenerator()
    mock_resp_text = '{"has_absolute_promise": false, "has_unverifiable_claim": false, "has_website_mention": true, "ok": true}'
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(mock_resp_text)
        result = gen._validate_caption_safety('Visita nuestra pagina web', 'casual', 'Dueños de mascotas', '')
    assert result is False


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_validate_caption_safety_allows_website_mention_when_url_present(brand_dna):
    from core.content_pipeline.generators.text_generator import TextGenerator
    gen = TextGenerator()
    mock_resp_text = '{"has_absolute_promise": false, "has_unverifiable_claim": false, "has_website_mention": true, "ok": true}'
    with patch('core.content_pipeline.generators.text_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(mock_resp_text)
        result = gen._validate_caption_safety('Visita nuestra pagina web', 'profesional', 'PYMEs', 'https://tuwebmx.com')
    assert result is True
```

`AnalysisJob`, `BrandDNA`, `override_settings`, `patch`, `_mock_vertex_client`,
`MOCK_VERTEX_RESPONSE` y el fixture `brand_dna` YA están importados/definidos
al inicio de `test_text_generator.py` — no los vuelvas a definir.

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_text_generator.py -k "no_business_url or website_mention" -v`
Expected: FAIL — `TypeError: _validate_caption_safety() missing 1 required positional argument: 'business_url'` (o similar) en los 2 tests directos; `test_generate_runs_safety_qc_when_no_business_url` falla con `mock_qc.call_count == 0` (el chequeo no se dispara todavía para negocios sin URL que no son nicho sensible).

- [ ] **Step 3: Extender `_SAFETY_QC_PROMPT`**

El bloque actual es:

```python
_SAFETY_QC_PROMPT = (
    "Analiza este texto de marketing para redes sociales de forma estricta.\n"
    "Contexto de la marca — tono: {tone}, audiencia: {audience}\n\n"
    "Texto: \"{caption}\"\n\n"
    "Responde UNICAMENTE con este JSON (sin markdown):\n"
    '{{"has_absolute_promise": <bool>, "has_unverifiable_claim": <bool>, "ok": <bool>}}\n\n'
    "has_absolute_promise: true si usa palabras o frases como 'garantizado', 'garantizamos', "
    "'asegurar', 'aseguramos', 'asegurando', '100%', 'nunca falla', 'sin riesgo', o cualquier "
    "promesa absoluta de resultado.\n"
    "has_unverifiable_claim: true si afirma un resultado medico, financiero, legal o educativo "
    "especifico que no se puede verificar (ej: 'aseguramos un desarrollo optimo', "
    "'garantizamos tu recuperacion', 'triplica tus ingresos').\n"
    "ok: true SOLO si ambos son false."
)
```

Reemplazarlo por:

```python
_SAFETY_QC_PROMPT = (
    "Analiza este texto de marketing para redes sociales de forma estricta.\n"
    "Contexto de la marca — tono: {tone}, audiencia: {audience}\n\n"
    "Texto: \"{caption}\"\n\n"
    "Responde UNICAMENTE con este JSON (sin markdown):\n"
    '{{"has_absolute_promise": <bool>, "has_unverifiable_claim": <bool>, "has_website_mention": <bool>, "ok": <bool>}}\n\n'
    "has_absolute_promise: true si usa palabras o frases como 'garantizado', 'garantizamos', "
    "'asegurar', 'aseguramos', 'asegurando', '100%', 'nunca falla', 'sin riesgo', o cualquier "
    "promesa absoluta de resultado.\n"
    "has_unverifiable_claim: true si afirma un resultado medico, financiero, legal o educativo "
    "especifico que no se puede verificar (ej: 'aseguramos un desarrollo optimo', "
    "'garantizamos tu recuperacion', 'triplica tus ingresos').\n"
    "has_website_mention: true si el texto invita a visitar un sitio web, pagina o URL "
    "(ej. 'visita nuestra web', 'entra a nuestro sitio', menciona www. o una URL).\n"
    "ok: true SOLO si has_absolute_promise y has_unverifiable_claim son false. "
    "Ignora has_website_mention para calcular ok — se evalua aparte en el codigo."
)
```

- [ ] **Step 4: Extender `_SAFETY_FIX_PROMPT`**

El bloque actual es:

```python
_SAFETY_FIX_PROMPT = (
    "Reescribe el siguiente post de marketing para que NO haga promesas absolutas ni afirme "
    "resultados de salud, financieros, legales o educativos no verificables. Mantén el mismo "
    "mensaje central y longitud aproximada, pero en tono neutro-positivo, sin palabras como "
    "'garantizado', 'asegurar', 'aseguramos', '100%'.\n\n"
    "Post original: {caption}\n\n"
    "Tono de la marca: {tone}\n"
    "Responde UNICAMENTE con el texto corregido, sin comillas ni explicaciones."
)
```

Reemplazarlo por:

```python
_SAFETY_FIX_PROMPT = (
    "Reescribe el siguiente post de marketing para que NO haga promesas absolutas ni afirme "
    "resultados de salud, financieros, legales o educativos no verificables, y que NO invite a "
    "visitar un sitio web, pagina o URL. Mantén el mismo mensaje central y longitud aproximada, "
    "pero en tono neutro-positivo, sin palabras como 'garantizado', 'asegurar', 'aseguramos', "
    "'100%', ni frases como 'visita nuestra web'.\n\n"
    "Post original: {caption}\n\n"
    "Tono de la marca: {tone}\n"
    "Responde UNICAMENTE con el texto corregido, sin comillas ni explicaciones."
)
```

- [ ] **Step 5: Extender `_validate_caption_safety` y `_ensure_safe_caption`**

El bloque actual de `_validate_caption_safety` es:

```python
    def _validate_caption_safety(self, caption: str, tone: str, audience: str) -> bool:
        try:
            client = _vertex_client()
            prompt = _SAFETY_QC_PROMPT.format(caption=caption, tone=tone, audience=audience)
            with track_external_api('gemini', operation='caption_safety_qc'):
                resp = client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, contents=prompt)
            record_tokens(resp, operation='caption_safety_qc',
                          prompt_preview=prompt[:500],
                          response_preview=resp.text[:300] if resp.text else '')
            raw = resp.text.strip()
            raw = re.sub(r'^```(?:json)?\n?', '', raw)
            raw = re.sub(r'\n?```$', '', raw)
            match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
                ok = bool(data.get('ok', True))
                if not ok:
                    flags = [k for k in ('has_absolute_promise', 'has_unverifiable_claim') if data.get(k)]
                    logger.warning(f"Caption safety QC REJECTED: {', '.join(flags)} | caption={caption[:100]}")
                return ok
        except Exception as e:
            logger.warning(f"Caption safety QC error (asumiendo ok): {e}")
        return True
```

Reemplazarlo por:

```python
    def _validate_caption_safety(self, caption: str, tone: str, audience: str, business_url: str) -> bool:
        try:
            client = _vertex_client()
            prompt = _SAFETY_QC_PROMPT.format(caption=caption, tone=tone, audience=audience)
            with track_external_api('gemini', operation='caption_safety_qc'):
                resp = client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, contents=prompt)
            record_tokens(resp, operation='caption_safety_qc',
                          prompt_preview=prompt[:500],
                          response_preview=resp.text[:300] if resp.text else '')
            raw = resp.text.strip()
            raw = re.sub(r'^```(?:json)?\n?', '', raw)
            raw = re.sub(r'\n?```$', '', raw)
            match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
                ok = bool(data.get('ok', True))
                if not business_url and data.get('has_website_mention'):
                    ok = False
                if not ok:
                    flags = [k for k in ('has_absolute_promise', 'has_unverifiable_claim', 'has_website_mention') if data.get(k)]
                    logger.warning(f"Caption safety QC REJECTED: {', '.join(flags)} | caption={caption[:100]}")
                return ok
        except Exception as e:
            logger.warning(f"Caption safety QC error (asumiendo ok): {e}")
        return True
```

El bloque actual de `_ensure_safe_caption` es:

```python
    def _ensure_safe_caption(self, caption: str, brand_dna: BrandDNA, max_qc_retries: int) -> str:
        for attempt in range(max_qc_retries + 1):
            if self._validate_caption_safety(caption, brand_dna.tone, brand_dna.audience):
                return caption
            if attempt < max_qc_retries:
                logger.warning(f"Caption safety QC falló (intento {attempt + 1}/{max_qc_retries + 1}), regenerando...")
                caption = self._regenerate_safe_caption(caption, brand_dna.tone)
        logger.warning(f"Safety QC: reintentos agotados para '{brand_dna.business_name}', se usa el ultimo caption generado")
        return caption
```

Cambia a (solo la línea que llama `_validate_caption_safety`):

```python
    def _ensure_safe_caption(self, caption: str, brand_dna: BrandDNA, max_qc_retries: int) -> str:
        for attempt in range(max_qc_retries + 1):
            if self._validate_caption_safety(caption, brand_dna.tone, brand_dna.audience, brand_dna.business_url):
                return caption
            if attempt < max_qc_retries:
                logger.warning(f"Caption safety QC falló (intento {attempt + 1}/{max_qc_retries + 1}), regenerando...")
                caption = self._regenerate_safe_caption(caption, brand_dna.tone)
        logger.warning(f"Safety QC: reintentos agotados para '{brand_dna.business_name}', se usa el ultimo caption generado")
        return caption
```

- [ ] **Step 6: Extender el trigger en `generate()`**

El bloque actual es:

```python
        if _is_sensitive_niche(brand_dna):
            logger.info(f"Nicho sensible detectado para '{brand_dna.business_name}' — auditando captions")
            for post in posts:
                post['caption'] = self._ensure_safe_caption(post['caption'], brand_dna, max_qc_retries)
        return posts
```

Reemplazarlo por:

```python
        if _is_sensitive_niche(brand_dna) or not brand_dna.business_url:
            logger.info(f"Auditando captions para '{brand_dna.business_name}' (nicho sensible o sin business_url)")
            for post in posts:
                post['caption'] = self._ensure_safe_caption(post['caption'], brand_dna, max_qc_retries)
        return posts
```

- [ ] **Step 7: Correr los tests y verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_text_generator.py -k "no_business_url or website_mention" -v`
Expected: PASS (3 passed)

- [ ] **Step 8: Correr toda la suite de `test_text_generator.py`**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_text_generator.py -v`
Expected: todos los tests pasan — `test_generate_skips_safety_qc_for_normal_business` usa el fixture `brand_dna` (que ya tiene `business_url='https://tuwebmx.com'` y no es nicho sensible) y debe seguir sin disparar el QC; `test_generate_runs_safety_qc_for_sensitive_niche` usa `sensitive_brand_dna` (nicho sensible) y sigue disparándolo igual que antes. Ninguno de los 2 fixtures existentes tiene `business_url` vacío, así que no se ven afectados por el cambio de trigger.

- [ ] **Step 9: Commit**

```bash
git add core/content_pipeline/generators/text_generator.py core/content_pipeline/tests/test_text_generator.py
git commit -m "feat(seguridad): auditar caption si no hay business_url (no solo en nichos sensibles)"
```

---

## Verificación manual post-implementación (no automatizable)

Después de que las 3 tareas estén commiteadas y los contenedores recreados
(`docker compose up -d --force-recreate --no-deps --scale rqworker=3 backend rqworker`
— ver memoria `feedback_gunicorn_restart.md`), generar un calendario de prueba
para un negocio **sin URL** (dejar el campo de sitio web vacío en el análisis) y
confirmar que ningún post — imagen individual, carrusel, ni el caption — invita
a visitar un sitio web. Generar también uno **con URL** y confirmar que el
comportamiento no cambió (sigue pudiendo mencionar el sitio si tiene sentido).
