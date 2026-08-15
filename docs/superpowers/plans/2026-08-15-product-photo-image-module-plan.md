# Generación de imagen con foto real de producto (módulo 1 de 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hacer que el modo "solo imagen" (`MODE_SAMPLE_IMAGE`) use la foto real de producto que el usuario sube — nano banana la edita/compone directamente — y que la regeneración use la imagen actual + un análisis de visión guardado para mantener coherencia con el producto real.

**Architecture:** Se retira `MODE_SAMPLE_PRODUCT_REEL`/`ProductShowcaseGenerator` (motor de templates 3D, no comparte tecnología con esto). Se agrega `ProductPhotoAnalyzer` (espeja `LogoAnalyzer`) que corre en `analyze_brand_task` y guarda el análisis en `BrandDNA`. `ImageGenerator` gana 2 métodos nuevos: `generate_from_product_photo` (primera vez, ve la foto real) y `regenerate_with_reference` (regeneración, ve la imagen actual + contexto guardado). Un auditor de QC nuevo solo rechaza texto mal escrito, no la sola presencia de texto.

**Tech Stack:** Django, `google-genai` (Gemini multimodal), `gemini-3.1-flash-lite-image` (modelo económico primero), pytest + `unittest.mock`.

## Global Constraints

- Spec fuente de verdad: `docs/superpowers/specs/2026-08-15-product-photo-image-module-design.md` (commit `b5c882c`).
- Commits: `GIT_EDITOR=true git commit -m "msg"` (nunca heredoc). `git add` de archivos exactos, nunca `-A`/`-a`.
- Sin rama de feature — commits directo a `main`, local. NO hacer `git push` a menos que Anuar lo pida explícitamente.
- El análisis de visión (`ProductPhotoAnalyzer`) siempre corre y se guarda cuando hay foto, sin importar el modo — pero `TextGenerator`/`ImageGenerator` solo lo **usan** cuando el modo es de prueba/admin (`allows_sample_generation`). Producción real (`MODE_FULL`) no se toca en este plan.
- El módulo de reel y el pipeline de 7 días quedan explícitamente fuera de este plan — brainstorms separados después.
- La idea de visión para "imágenes extra" (carrusel/semana) también queda fuera — no aplica a "solo imagen" (genera 1 sola pieza).
- El cruce de análisis entre negocios distintos (reutilización) queda fuera — este plan solo guarda el análisis bien clasificado.

---

### Task 1: Retiro completo de MODE_SAMPLE_PRODUCT_REEL + ProductShowcaseGenerator

**Files:**
- Delete: `core/content_pipeline/generators/product_showcase_generator.py`
- Delete: `core/content_pipeline/tests/test_product_showcase_generator.py`
- Delete: `core/content_pipeline/hyperframes_reel/compositions/confetti-fall.html`
- Delete: `core/content_pipeline/hyperframes_reel/compositions/frame-assembly.html`
- Delete: `core/content_pipeline/hyperframes_reel/compositions/glass-shatter-reveal.html`
- Modify: `core/brand_dna/models.py:29-38`
- Modify: `core/brand_dna/views.py:152-159`
- Modify: `core/content_pipeline/tasks.py:1-207`
- Modify: `core/brand_dna/templates/brand_dna/new_analysis.html:120`
- Modify: `core/content_pipeline/tests/test_tasks.py:244-257,344-405`
- Modify: `core/brand_dna/tests/test_views.py:162-260` (aprox., 4 tests)
- Modify: `core/brand_dna/tests/test_models.py` (1 test)

**Interfaces:**
- Consumes: nada de tareas anteriores (primera tarea del plan).
- Produces: `AnalysisJob.MODE_CHOICES` con solo 3 modos (`full`, `sample_image`, `sample_reel`). Ningún código posterior debe referenciar `MODE_SAMPLE_PRODUCT_REEL`, `ProductShowcaseGenerator`, ni `_generate_product_reference_sample`.

**Nota importante:** este retiro incluye los 3 templates procedurales (`confetti-fall`, `frame-assembly`, `glass-shatter-reveal`) que recibieron sombras de contacto reales en un plan anterior de esta misma sesión (commits `cfd4390..e4b9201`). Anuar confirmó explícitamente "eliminarlo por completo" sabiendo que es el motor de templates 3D — no es un descuido, es la decisión tomada en el brainstorm.

**Verificación previa (no destructiva):** confirmar que nada más referencia estos símbolos antes de borrar:

```bash
grep -rln "ProductShowcaseGenerator\|MODE_SAMPLE_PRODUCT_REEL\|_generate_product_reference_sample\|confetti-fall\|frame-assembly\|glass-shatter-reveal" --include="*.py" --include="*.html" . | grep -v node_modules
```

Debe listar exactamente los archivos de este task más `core/content_pipeline/image_utils.py` (comentarios de `enhance_photo_classic`, no se toca — ver nota abajo) y `core/content_pipeline/hyperframes_reel/index.html` (archivo top-level preexistente, no forma parte de `_SHOWCASE_COMPOSITIONS`, no se usa en runtime — dejarlo tal cual, fuera de alcance).

**Nota sobre `enhance_photo_classic`:** vive en `core/content_pipeline/image_utils.py`, hoy solo la llama `ProductShowcaseGenerator`, pero tiene su propia suite de tests independiente (`core/content_pipeline/tests/test_image_utils.py`) como utilidad general de imagen. NO se borra en este plan — queda como utilidad sin uso actual, no rompe nada.

- [ ] **Step 1: Borrar los 3 archivos y el generador**

```bash
rm core/content_pipeline/generators/product_showcase_generator.py
rm core/content_pipeline/tests/test_product_showcase_generator.py
rm core/content_pipeline/hyperframes_reel/compositions/confetti-fall.html
rm core/content_pipeline/hyperframes_reel/compositions/frame-assembly.html
rm core/content_pipeline/hyperframes_reel/compositions/glass-shatter-reveal.html
```

- [ ] **Step 2: Quitar el modo de `AnalysisJob.MODE_CHOICES`**

En `core/brand_dna/models.py`, reemplazar:

```python
    MODE_FULL = 'full'
    MODE_SAMPLE_IMAGE = 'sample_image'
    MODE_SAMPLE_REEL = 'sample_reel'
    MODE_SAMPLE_PRODUCT_REEL = 'sample_product_reel'
    MODE_CHOICES = [
        (MODE_FULL, 'Calendario completo'),
        (MODE_SAMPLE_IMAGE, 'Muestra: imagen'),
        (MODE_SAMPLE_REEL, 'Muestra: reel'),
        (MODE_SAMPLE_PRODUCT_REEL, 'Muestra: reel con producto real (solo admin)'),
    ]
```

por:

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

No hace falta migración — `generation_mode` es un `CharField` con `choices`, `choices` no genera constraint a nivel de base de datos en Postgres (Django no lo aplica en el schema).

- [ ] **Step 3: Quitar el modo de `valid_modes` en `analyze_submit`**

En `core/brand_dna/views.py`, reemplazar:

```python
    requested_mode = request.POST.get('generation_mode', AnalysisJob.MODE_FULL)
    valid_modes = {
        AnalysisJob.MODE_FULL, AnalysisJob.MODE_SAMPLE_IMAGE, AnalysisJob.MODE_SAMPLE_REEL,
        AnalysisJob.MODE_SAMPLE_PRODUCT_REEL,
    }
```

por:

```python
    requested_mode = request.POST.get('generation_mode', AnalysisJob.MODE_FULL)
    valid_modes = {
        AnalysisJob.MODE_FULL, AnalysisJob.MODE_SAMPLE_IMAGE, AnalysisJob.MODE_SAMPLE_REEL,
    }
```

Un POST con `generation_mode=sample_product_reel` ahora cae automáticamente a `MODE_FULL` (mismo comportamiento que cualquier valor inválido, ya cubierto por el código existente).

- [ ] **Step 4: Quitar la función y su uso en `generate_sample_task`**

En `core/content_pipeline/tasks.py`, quitar el import (línea 19):

```python
from core.content_pipeline.generators.product_showcase_generator import ProductShowcaseGenerator
```

Borrar la función completa `_generate_product_reference_sample` (líneas 51-87, incluyendo las 2 líneas en blanco que la separan de `content_generation_task`).

Dentro de `generate_sample_task`, quitar:

```python
        if job.generation_mode == AnalysisJob.MODE_SAMPLE_PRODUCT_REEL:
            _generate_product_reference_sample(job, brand_dna)
            return

```

(las 3 líneas más la línea en blanco que sigue, justo después de `job.update_progress(AnalysisJob.STAGE_CONTENT, 80)`).

- [ ] **Step 5: Quitar la opción del template de admin**

En `core/brand_dna/templates/brand_dna/new_analysis.html`, borrar la línea 120:

```html
            <input type="radio" name="generation_mode" value="sample_product_reel" class="mode-product"> [ADMIN] Reel con producto real
```

- [ ] **Step 6: Actualizar tests — `test_tasks.py`**

Borrar el fixture `job_with_dna_sample_product_reel` completo (líneas 244-257) y los 3 tests que lo usan (líneas 344-405, desde el `@override_settings` que precede a `test_generate_sample_task_product_reel_mode_creates_post` hasta el final de `test_generate_sample_task_product_reel_mode_fails_without_photo`, dejando exactamente 2 líneas en blanco antes del fixture `calendar_with_dna` que sigue).

- [ ] **Step 7: Actualizar tests — `test_views.py`**

4 tests usan `generation_mode: 'sample_product_reel'` para probar comportamiento GENÉRICO de subida de foto (no específico del modo retirado) — cambiar el valor posteado a `'sample_image'` en los 4, y ajustar la única aserción que depende del modo:

En `test_analyze_submit_saves_product_reference_photo_when_permitted` (línea ~162), cambiar:
```python
            'generation_mode': 'sample_product_reel',
```
por:
```python
            'generation_mode': 'sample_image',
```
y cambiar:
```python
    assert job.generation_mode == AnalysisJob.MODE_SAMPLE_PRODUCT_REEL
```
por:
```python
    assert job.generation_mode == AnalysisJob.MODE_SAMPLE_IMAGE
```

En `test_analyze_submit_rejects_invalid_product_reference_photo`, `test_analyze_submit_product_photo_upload_failure_shows_error_and_does_not_orphan_job`, y `test_analyze_submit_ignores_product_mode_without_permission`: cambiar `'generation_mode': 'sample_product_reel'` por `'generation_mode': 'sample_image'` (sin más cambios — el resto de cada test ya no depende del modo específico).

- [ ] **Step 8: Actualizar tests — `test_models.py`**

Borrar el test `test_analysis_job_has_product_reel_mode`:

```python
def test_analysis_job_has_product_reel_mode():
    assert AnalysisJob.MODE_SAMPLE_PRODUCT_REEL == 'sample_product_reel'
```

- [ ] **Step 9: Correr la suite completa**

```bash
docker compose run --rm --entrypoint "" backend sh -c "mkdir -p /tmp/prometheus-multiproc /app/logs && python -m pytest -q"
```

Expected: todos los tests pasan, sin ningún error de import ni referencia rota.

- [ ] **Step 10: Commit**

```bash
git add core/brand_dna/models.py core/brand_dna/views.py core/content_pipeline/tasks.py \
  core/brand_dna/templates/brand_dna/new_analysis.html \
  core/content_pipeline/tests/test_tasks.py core/brand_dna/tests/test_views.py core/brand_dna/tests/test_models.py
git rm core/content_pipeline/generators/product_showcase_generator.py \
  core/content_pipeline/tests/test_product_showcase_generator.py \
  core/content_pipeline/hyperframes_reel/compositions/confetti-fall.html \
  core/content_pipeline/hyperframes_reel/compositions/frame-assembly.html \
  core/content_pipeline/hyperframes_reel/compositions/glass-shatter-reveal.html
GIT_EDITOR=true git commit -m "fix(brand_dna): retira MODE_SAMPLE_PRODUCT_REEL y ProductShowcaseGenerator

No comparte tecnologia con la edicion/composicion directa de fotos reales
ya validada con nano banana (Enfoque A) -- es el catalogo de templates 3D
(HyperFrames+Three.js), construido para otro fin. Decision de Anuar
2026-08-15, ver docs/superpowers/specs/2026-08-15-product-photo-image-module-design.md."
```

---

### Task 2: ProductPhotoAnalyzer + campos nuevos en BrandDNA + integración en analyze_brand_task

**Files:**
- Create: `core/brand_dna/extractors/product_photo_analyzer.py`
- Create: `core/brand_dna/tests/test_product_photo_analyzer.py`
- Modify: `core/brand_dna/models.py` (campos nuevos en `BrandDNA`)
- Create: migración nueva (`python manage.py makemigrations brand_dna`)
- Modify: `core/brand_dna/tasks.py` (`analyze_brand_task`)
- Modify: `core/brand_dna/tests/test_tasks.py` (o el archivo de tests de `analyze_brand_task` que corresponda — confirmar nombre exacto con `grep -rn "analyze_brand_task" core/brand_dna/tests/`)

**Interfaces:**
- Consumes: nada nuevo de Task 1.
- Produces: `ProductPhotoAnalyzer().analyze(image_bytes: bytes, mime_type: str) -> dict` con claves `description: str` y `category: str` (fallback `{'description': '', 'category': ''}` en error). `BrandDNA.product_photo_analysis: str` y `BrandDNA.product_category: str`, disponibles para Task 4+ vía `brand_dna.product_photo_analysis`.

- [ ] **Step 1: Escribir el test que falla primero**

`core/brand_dna/tests/test_product_photo_analyzer.py`:

```python
from unittest.mock import patch, MagicMock
from django.test import override_settings


def _mock_vertex_client(response_json):
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = response_json
    mock_client.models.generate_content.return_value = mock_resp
    return mock_client


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_analyze_returns_description_and_category():
    from core.brand_dna.extractors.product_photo_analyzer import ProductPhotoAnalyzer
    analyzer = ProductPhotoAnalyzer()
    with patch('core.brand_dna.extractors.product_photo_analyzer._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(
            '{"description": "Aretes de plata con piedra turquesa, estilo boho artesanal", "category": "joyeria"}'
        )
        result = analyzer.analyze(b'fake-image-bytes', 'image/jpeg')

    assert result['description'] == 'Aretes de plata con piedra turquesa, estilo boho artesanal'
    assert result['category'] == 'joyeria'


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_analyze_handles_error_fail_open():
    from core.brand_dna.extractors.product_photo_analyzer import ProductPhotoAnalyzer
    analyzer = ProductPhotoAnalyzer()
    with patch('core.brand_dna.extractors.product_photo_analyzer._vertex_client', side_effect=Exception('boom')):
        result = analyzer.analyze(b'fake-image-bytes', 'image/jpeg')

    assert result == {'description': '', 'category': ''}


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global')
def test_analyze_handles_malformed_json_fail_open():
    from core.brand_dna.extractors.product_photo_analyzer import ProductPhotoAnalyzer
    analyzer = ProductPhotoAnalyzer()
    with patch('core.brand_dna.extractors.product_photo_analyzer._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client('no es json')
        result = analyzer.analyze(b'fake-image-bytes', 'image/jpeg')

    assert result == {'description': '', 'category': ''}
```

- [ ] **Step 2: Correr el test, confirmar que falla**

```bash
docker compose run --rm --entrypoint "" backend sh -c "python -m pytest core/brand_dna/tests/test_product_photo_analyzer.py -v"
```

Expected: FAIL con `ModuleNotFoundError: No module named 'core.brand_dna.extractors.product_photo_analyzer'`.

- [ ] **Step 3: Implementar `ProductPhotoAnalyzer`**

`core/brand_dna/extractors/product_photo_analyzer.py` — espeja `logo_analyzer.py` (mismo cliente, mismo fail-open), pero con `response_schema` estructurado (Pydantic) en vez de texto libre, porque necesita 2 campos separados (`description` + `category`) en una sola llamada:

```python
import json
import logging
import google.genai as genai
from google.genai import types
from django.conf import settings
from pydantic import BaseModel
from core.shared.metrics_utils import track_external_api, record_tokens, vertex_labels

logger = logging.getLogger(__name__)

_FALLBACK = {'description': '', 'category': ''}

_PROMPT = (
    "Analiza esta foto de un producto real subida por un negocio.\n\n"
    "description: 1-2 oraciones describiendo el producto -- tipo, colores, "
    "materiales, estilo, detalles distintivos. Solo la descripcion, sin "
    "listas ni formato.\n"
    "category: el giro/tipo de producto en 1-3 palabras, forma normalizada "
    "en espanol (ej. 'joyeria', 'reposteria', 'ropa', 'muebles')."
)


class ProductPhotoAnalysisSchema(BaseModel):
    description: str
    category: str


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION_TEXT,
    )


class ProductPhotoAnalyzer:
    def analyze(self, image_bytes: bytes, mime_type: str) -> dict:
        try:
            client = _vertex_client()
            image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            with track_external_api('gemini', operation='product_photo_analysis'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[_PROMPT, image_part],
                    config=types.GenerateContentConfig(
                        labels=vertex_labels(),
                        response_mime_type="application/json",
                        response_schema=ProductPhotoAnalysisSchema,
                    ),
                )
            record_tokens(resp, operation='product_photo_analysis',
                          prompt_preview=_PROMPT[:500],
                          response_preview=resp.text[:500] if resp.text else '')
            data = json.loads(resp.text)
            return {
                'description': (data.get('description') or '').strip(),
                'category': (data.get('category') or '').strip(),
            }
        except Exception as e:
            logger.error(f"ProductPhotoAnalyzer error: {e}")
            return _FALLBACK.copy()
```

- [ ] **Step 4: Correr el test, confirmar que pasa**

```bash
docker compose run --rm --entrypoint "" backend sh -c "python -m pytest core/brand_dna/tests/test_product_photo_analyzer.py -v"
```

Expected: PASS los 3 tests.

- [ ] **Step 5: Campos nuevos en `BrandDNA` + migración**

En `core/brand_dna/models.py`, dentro de la clase `BrandDNA`, después de `common_hashtags`:

```python
    common_hashtags = models.JSONField(default=list)
    product_photo_analysis = models.TextField(blank=True, default='')
    product_category = models.CharField(max_length=100, blank=True, default='')
```

```bash
docker compose run --rm --entrypoint "" backend python manage.py makemigrations brand_dna
```

Expected: genera `core/brand_dna/migrations/00XX_brandna_product_category_and_more.py` (o similar, nombre real depende del autogenerador de Django).

- [ ] **Step 6: Escribir el test que falla para la integración en `analyze_brand_task`**

En `core/brand_dna/tests/test_tasks.py` (confirmado: es el archivo real de tests de `analyze_brand_task`, NO confundir con `core/content_pipeline/tests/test_tasks.py` que prueba `generate_sample_task` y otras tasks del pipeline de contenido — ambos archivos se llaman igual en carpetas distintas). Agregar los fixtures, mismo estilo que el `pending_job` ya existente (línea 15-21):

```python
@pytest.fixture
def job_with_product_photo():
    return AnalysisJob.objects.create(
        email='test@example.com',
        business_url='https://tuwebmx.com',
        business_description='Joyeria Luna\nJoyeria artesanal.',
        product_reference_image_path='uploads/product_ref_test.jpg',
    )


@pytest.fixture
def job_without_product_photo():
    return AnalysisJob.objects.create(
        email='test@example.com',
        business_url='https://tuwebmx.com',
        business_description='Joyeria Luna\nJoyeria artesanal.',
    )
```

Agregar los tests:

```python
@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='us-central1',
    GOOGLE_CLOUD_LOCATION_TEXT='global',
)
def test_analyze_brand_task_analyzes_product_photo_when_present(job_with_product_photo):
    with patch('core.brand_dna.tasks.WebScraper'), \
         patch('core.brand_dna.tasks.ManualBrandExtractor') as MockExtractor, \
         patch('core.brand_dna.tasks.upload_exists', return_value=True), \
         patch('core.brand_dna.tasks.read_upload', return_value=b'fake-photo-bytes'), \
         patch('core.brand_dna.tasks.normalize_image', return_value=b'fake-photo-bytes'), \
         patch('core.brand_dna.tasks.LogoAnalyzer'), \
         patch('core.brand_dna.tasks.ProductPhotoAnalyzer') as MockAnalyzer, \
         patch('core.brand_dna.tasks.django_rq'):
        MockExtractor.return_value.extract.return_value = {
            'description': 'x', 'keywords': [], 'audience': 'x', 'tone': 'profesional',
        }
        MockAnalyzer.return_value.analyze.return_value = {
            'description': 'Aretes de plata', 'category': 'joyeria',
        }
        from core.brand_dna.tasks import analyze_brand_task
        analyze_brand_task(str(job_with_product_photo.id))

    brand_dna = job_with_product_photo.brand_dna
    assert brand_dna.product_photo_analysis == 'Aretes de plata'
    assert brand_dna.product_category == 'joyeria'


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='us-central1',
    GOOGLE_CLOUD_LOCATION_TEXT='global',
)
def test_analyze_brand_task_skips_photo_analysis_without_photo(job_without_product_photo):
    with patch('core.brand_dna.tasks.WebScraper'), \
         patch('core.brand_dna.tasks.ManualBrandExtractor') as MockExtractor, \
         patch('core.brand_dna.tasks.LogoAnalyzer'), \
         patch('core.brand_dna.tasks.ProductPhotoAnalyzer') as MockAnalyzer, \
         patch('core.brand_dna.tasks.django_rq'):
        MockExtractor.return_value.extract.return_value = {
            'description': 'x', 'keywords': [], 'audience': 'x', 'tone': 'profesional',
        }
        from core.brand_dna.tasks import analyze_brand_task
        analyze_brand_task(str(job_without_product_photo.id))

    MockAnalyzer.return_value.analyze.assert_not_called()
    brand_dna = job_without_product_photo.brand_dna
    assert brand_dna.product_photo_analysis == ''
```

- [ ] **Step 7: Correr, confirmar que falla**

```bash
docker compose run --rm --entrypoint "" backend sh -c "python -m pytest core/brand_dna/tests/test_tasks.py -k product_photo -v"
```

Expected: FAIL — `ProductPhotoAnalyzer` no existe en `core.brand_dna.tasks` todavía (no está importado), y `product_photo_analysis`/`product_category` no se pasan a `BrandDNA.objects.create`.

- [ ] **Step 8: Integrar en `analyze_brand_task`**

En `core/brand_dna/tasks.py`, agregar el import junto a `LogoAnalyzer`:

```python
from core.brand_dna.extractors.logo_analyzer import LogoAnalyzer
from core.brand_dna.extractors.product_photo_analyzer import ProductPhotoAnalyzer
```

Después del bloque de `logo_data` (línea 47, `job.update_progress(AnalysisJob.STAGE_LOGO, 55)`), agregar:

```python
        product_photo_data = {'description': '', 'category': ''}
        if job.product_reference_image_path:
            if upload_exists(job.product_reference_image_path):
                product_photo_bytes = normalize_image(read_upload(job.product_reference_image_path))
                product_photo_data = ProductPhotoAnalyzer().analyze(product_photo_bytes, 'image/webp')
```

Y en `BrandDNA.objects.create(...)`, agregar los 2 campos nuevos:

```python
        BrandDNA.objects.create(
            job=job,
            business_name=literal_business_name or 'Mi Negocio',
            business_url=job.business_url,
            description=web_data.get('description', ''),
            keywords=web_data.get('keywords', []),
            audience=web_data.get('audience', ''),
            tone=web_data.get('tone', 'profesional'),
            primary_colors=logo_data.get('primary_colors') or web_data.get('brand_colors', []),
            logo_elements=logo_data.get('logo_elements', ''),
            product_photo_analysis=product_photo_data.get('description', ''),
            product_category=product_photo_data.get('category', ''),
        )
```

- [ ] **Step 9: Correr, confirmar que pasa**

```bash
docker compose run --rm --entrypoint "" backend sh -c "python -m pytest core/brand_dna/tests/test_tasks.py -k product_photo -v"
```

Expected: PASS ambos tests.

- [ ] **Step 10: Correr la suite completa**

```bash
docker compose run --rm --entrypoint "" backend sh -c "mkdir -p /tmp/prometheus-multiproc /app/logs && python -m pytest -q"
```

- [ ] **Step 11: Commit**

```bash
git add core/brand_dna/extractors/product_photo_analyzer.py core/brand_dna/tests/test_product_photo_analyzer.py \
  core/brand_dna/models.py core/brand_dna/migrations/ core/brand_dna/tasks.py core/brand_dna/tests/test_tasks.py
GIT_EDITOR=true git commit -m "feat(brand_dna): analiza la foto de producto durante el analisis de marca

Espeja LogoAnalyzer: 1 llamada multimodal a Gemini, fail-open. Guarda
descripcion + categoria normalizada en BrandDNA (product_photo_analysis,
product_category) -- disponible para TextGenerator/ImageGenerator en modos
de prueba, y como dato clasificado reusable a futuro."
```

---

### Task 3: Setting VERTEX_IMAGE_MODEL_LITE + entrada en RPM_LIMITS

**Files:**
- Modify: `saas_chatbot/settings.py:166`
- Modify: `core/shared/rate_limiter.py:32-39`
- Modify: `core/shared/tests/test_rate_limiter.py`

**Interfaces:**
- Consumes: nada nuevo.
- Produces: `settings.VERTEX_IMAGE_MODEL_LITE = 'gemini-3.1-flash-lite-image'`, y `RPM_LIMITS['vertex']['gemini-3.1-flash-lite-image'] = 1` reconocido por `throttle()`/`call_with_429_retry()`. Task 4 usa este setting directamente.

- [ ] **Step 1: Agregar el setting**

En `saas_chatbot/settings.py`, después de la línea 166 (`VERTEX_IMAGE_MODEL = 'gemini-3.1-flash-image'`):

```python
VERTEX_IMAGE_MODEL = 'gemini-3.1-flash-image'
VERTEX_IMAGE_MODEL_LITE = 'gemini-3.1-flash-lite-image'
```

- [ ] **Step 2: Escribir el test que falla para el rate limit**

En `core/shared/tests/test_rate_limiter.py`, agregar:

```python
def test_rpm_limits_has_conservative_entry_for_lite_image_model():
    from core.shared import rate_limiter
    assert rate_limiter.RPM_LIMITS['vertex']['gemini-3.1-flash-lite-image'] == 1
```

- [ ] **Step 3: Correr, confirmar que falla**

```bash
docker compose run --rm --entrypoint "" backend sh -c "python -m pytest core/shared/tests/test_rate_limiter.py -k lite_image -v"
```

Expected: FAIL con `KeyError: 'gemini-3.1-flash-lite-image'`.

- [ ] **Step 4: Agregar la entrada**

En `core/shared/rate_limiter.py`:

```python
RPM_LIMITS = {
    'vertex': {
        'gemini-3.1-flash-image': 1,
        'gemini-3.1-flash-lite-image': 1,
    },
    'gemini_api': {
        'gemini-3.1-flash-image': 20,
    },
}
```

Agregar comentario arriba de la nueva línea explicando el valor conservador (sin dato empírico propio para lite en Vertex, uso admin/prueba de bajo volumen):

```python
        # gemini-3.1-flash-lite-image: valor conservador de partida (mismo que
        # el modelo normal) -- sin prueba empirica propia en Vertex todavia.
        # Uso admin/prueba unicamente (bajo volumen), el impacto de un limite
        # conservador es minimo. Ver docs/superpowers/specs/2026-08-15-product-photo-image-module-design.md.
        'gemini-3.1-flash-lite-image': 1,
```

- [ ] **Step 5: Correr, confirmar que pasa**

```bash
docker compose run --rm --entrypoint "" backend sh -c "python -m pytest core/shared/tests/test_rate_limiter.py -v"
```

Expected: PASS todos, incluyendo el nuevo.

- [ ] **Step 6: Commit**

```bash
git add saas_chatbot/settings.py core/shared/rate_limiter.py core/shared/tests/test_rate_limiter.py
GIT_EDITOR=true git commit -m "feat: agrega gemini-3.1-flash-lite-image como modelo economico de prueba

VERTEX_IMAGE_MODEL_LITE nuevo setting + entrada conservadora en RPM_LIMITS
(base_model distinto al normal, sin proteccion automatica sin esto)."
```

---

### Task 4: ImageGenerator.generate_from_product_photo + auditor de calidad nuevo

**Files:**
- Modify: `core/content_pipeline/generators/image_generator.py`
- Modify: `core/content_pipeline/tests/test_image_generator.py`

**Interfaces:**
- Consumes: `settings.VERTEX_IMAGE_MODEL_LITE` (Task 3), `_gemini_api_client()`/`_vertex_client()`/`_upload_to_storage()` (ya existentes).
- Produces: `ImageGenerator.generate_from_product_photo(photo_bytes: bytes, mime_type: str, caption: str, colors: list[str], tone: str, filename: str, vision_context: str = '', max_qc_retries: int = 2) -> str` (URL o `''`). Task 5 lo consume.

- [ ] **Step 1: Escribir el test que falla para el schema/auditor nuevo**

En `core/content_pipeline/tests/test_image_generator.py`, agregar:

```python
class TestValidateProductPhotoGeneration:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_passes_when_no_text(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = (
            '{"has_text": false, "text_is_correct_spanish": true, "is_abstract_3d": false, '
            '"has_screen_content": false, "has_malformed_object": false, '
            '"has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": true}'
        )
        mock_client.models.generate_content.return_value = mock_resp
        with patch('core.content_pipeline.generators.image_generator._vertex_text_client', return_value=mock_client):
            assert gen._validate_product_photo_generation(b'fake-png') is True

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_passes_when_text_is_correct_spanish(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = (
            '{"has_text": true, "text_is_correct_spanish": true, "is_abstract_3d": false, '
            '"has_screen_content": false, "has_malformed_object": false, '
            '"has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": true}'
        )
        mock_client.models.generate_content.return_value = mock_resp
        with patch('core.content_pipeline.generators.image_generator._vertex_text_client', return_value=mock_client):
            assert gen._validate_product_photo_generation(b'fake-png') is True

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_rejects_when_text_is_incorrect_spanish(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.text = (
            '{"has_text": true, "text_is_correct_spanish": false, "is_abstract_3d": false, '
            '"has_screen_content": false, "has_malformed_object": false, '
            '"has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": false}'
        )
        mock_client.models.generate_content.return_value = mock_resp
        with patch('core.content_pipeline.generators.image_generator._vertex_text_client', return_value=mock_client):
            assert gen._validate_product_photo_generation(b'fake-png') is False

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_fail_open_on_error(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_text_client', side_effect=Exception('boom')):
            assert gen._validate_product_photo_generation(b'fake-png') is True
```

- [ ] **Step 2: Correr, confirmar que falla**

```bash
docker compose run --rm --entrypoint "" backend sh -c "python -m pytest core/content_pipeline/tests/test_image_generator.py -k ValidateProductPhotoGeneration -v"
```

Expected: FAIL — `_validate_product_photo_generation` no existe todavía.

- [ ] **Step 3: Agregar `ProductPhotoQCSchema` y `_validate_product_photo_generation`**

En `core/content_pipeline/generators/image_generator.py`, agregar el schema junto a `ImageQCSchema` (después de la línea 52):

```python
class ProductPhotoQCSchema(BaseModel):
    has_text: bool
    text_is_correct_spanish: bool
    is_abstract_3d: bool
    has_screen_content: bool
    has_malformed_object: bool
    has_unrealistic_grounding: bool
    has_suggestive_or_exposed_content: bool
    ok: bool
```

Agregar el método dentro de la clase `ImageGenerator`, después de `_validate_background` (línea 477, antes de `_validate_final_image`):

```python
    def _validate_product_photo_generation(self, image_bytes: bytes) -> bool:
        """QC para el camino de foto real de producto. A diferencia de
        _validate_background, el texto NO se rechaza por su sola presencia
        (nano banana puede dejar texto residual de la foto original, o
        generar algo pese a la instruccion de no hacerlo) -- solo se rechaza
        si esta mal escrito. Decision de Anuar 2026-08-15."""
        try:
            client = _vertex_text_client()
            image_part = types.Part.from_bytes(data=image_bytes, mime_type='image/png')
            prompt = (
                "Analyze this image strictly.\n\n"
                "has_text: true if ANY readable letters, words, or numbers appear anywhere "
                "in the image. Be very strict.\n"
                "text_is_correct_spanish: if has_text is true, is that text grammatically "
                "correct, properly spelled Spanish (no typos, no gibberish, no broken words)? "
                "If has_text is false, respond true (not applicable).\n"
                "is_abstract_3d: true if the image has floating 3D geometric shapes, abstract CGI objects, or surreal renders.\n"
                "has_screen_content: true if any computer monitor, laptop screen, phone screen, TV, or digital display "
                "shows visible content — including websites, text, images, graphics, UI elements, or any non-blank content. "
                "A screen must be completely BLACK or clearly turned off to not count. Be very strict.\n"
                "has_malformed_object: true if any object, tool, instrument, hand, or mechanical item is anatomically or "
                "physically impossible or distorted. Only flag clear, obvious cases.\n"
                "has_unrealistic_grounding: true if the main subject appears to float, hover, or is otherwise "
                "disconnected from the surface/floor/background it should be resting on. Only flag clear, obvious cases.\n"
                "has_suggestive_or_exposed_content: true if the image shows exposed intimate body parts, implied "
                "nudity, or sexually suggestive content. Be conservative and strict.\n"
                "ok: true ONLY if (has_text=false OR text_is_correct_spanish=true) AND is_abstract_3d=false "
                "AND has_screen_content=false AND has_malformed_object=false AND has_unrealistic_grounding=false "
                "AND has_suggestive_or_exposed_content=false."
            )
            with track_external_api('gemini', operation='product_photo_qc'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[image_part, prompt],
                    config=types.GenerateContentConfig(
                        labels=vertex_labels(),
                        response_mime_type="application/json",
                        response_schema=ProductPhotoQCSchema,
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    ),
                )
            record_tokens(resp, operation='product_photo_qc',
                          prompt_preview=prompt[:500],
                          response_preview=resp.text[:500] if resp.text else '')
            data = json.loads(resp.text)
            ok = bool(data.get('ok', True))
            if ok:
                logger.info(f"Product photo QC OK: {data}")
            else:
                logger.warning(f"Product photo QC REJECTED: {data}")
            return ok
        except Exception as e:
            logger.warning(f"Product photo QC error (assuming ok): {e}")
        return True
```

- [ ] **Step 4: Correr, confirmar que pasa**

```bash
docker compose run --rm --entrypoint "" backend sh -c "python -m pytest core/content_pipeline/tests/test_image_generator.py -k ValidateProductPhotoGeneration -v"
```

Expected: PASS los 4 tests.

- [ ] **Step 5: Escribir el test que falla para `generate_from_product_photo`**

```python
class TestGenerateFromProductPhoto:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_sends_photo_and_creative_direction_uses_lite_model(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_part = MagicMock()
        mock_part.inline_data.data = b'fake-generated-png'
        mock_gen_client = MagicMock()
        mock_gen_client.models.generate_content.return_value = MagicMock(
            candidates=[MagicMock(content=MagicMock(parts=[mock_part]))]
        )
        with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_gen_client), \
             patch.object(gen, '_validate_product_photo_generation', return_value=True), \
             patch.object(gen, '_upload_to_storage', return_value='https://storage.googleapis.com/test/img.png'):
            url = gen.generate_from_product_photo(
                photo_bytes=b'fake-photo-bytes', mime_type='image/jpeg',
                caption='Aretes artesanales', colors=['#e94560'], tone='alegre',
                filename='test-product',
            )

        assert url == 'https://storage.googleapis.com/test/img.png'
        call_kwargs = mock_gen_client.models.generate_content.call_args.kwargs
        assert call_kwargs['model'] == 'gemini-3.1-flash-lite-image'
        contents = call_kwargs['contents']
        assert len(contents) == 2
        assert isinstance(contents[0], str)  # el prompt de direccion creativa
        assert contents[1].inline_data.data == b'fake-photo-bytes'  # types.Part.from_bytes real, no mockeado
        assert contents[1].inline_data.mime_type == 'image/jpeg'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_prompt_instructs_remove_original_text_and_no_new_text(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_part = MagicMock()
        mock_part.inline_data.data = b'fake-generated-png'
        mock_gen_client = MagicMock()
        mock_gen_client.models.generate_content.return_value = MagicMock(
            candidates=[MagicMock(content=MagicMock(parts=[mock_part]))]
        )
        with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_gen_client), \
             patch.object(gen, '_validate_product_photo_generation', return_value=True), \
             patch.object(gen, '_upload_to_storage', return_value='https://storage.test/img.png'):
            gen.generate_from_product_photo(
                photo_bytes=b'fake-photo-bytes', mime_type='image/jpeg',
                caption='Aretes artesanales', colors=['#e94560'], tone='alegre',
                filename='test-product',
            )

        call_kwargs = mock_gen_client.models.generate_content.call_args.kwargs
        prompt_text = ' '.join(str(c) for c in call_kwargs['contents'] if isinstance(c, str))
        assert 'elimina' in prompt_text.lower() or 'remove' in prompt_text.lower() or 'quita' in prompt_text.lower()
        assert 'no agregues texto' in prompt_text.lower() or 'do not add text' in prompt_text.lower() or 'no text' in prompt_text.lower()

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_returns_empty_string_on_error(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client', side_effect=Exception('boom')):
            url = gen.generate_from_product_photo(
                photo_bytes=b'fake-photo-bytes', mime_type='image/jpeg',
                caption='Aretes artesanales', colors=['#e94560'], tone='alegre',
                filename='test-product',
            )
        assert url == ''
```

- [ ] **Step 6: Correr, confirmar que falla**

```bash
docker compose run --rm --entrypoint "" backend sh -c "python -m pytest core/content_pipeline/tests/test_image_generator.py -k GenerateFromProductPhoto -v"
```

Expected: FAIL — `generate_from_product_photo` no existe todavía.

- [ ] **Step 7: Implementar `generate_from_product_photo`**

Agregar el método a la clase `ImageGenerator`, junto a `generate`/`generate_carousel` (después de la línea 244, antes de `generate_carousel`):

```python
    def generate_from_product_photo(self, photo_bytes: bytes, mime_type: str, caption: str,
                                      colors: list[str], tone: str, filename: str,
                                      vision_context: str = '', max_qc_retries: int = 2) -> str:
        """Primera generacion usando la foto real de producto -- nano banana
        ve la foto directamente en la misma llamada que la direccion
        creativa (Enfoque A, ya validado). Usa el modelo economico
        (VERTEX_IMAGE_MODEL_LITE) por decision de Anuar, para probar costo
        antes de escalar al modelo normal."""
        try:
            color_str = ', '.join(colors[:3]) if colors else 'warm neutrals'
            context_line = f" Contexto del producto: {vision_context}." if vision_context else ''
            prompt = (
                f"Edit this real product photo into a professional social media post background.\n"
                f"Extract only the real product from the photo. Remove/eliminate any text, "
                f"watermark, or logo present in the original photo — do not carry them into "
                f"the new composition. Do not add new text, headline, or CTA for now.\n"
                f"Creative direction: {caption}.{context_line} Mood: {tone}. "
                f"Brand colors ({color_str}) should be visually present in props/backdrop/accents. "
                f"DSLR camera quality, shallow depth of field, photorealistic. Square 1:1 format."
            )
            photo_part = types.Part.from_bytes(data=photo_bytes, mime_type=mime_type)
            last_bytes = None
            total_attempts = max_qc_retries + 1
            for attempt in range(total_attempts):
                last_bytes = self._generate_from_photo_with_retry(prompt, photo_part)
                if self._validate_product_photo_generation(last_bytes):
                    return self._upload_to_storage(last_bytes, filename)
                if attempt < max_qc_retries:
                    logger.warning(f"Product photo QC failed (attempt {attempt + 1}/{total_attempts}), regenerando...")
            logger.warning("Product photo QC: reintentos agotados, usando ultima imagen generada")
            return self._upload_to_storage(last_bytes, filename)
        except Exception as e:
            logger.error(f"ImageGenerator.generate_from_product_photo error: {e}")
            return ''

    def _generate_from_photo_with_retry(self, prompt: str, photo_part) -> bytes:
        provider = 'gemini_api' if self._use_gemini_api else 'vertex'
        return call_with_429_retry(
            lambda: self._generate_from_photo(prompt, photo_part),
            settings.VERTEX_IMAGE_MODEL_LITE, provider=provider,
        )

    def _generate_from_photo(self, prompt: str, photo_part) -> bytes:
        client = _gemini_api_client() if self._use_gemini_api else _vertex_client()
        config_kwargs = dict(
            response_modalities=['IMAGE', 'TEXT'],
            image_config=types.ImageConfig(aspect_ratio='1:1'),
        )
        if not self._use_gemini_api:
            config_kwargs['labels'] = vertex_labels()
        with track_external_api('gemini_image', operation='image_generate_from_photo'):
            resp = client.models.generate_content(
                model=settings.VERTEX_IMAGE_MODEL_LITE,
                contents=[prompt, photo_part],
                config=types.GenerateContentConfig(**config_kwargs),
            )
        for part in resp.candidates[0].content.parts:
            if part.inline_data:
                record_gemini_image_generation('generate_from_photo')
                return part.inline_data.data
        raise ValueError("No image returned")
```

- [ ] **Step 8: Correr, confirmar que pasa**

```bash
docker compose run --rm --entrypoint "" backend sh -c "python -m pytest core/content_pipeline/tests/test_image_generator.py -k GenerateFromProductPhoto -v"
```

Expected: PASS los 3 tests. Reemplazar la aserción laxa marcada en el Step 5 por una real (ej. confirmar `call_kwargs['contents'][1] is photo_part` o inspeccionar el `Part` construido).

- [ ] **Step 9: Correr la suite completa**

```bash
docker compose run --rm --entrypoint "" backend sh -c "mkdir -p /tmp/prometheus-multiproc /app/logs && python -m pytest -q"
```

- [ ] **Step 10: Commit**

```bash
git add core/content_pipeline/generators/image_generator.py core/content_pipeline/tests/test_image_generator.py
GIT_EDITOR=true git commit -m "feat(image_generator): genera imagen desde foto real de producto

generate_from_product_photo: nano banana ve la foto real + direccion
creativa en la misma llamada (Enfoque A). Elimina texto/marca de agua
original, no agrega texto nuevo. Auditor nuevo (_validate_product_photo_generation)
solo rechaza texto mal escrito, no su sola presencia. Modelo economico
(gemini-3.1-flash-lite-image) primero."
```

---

### Task 5: Ruteo en generate_sample_task

**Files:**
- Modify: `core/content_pipeline/tasks.py` (`generate_sample_task`)
- Modify: `core/content_pipeline/tests/test_tasks.py`

**Interfaces:**
- Consumes: `ImageGenerator.generate_from_product_photo` (Task 4), `job.product_reference_image_path` (ya existente), `brand_dna.product_photo_analysis` (Task 2).
- Produces: nada nuevo para tareas posteriores (última pieza del flujo de primera generación).

- [ ] **Step 1: Escribir el test que falla**

En `core/content_pipeline/tests/test_tasks.py`, agregar un fixture `job_with_dna_sample_image_and_photo` (mismo patrón que `job_with_dna_sample_image`, agregando `product_reference_image_path='uploads/product_ref_test.jpg'`) y:

```python
@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='us-central1',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
)
def test_generate_sample_task_uses_product_photo_when_present(job_with_dna_sample_image_and_photo):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.read_upload', return_value=b'fake-photo-bytes'), \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
        MockText.return_value.generate.return_value = _MOCK_POSTS_FOR_SAMPLE
        MockImage.return_value.generate_from_product_photo.return_value = 'https://storage.test/product.png'

        from core.content_pipeline.tasks import generate_sample_task
        generate_sample_task(str(job_with_dna_sample_image_and_photo.id))

    MockImage.return_value.generate_from_product_photo.assert_called_once()
    MockImage.return_value.generate.assert_not_called()
    call_kwargs = MockImage.return_value.generate_from_product_photo.call_args.kwargs
    assert call_kwargs['photo_bytes'] == b'fake-photo-bytes'
    post = ContentPost.objects.get(calendar__brand_dna__job=job_with_dna_sample_image_and_photo)
    assert post.image_url == 'https://storage.test/product.png'


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
)
def test_generate_sample_task_ignores_photo_for_reel_mode(job_with_dna_sample_reel):
    # El modo reel no rutea a generate_from_product_photo en este plan --
    # eso es el modulo 2 (fuera de alcance aqui).
    job_with_dna_sample_reel.product_reference_image_path = 'uploads/product_ref_test.jpg'
    job_with_dna_sample_reel.save(update_fields=['product_reference_image_path'])
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ReelScriptGenerator') as MockScript, \
         patch('core.content_pipeline.tasks.ReelGenerator') as MockReel, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
        MockText.return_value.generate.return_value = _MOCK_POSTS_FOR_SAMPLE
        MockScript.return_value.generate.return_value = {'hook_text': 'H', 'scene_prompts': ['a']}
        MockReel.return_value.generate.return_value = ('https://storage.test/reel.mp4', 'https://storage.test/poster.png')

        from core.content_pipeline.tasks import generate_sample_task
        generate_sample_task(str(job_with_dna_sample_reel.id))

    MockImage.return_value.generate_from_product_photo.assert_not_called()
```

- [ ] **Step 2: Correr, confirmar que falla**

```bash
docker compose run --rm --entrypoint "" backend sh -c "python -m pytest core/content_pipeline/tests/test_tasks.py -k product_photo -v"
```

Expected: FAIL — `generate_from_product_photo` nunca se llama todavía (el primer test falla en el `assert_called_once()`).

- [ ] **Step 3: Rutear en `generate_sample_task`**

En `core/content_pipeline/tasks.py`, dentro de `generate_sample_task`, reemplazar el bloque de generación (líneas ~165-184) por:

```python
        calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        reel_script_gen = ReelScriptGenerator()
        reel_gen = ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)

        if wanted_format == ContentPost.FORMAT_SINGLE and job.product_reference_image_path:
            photo_bytes = read_upload(job.product_reference_image_path)
            image_url = image_gen.generate_from_product_photo(
                photo_bytes=photo_bytes, mime_type='image/jpeg',
                caption=post_data['caption'], colors=brand_dna.primary_colors,
                tone=brand_dna.tone, filename=f"{job_id}-sample",
                vision_context=brand_dna.product_photo_analysis,
            )
            image_urls, video_url = [], ''
        else:
            image_url, image_urls, video_url = _generate_post_media(
                image_gen, reel_script_gen, reel_gen,
                fmt=wanted_format,
                filename=f"{job_id}-sample",
                caption=post_data['caption'],
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                audience=brand_dna.audience,
                business_url=brand_dna.business_url,
                brand_dna=brand_dna,
                post_data=post_data,
            )
```

`read_upload` ya está importado en este archivo (línea 20).

- [ ] **Step 4: Correr, confirmar que pasa**

```bash
docker compose run --rm --entrypoint "" backend sh -c "python -m pytest core/content_pipeline/tests/test_tasks.py -k product_photo -v"
```

Expected: PASS ambos tests.

- [ ] **Step 5: Correr la suite completa**

```bash
docker compose run --rm --entrypoint "" backend sh -c "mkdir -p /tmp/prometheus-multiproc /app/logs && python -m pytest -q"
```

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/tasks.py core/content_pipeline/tests/test_tasks.py
GIT_EDITOR=true git commit -m "feat(tasks): rutea MODE_SAMPLE_IMAGE con foto real a generate_from_product_photo

Cuando hay foto de producto subida y el formato es single, usa la foto real
en vez del camino de generacion desde texto. Reel/carrusel siguen el camino
de hoy -- fuera de alcance de este modulo."
```

---

### Task 6: ImageGenerator.regenerate_with_reference

**Files:**
- Modify: `core/content_pipeline/generators/image_generator.py`
- Modify: `core/content_pipeline/tests/test_image_generator.py`

**Interfaces:**
- Consumes: `_validate_product_photo_generation` (Task 4), `_gemini_api_client()`/`_vertex_client()`/`_upload_to_storage()`.
- Produces: `ImageGenerator.regenerate_with_reference(current_image_bytes: bytes, feedback: str, vision_context: str, filename: str, max_qc_retries: int = 2) -> str`. Task 7 lo consume.

- [ ] **Step 1: Escribir el test que falla**

```python
class TestRegenerateWithReference:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_sends_current_image_not_original_photo(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        mock_part = MagicMock()
        mock_part.inline_data.data = b'fake-regenerated-png'
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = MagicMock(
            candidates=[MagicMock(content=MagicMock(parts=[mock_part]))]
        )
        with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_client), \
             patch.object(gen, '_validate_product_photo_generation', return_value=True), \
             patch.object(gen, '_upload_to_storage', return_value='https://storage.test/regen.png'):
            url = gen.regenerate_with_reference(
                current_image_bytes=b'current-image-bytes',
                feedback='hazlo mas colorido',
                vision_context='Aretes de plata con turquesa',
                filename='test-product-regen',
            )

        assert url == 'https://storage.test/regen.png'
        call_kwargs = mock_client.models.generate_content.call_args.kwargs
        prompt_text = ' '.join(str(c) for c in call_kwargs['contents'] if isinstance(c, str))
        assert 'hazlo mas colorido' in prompt_text
        assert 'Aretes de plata con turquesa' in prompt_text

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_LOCATION='global',
        GOOGLE_CLOUD_LOCATION_TEXT='global',
        VERTEX_IMAGE_MODEL_LITE='gemini-3.1-flash-lite-image',
    )
    def test_returns_empty_string_on_error(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client', side_effect=Exception('boom')):
            url = gen.regenerate_with_reference(
                current_image_bytes=b'current-image-bytes', feedback='mas colorido',
                vision_context='', filename='test-product-regen',
            )
        assert url == ''
```

- [ ] **Step 2: Correr, confirmar que falla**

```bash
docker compose run --rm --entrypoint "" backend sh -c "python -m pytest core/content_pipeline/tests/test_image_generator.py -k RegenerateWithReference -v"
```

Expected: FAIL — `regenerate_with_reference` no existe todavía.

- [ ] **Step 3: Implementar `regenerate_with_reference`**

Agregar el método junto a `generate_from_product_photo` (Task 4):

```python
    def regenerate_with_reference(self, current_image_bytes: bytes, feedback: str,
                                    vision_context: str, filename: str, max_qc_retries: int = 2) -> str:
        """Regeneracion: nano banana ve la imagen ACTUAL (lo que el usuario
        esta viendo, no la foto original) + el feedback del usuario + el
        analisis de vision guardado (para no perder fidelidad al producto
        real en regeneraciones sucesivas). Distinto de generate_from_product_photo
        -- no manda la foto cruda, manda el resultado anterior."""
        try:
            context_line = f" Recuerda el producto real: {vision_context}." if vision_context else ''
            prompt = (
                f"This is the current image the user is looking at. Edit it based on this "
                f"feedback: {feedback}.{context_line} Keep the real product recognizable and "
                f"consistent with the context above. Do not add new text, headline, or CTA. "
                f"DSLR camera quality, photorealistic, square 1:1 format."
            )
            image_part = types.Part.from_bytes(data=current_image_bytes, mime_type='image/png')
            last_bytes = None
            total_attempts = max_qc_retries + 1
            for attempt in range(total_attempts):
                last_bytes = self._generate_from_photo_with_retry(prompt, image_part)
                if self._validate_product_photo_generation(last_bytes):
                    return self._upload_to_storage(last_bytes, filename)
                if attempt < max_qc_retries:
                    logger.warning(f"Regen QC failed (attempt {attempt + 1}/{total_attempts}), reintentando...")
            logger.warning("Regen QC: reintentos agotados, usando ultima imagen generada")
            return self._upload_to_storage(last_bytes, filename)
        except Exception as e:
            logger.error(f"ImageGenerator.regenerate_with_reference error: {e}")
            return ''
```

Reusa `_generate_from_photo_with_retry`/`_generate_from_photo` de Task 4 tal cual (el nombre genérico de parámetro `photo_part` ya encaja con pasar la imagen actual en vez de la foto original — no hace falta duplicar el método).

- [ ] **Step 4: Correr, confirmar que pasa**

```bash
docker compose run --rm --entrypoint "" backend sh -c "python -m pytest core/content_pipeline/tests/test_image_generator.py -k RegenerateWithReference -v"
```

Expected: PASS ambos tests.

- [ ] **Step 5: Correr la suite completa**

```bash
docker compose run --rm --entrypoint "" backend sh -c "mkdir -p /tmp/prometheus-multiproc /app/logs && python -m pytest -q"
```

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/generators/image_generator.py core/content_pipeline/tests/test_image_generator.py
GIT_EDITOR=true git commit -m "feat(image_generator): regenera con imagen actual + contexto de vision

regenerate_with_reference: manda la imagen que el usuario esta viendo (no
la foto original) + el feedback + el analisis de vision guardado como
ancla de fidelidad al producto real. Reusa el mismo QC y camino de
retry/modelo que generate_from_product_photo."
```

---

### Task 7: Ruteo en post_action_api (acción regenerate)

**Files:**
- Modify: `core/shared/gcs_uploads.py` (función nueva de descarga por URL pública)
- Modify: `core/shared/tests/test_gcs_uploads.py`
- Modify: `core/brand_dna/views.py` (`post_action_api`, acción `'regenerate'`)
- Modify: `core/brand_dna/tests/test_views.py`

**Interfaces:**
- Consumes: `ImageGenerator.regenerate_with_reference` (Task 6).
- Produces: `read_upload_from_public_url(url: str) -> bytes` en `core/shared/gcs_uploads.py`.

**Confirmado leyendo el código real**: `gcs_uploads.py` hoy solo tiene `read_upload(gcs_path)` (por path relativo, ej. `posts/filename.png`) — no existe forma de descargar a partir de la URL pública que se guarda en `post.image_url`. Esa URL se construye en `image_generator.py:839` como `f'{blob.public_url}?v={timestamp}'`, o sea `https://storage.googleapis.com/{bucket}/{blob_name}?v=...`. Hace falta una función nueva que parsee el `blob_name` de esa URL y reuse el mismo patrón de `read_upload`.

- [ ] **Step 1: Escribir el test que falla para `read_upload_from_public_url`**

En `core/shared/tests/test_gcs_uploads.py` (crear si no existe — confirmar primero con `ls core/shared/tests/test_gcs_uploads.py`):

```python
from unittest.mock import patch, MagicMock
from django.test import override_settings


@override_settings(GOOGLE_CLOUD_PROJECT='agente-cosmic', GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket')
def test_read_upload_from_public_url_parses_blob_path_and_strips_query():
    from core.shared.gcs_uploads import read_upload_from_public_url
    mock_blob = MagicMock()
    mock_blob.download_as_bytes.return_value = b'fake-bytes'
    mock_bucket = MagicMock()
    mock_bucket.blob.return_value = mock_blob
    with patch('core.shared.gcs_uploads._client') as mock_client:
        mock_client.return_value.bucket.return_value = mock_bucket
        result = read_upload_from_public_url(
            'https://storage.googleapis.com/test-bucket/posts/job123-day1.png?v=1234567890'
        )
    mock_bucket.blob.assert_called_once_with('posts/job123-day1.png')
    assert result == b'fake-bytes'
```

- [ ] **Step 2: Correr, confirmar que falla**

```bash
docker compose run --rm --entrypoint "" backend sh -c "python -m pytest core/shared/tests/test_gcs_uploads.py -v"
```

Expected: FAIL — `read_upload_from_public_url` no existe todavía (o `ImportError` si el archivo de test no existía).

- [ ] **Step 3: Implementar `read_upload_from_public_url`**

En `core/shared/gcs_uploads.py`, agregar después de `read_upload`:

```python
def read_upload_from_public_url(url: str) -> bytes:
    """Descarga un blob a partir de su URL publica de GCS (la que guarda
    ContentPost.image_url, con cache-busting ?v=... incluido) -- a
    diferencia de read_upload, que espera un path relativo."""
    path = url.split(f'{settings.GOOGLE_CLOUD_STORAGE_BUCKET}/', 1)[1].split('?', 1)[0]
    return read_upload(path)
```

- [ ] **Step 4: Correr, confirmar que pasa**

```bash
docker compose run --rm --entrypoint "" backend sh -c "python -m pytest core/shared/tests/test_gcs_uploads.py -v"
```

Expected: PASS.

- [ ] **Step 5: Commit de esta pieza antes de seguir con el ruteo**

```bash
git add core/shared/gcs_uploads.py core/shared/tests/test_gcs_uploads.py
GIT_EDITOR=true git commit -m "feat(gcs_uploads): agrega descarga por URL publica

read_upload_from_public_url -- necesario para regenerate_with_reference,
que necesita los bytes de la imagen actual (post.image_url es una URL
publica con cache-busting, no un path relativo como el resto del modulo)."
```

- [ ] **Step 6: Escribir el test que falla para el ruteo en la vista**

En `core/brand_dna/tests/test_views.py`, el fixture `job_with_calendar` (línea 448, ya usado por los tests de regeneración existentes) crea un `job` con `BrandDNA` + `ContentCalendar` de 7 posts (formato default, sin foto). Agregar una variante con foto, mismo patrón exacto:

```python
@pytest.fixture
def job_with_calendar_and_product_photo(user):
    job = AnalysisJob.objects.create(
        email=user.email, business_url='https://tuwebmx.com', user=user,
        status=AnalysisJob.STATUS_DONE, stage=AnalysisJob.STAGE_COMPLETE, progress=100,
        product_reference_image_path='uploads/product_ref_test.jpg',
    )
    dna = BrandDNA.objects.create(
        job=job, business_name='Joyeria Luna', business_url='https://tuwebmx.com',
        description='Joyeria artesanal', keywords=['joyeria'], audience='Mujeres 25-45',
        tone='elegante', primary_colors=['#e94560'],
        product_photo_analysis='Aretes de plata con turquesa, estilo boho',
        product_category='joyeria',
    )
    calendar = ContentCalendar.objects.create(brand_dna=dna)
    for i in range(1, 8):
        ContentPost.objects.create(
            calendar=calendar, day_number=i, caption=f'Post {i}',
            image_url='https://example.com/img.jpg', suggested_time='19:00',
            hashtags=[], scheduled_at=timezone.now() + timedelta(days=i),
        )
    return job
```

```python
def test_regenerate_action_uses_reference_image_when_job_has_product_photo(client, user, job_with_calendar_and_product_photo):
    post = job_with_calendar_and_product_photo.brand_dna.calendar.posts.filter(format='single').first()
    with patch('core.brand_dna.views._regenerate_caption', return_value='Nuevo caption'), \
         patch('core.brand_dna.views.read_upload_from_public_url', return_value=b'current-image-bytes') as mock_download, \
         patch('core.brand_dna.views.ImageGenerator') as MockImage:
        MockImage.return_value.regenerate_with_reference.return_value = 'https://storage.test/regen.png'
        response = client.post(f'/post/{post.id}/action/', data=json.dumps({
            'action': 'regenerate', 'value': 'hazlo mas colorido',
        }), content_type='application/json')

    assert response.status_code == 200
    MockImage.return_value.regenerate_with_reference.assert_called_once()
    call_kwargs = MockImage.return_value.regenerate_with_reference.call_args.kwargs
    assert call_kwargs['current_image_bytes'] == b'current-image-bytes'
    assert call_kwargs['feedback'] == 'hazlo mas colorido'
    post.refresh_from_db()
    assert post.image_url == 'https://storage.test/regen.png'


def test_regenerate_action_uses_normal_path_without_product_photo(client, user, job_with_calendar):
    post = job_with_calendar.brand_dna.calendar.posts.filter(format='single').first()
    with patch('core.brand_dna.views._regenerate_caption', return_value='Nuevo caption'), \
         patch('core.brand_dna.views.ImageGenerator') as MockImage, \
         patch('core.brand_dna.views._generate_post_media', return_value=('https://storage.test/normal.png', [], '')):
        response = client.post(f'/post/{post.id}/action/', data=json.dumps({
            'action': 'regenerate', 'value': 'hazlo mas colorido',
        }), content_type='application/json')

    assert response.status_code == 200
    MockImage.return_value.regenerate_with_reference.assert_not_called()
```

- [ ] **Step 7: Correr, confirmar que falla**

```bash
docker compose run --rm --entrypoint "" backend sh -c "python -m pytest core/brand_dna/tests/test_views.py -k regenerate_action_uses_reference -v"
```

Expected: FAIL — `regenerate_with_reference` nunca se llama todavía.

- [ ] **Step 8: Rutear en `post_action_api`**

En `core/brand_dna/views.py`, agregar el import junto a los demás imports de `core.shared.gcs_uploads` del archivo (revisar el bloque de imports existente al inicio del archivo — ya importa `save_upload`, agregar `read_upload_from_public_url` a la misma línea o una nueva):

```python
from core.shared.gcs_uploads import save_upload, read_upload_from_public_url
```

Dentro del bloque `if action == 'regenerate':` (línea 527), reemplazar el bloque de regeneración de imagen (líneas 545-577) por una rama condicional:

```python
        # Regenerar imagen (o slides del carrusel, H20 + roadmap #5) con el nuevo caption
        new_image_url = post.image_url
        try:
            from core.content_pipeline.generators.image_generator import ImageGenerator
            brand_dna = post.calendar.brand_dna
            job_id = str(brand_dna.job.id)
            image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)

            if brand_dna.job.product_reference_image_path and post.image_url:
                current_image_bytes = read_upload_from_public_url(post.image_url)
                generated_url = image_gen.regenerate_with_reference(
                    current_image_bytes=current_image_bytes,
                    feedback=value,
                    vision_context=brand_dna.product_photo_analysis,
                    filename=f"{job_id}-day{post.day_number}-regen-{int(_time.time())}",
                )
                generated_urls = []
            else:
                from core.content_pipeline.tasks import _generate_post_media
                generated_url, generated_urls, _ = _generate_post_media(
                    image_gen,
                    None,  # reel_script_gen
                    None,  # reel_gen
                    fmt=post.format,
                    filename=f"{job_id}-day{post.day_number}-regen-{int(_time.time())}",
                    caption=new_caption,
                    colors=brand_dna.primary_colors,
                    tone=brand_dna.tone,
                    brand_name=brand_dna.business_name,
                    keywords=brand_dna.keywords,
                    description=brand_dna.description,
                    audience=brand_dna.audience,
                    max_qc_retries=0,  # regen es síncrono — sin reintentos QC para evitar timeout
                )

            if generated_url:
                new_image_url = generated_url
                post.image_url = new_image_url
                post.image_urls = generated_urls
                post.save(update_fields=['caption', 'user_note', 'user_status', 'image_url', 'image_urls', 'regen_count'])
            else:
                post.save(update_fields=['caption', 'user_note', 'user_status', 'regen_count'])
        except Exception as img_err:
            logger.error(f"Image regeneration error for post {post_id}: {img_err}")
            post.save(update_fields=['caption', 'user_note', 'user_status', 'regen_count'])
        POST_ACTIONS.labels(action='regenerated').inc()
```

- [ ] **Step 9: Correr, confirmar que pasa**

```bash
docker compose run --rm --entrypoint "" backend sh -c "python -m pytest core/brand_dna/tests/test_views.py -k regenerate_action -v"
```

Expected: PASS ambos tests nuevos, y los tests de regeneración ya existentes (`test_regenerate_action_uses_carousel_when_post_format_is_carousel`, `test_regenerate_action_blocked_for_reel_posts`) siguen pasando sin cambios.

- [ ] **Step 10: Correr la suite completa**

```bash
docker compose run --rm --entrypoint "" backend sh -c "mkdir -p /tmp/prometheus-multiproc /app/logs && python -m pytest -q"
```

- [ ] **Step 11: Commit**

```bash
git add core/brand_dna/views.py core/brand_dna/tests/test_views.py
GIT_EDITOR=true git commit -m "feat(views): regeneracion usa la imagen actual + contexto cuando hay foto real

post_action_api (regenerate): si el job tiene foto de producto real, usa
regenerate_with_reference (imagen actual + feedback + analisis guardado)
en vez del camino de generacion desde texto. Sin foto, comportamiento
identico a hoy."
```

---

## Verificación manual final (Anuar)

Después de completar las 7 tareas: subir una foto real de producto vía el formulario de análisis con `generation_mode=sample_image`, confirmar que la imagen generada usa la foto real (no genérica), probar "regenerar" con feedback y confirmar que el resultado es coherente con el producto real, y revisar en Django Admin que `BrandDNA.product_photo_analysis`/`product_category` se llenaron correctamente.
