# Agente Cosmic — Plan de Implementación (Hackathon 2026-06-05)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sistema agéntico que extrae el ADN de marca de un negocio (web + logo + posts anteriores) y genera un calendario de 7 días de contenido (captions + imágenes) entregado por email diariamente.

**Architecture:** Dos apps Django nuevas (`core/brand_dna` y `core/content_pipeline`) coordinadas por RQ. La landing en `/` recibe formulario multipart → crea `AnalysisJob` → encola `analyze_brand_task`. El frontend hace polling a `/api/brand-dna/status/<id>/` cada 3s. Al terminar el análisis se encola `content_generation_task` que genera contenido, envía email #1 y programa 6 RQ jobs para emails diarios 2-7.

**Tech Stack:** Django 5.2, django-rq 3.0.1, rq 2.4.0, google-cloud-vision, google-cloud-storage, google-genai (Vertex AI mode), Pollinations.ai, django-anymail/Mailgun, BeautifulSoup4, pytest + unittest.mock.

---

## Mapa de archivos

```
core/
  brand_dna/
    __init__.py
    apps.py
    models.py                    AnalysisJob, BrandDNA
    extractors/
      __init__.py
      web_scraper.py             WebScraper.extract(url) → dict
      logo_analyzer.py           LogoAnalyzer.analyze(image_bytes, mime_type) → dict
      posts_analyzer.py          PostsAnalyzer.analyze(images, text, profile_url) → dict
    tasks.py                     analyze_brand_task(job_id) — RQ job principal
    views.py                     landing, analyze_submit, results, status_api
    urls.py
    templates/brand_dna/
      landing.html
      results.html
    tests/
      __init__.py
      test_models.py
      test_web_scraper.py
      test_logo_analyzer.py
      test_posts_analyzer.py
      test_tasks.py
      test_views.py
  content_pipeline/
    __init__.py
    apps.py
    models.py                    ContentCalendar, ContentPost
    generators/
      __init__.py
      text_generator.py          TextGenerator.generate(brand_dna) → list[dict]
      image_generator.py         ImageGenerator.generate(prompt, colors) → str (URL)
    email_sender.py              EmailSender.send_initial / send_daily
    scheduler.py                 schedule_daily_emails(calendar)
    tasks.py                     content_generation_task(job_id), send_daily_email_task(post_id)
    templates/content_pipeline/
      email_initial.html
      email_daily.html
    tests/
      __init__.py
      test_models.py
      test_text_generator.py
      test_image_generator.py
      test_email_sender.py
      test_scheduler.py
      test_tasks.py

saas_chatbot/
  settings.py                    (modificar: INSTALLED_APPS, vars Google Cloud)
  urls.py                        (modificar: incluir brand_dna.urls)
requirements.txt                 (modificar: agregar google-cloud-vision, google-cloud-storage)
```

---

## Tarea 0: Google Cloud + paquetes nuevos

**Archivos:**
- Modificar: `requirements.txt`
- Modificar: `saas_chatbot/settings.py`

> ⚠️ PREREQUISITO CRÍTICO: Antes de ejecutar cualquier otra tarea, el usuario debe configurar Google Cloud credentials (Agent Platform login). Compartirá las URLs de documentación relevantes. Los pasos de autenticación van dentro del contenedor Docker.

- [ ] **Paso 1: Agregar paquetes a requirements.txt**

Agregar estas líneas al final de `requirements.txt`:
```
google-cloud-vision>=3.7.0
google-cloud-storage>=2.18.0
```

- [ ] **Paso 2: Reconstruir el contenedor con los nuevos paquetes**

```bash
docker compose build backend worker
```
Esperado: build exitoso, sin errores de pip.

- [ ] **Paso 3: Agregar variables de entorno a settings.py**

En `saas_chatbot/settings.py`, después de las variables de Mailgun, agregar:
```python
# Google Cloud (para Cloud Vision + Cloud Storage + Vertex AI)
GOOGLE_CLOUD_PROJECT = get_env('GOOGLE_CLOUD_PROJECT', default='')
GOOGLE_CLOUD_STORAGE_BUCKET = get_env('GOOGLE_CLOUD_STORAGE_BUCKET', default='agente-cosmic-assets')
GOOGLE_CLOUD_LOCATION = get_env('GOOGLE_CLOUD_LOCATION', default='us-central1')
# GOOGLE_APPLICATION_CREDENTIALS se inyecta como variable de entorno del contenedor
```

- [ ] **Paso 4: Agregar vars al .env (no commitear el .env)**

```bash
GOOGLE_CLOUD_PROJECT=tu-project-id
GOOGLE_CLOUD_STORAGE_BUCKET=agente-cosmic-assets
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=/app/google-credentials.json
```

Y copiar el archivo `google-credentials.json` de la service account al directorio raíz del proyecto (está en `.gitignore`).

- [ ] **Paso 5: Verificar autenticación dentro del contenedor**

```bash
docker exec agente-cosmic-backend-1 python -c "
from google.cloud import vision
client = vision.ImageAnnotatorClient()
print('Cloud Vision OK')
"
```
Esperado: `Cloud Vision OK` sin excepciones.

- [ ] **Paso 6: Commit**

```bash
git add requirements.txt saas_chatbot/settings.py
GIT_EDITOR=true git commit -m "feat: add google-cloud-vision, cloud-storage packages and settings"
```

---

## Tarea 1: App brand_dna — setup y modelos

**Archivos:**
- Crear: `core/brand_dna/__init__.py`
- Crear: `core/brand_dna/apps.py`
- Crear: `core/brand_dna/models.py`
- Crear: `core/brand_dna/extractors/__init__.py`
- Crear: `core/brand_dna/tests/__init__.py`
- Crear: `core/brand_dna/tests/test_models.py`
- Modificar: `saas_chatbot/settings.py` (INSTALLED_APPS)
- Modificar: `saas_chatbot/urls.py`

- [ ] **Paso 1: Escribir el test de modelos (falla porque la app no existe)**

Crear `core/brand_dna/tests/test_models.py`:
```python
import pytest
from core.brand_dna.models import AnalysisJob, BrandDNA

pytestmark = pytest.mark.django_db


def test_analysis_job_creation():
    job = AnalysisJob.objects.create(
        email='test@example.com',
        business_url='https://tuwebmx.com',
    )
    assert job.status == 'pending'
    assert job.progress == 0
    assert job.stage == 'web'
    assert str(job.id) != ''


def test_brand_dna_creation(analysis_job):
    dna = BrandDNA.objects.create(
        job=analysis_job,
        business_name='Tu Web MX',
        business_url='https://tuwebmx.com',
        description='Agencia de diseño web',
        keywords=['diseño', 'web', 'digital'],
        audience='Empresas medianas en México',
        tone='profesional',
        primary_colors=['#1A1A2E', '#E94560'],
        logo_elements='Tipografía moderna, colores contrastantes',
        posting_style='Posts cortos con call to action',
        avg_caption_length=120,
        common_hashtags=['#diseñoweb', '#agenciadigital'],
    )
    assert dna.business_name == 'Tu Web MX'
    assert '#1A1A2E' in dna.primary_colors


def test_analysis_job_progress_update(analysis_job):
    analysis_job.progress = 50
    analysis_job.stage = 'logo'
    analysis_job.save()
    refreshed = AnalysisJob.objects.get(id=analysis_job.id)
    assert refreshed.progress == 50
    assert refreshed.stage == 'logo'


@pytest.fixture
def analysis_job():
    return AnalysisJob.objects.create(
        email='test@example.com',
        business_url='https://tuwebmx.com',
    )
```

- [ ] **Paso 2: Ejecutar test para verificar que falla**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/brand_dna/tests/test_models.py -v 2>&1 | tail -5
```
Esperado: ERROR — módulo no encontrado.

- [ ] **Paso 3: Crear estructura de carpetas y archivos base**

```bash
mkdir -p core/brand_dna/extractors
mkdir -p core/brand_dna/templates/brand_dna
mkdir -p core/brand_dna/tests
touch core/brand_dna/__init__.py
touch core/brand_dna/extractors/__init__.py
touch core/brand_dna/tests/__init__.py
```

- [ ] **Paso 4: Crear apps.py**

Crear `core/brand_dna/apps.py`:
```python
from django.apps import AppConfig


class BrandDnaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core.brand_dna'
    verbose_name = 'Brand DNA'
```

- [ ] **Paso 5: Crear models.py**

Crear `core/brand_dna/models.py`:
```python
import uuid
from django.db import models


class AnalysisJob(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_PROCESSING = 'processing'
    STATUS_DONE = 'done'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pendiente'),
        (STATUS_PROCESSING, 'Procesando'),
        (STATUS_DONE, 'Completado'),
        (STATUS_FAILED, 'Fallido'),
    ]
    STAGE_WEB = 'web'
    STAGE_LOGO = 'logo'
    STAGE_POSTS = 'posts'
    STAGE_CONTENT = 'content'
    STAGE_COMPLETE = 'complete'
    STAGE_CHOICES = [
        (STAGE_WEB, 'Analizando sitio web'),
        (STAGE_LOGO, 'Analizando logo'),
        (STAGE_POSTS, 'Analizando posts'),
        (STAGE_CONTENT, 'Generando contenido'),
        (STAGE_COMPLETE, 'Completo'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField()
    business_url = models.URLField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default=STAGE_WEB)
    progress = models.IntegerField(default=0)
    error_message = models.TextField(blank=True, default='')
    # Archivos subidos (rutas relativas a MEDIA_ROOT)
    logo_file_path = models.CharField(max_length=500, blank=True, default='')
    post_images_paths = models.JSONField(default=list, blank=True)
    posts_text = models.TextField(blank=True, default='')
    profile_url = models.URLField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'brand_dna_analysis_job'
        ordering = ['-created_at']

    def __str__(self):
        return f"Job {self.id} — {self.business_url} ({self.status})"

    def update_progress(self, stage: str, progress: int) -> None:
        self.stage = stage
        self.progress = progress
        self.save(update_fields=['stage', 'progress'])

    def mark_failed(self, error: str) -> None:
        self.status = self.STATUS_FAILED
        self.error_message = error
        self.save(update_fields=['status', 'error_message'])


class BrandDNA(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.OneToOneField(AnalysisJob, on_delete=models.CASCADE, related_name='brand_dna')
    business_name = models.CharField(max_length=255)
    business_url = models.URLField()
    # Web scraping
    description = models.TextField()
    keywords = models.JSONField(default=list)
    audience = models.TextField()
    tone = models.CharField(max_length=50)
    # Logo
    primary_colors = models.JSONField(default=list)
    logo_url = models.URLField(blank=True, default='')
    logo_elements = models.TextField(blank=True, default='')
    # Posts anteriores
    posting_style = models.TextField(blank=True, default='')
    avg_caption_length = models.IntegerField(default=150)
    common_hashtags = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'brand_dna_brand_dna'

    def __str__(self):
        return f"BrandDNA — {self.business_name}"
```

- [ ] **Paso 6: Registrar app en INSTALLED_APPS**

En `saas_chatbot/settings.py`, dentro de `INSTALLED_APPS`, agregar después de `'core.agent.apps.AgentConfig'`:
```python
    'core.brand_dna.apps.BrandDnaConfig',
```

- [ ] **Paso 7: Crear y aplicar migración**

```bash
docker exec agente-cosmic-backend-1 python manage.py makemigrations brand_dna
docker exec agente-cosmic-backend-1 python manage.py migrate
```
Esperado: migración creada y aplicada sin errores.

- [ ] **Paso 8: Ejecutar tests y verificar que pasan**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/brand_dna/tests/test_models.py -v
```
Esperado: 3 tests PASSED.

- [ ] **Paso 9: Commit**

```bash
git add core/brand_dna/ saas_chatbot/settings.py
GIT_EDITOR=true git commit -m "feat: add brand_dna app with AnalysisJob and BrandDNA models"
```

---

## Tarea 2: App content_pipeline — setup y modelos

**Archivos:**
- Crear: `core/content_pipeline/__init__.py`
- Crear: `core/content_pipeline/apps.py`
- Crear: `core/content_pipeline/models.py`
- Crear: `core/content_pipeline/generators/__init__.py`
- Crear: `core/content_pipeline/tests/__init__.py`
- Crear: `core/content_pipeline/tests/test_models.py`
- Modificar: `saas_chatbot/settings.py` (INSTALLED_APPS)

- [ ] **Paso 1: Escribir test de modelos**

Crear `core/content_pipeline/tests/test_models.py`:
```python
import pytest
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost

pytestmark = pytest.mark.django_db


@pytest.fixture
def brand_dna():
    job = AnalysisJob.objects.create(email='test@example.com', business_url='https://tuwebmx.com')
    return BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseño'], audience='PYMEs',
        tone='profesional', primary_colors=['#1A1A2E'],
    )


def test_content_calendar_creation(brand_dna):
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    assert calendar.brand_dna == brand_dna
    assert calendar.id is not None


def test_content_post_creation(brand_dna):
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    scheduled = timezone.now() + timedelta(days=1)
    post = ContentPost.objects.create(
        calendar=calendar,
        day_number=1,
        caption='Post de prueba para redes sociales.',
        image_url='https://storage.googleapis.com/agente-cosmic/img1.jpg',
        suggested_time='19:00',
        hashtags=['#diseñoweb', '#mexico'],
        scheduled_at=scheduled,
    )
    assert post.status == 'pending'
    assert post.day_number == 1
    assert post.sent_at is None


def test_calendar_has_7_posts(brand_dna):
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    for i in range(1, 8):
        ContentPost.objects.create(
            calendar=calendar, day_number=i,
            caption=f'Post día {i}', image_url='https://example.com/img.jpg',
            suggested_time='19:00', hashtags=[],
            scheduled_at=timezone.now() + timedelta(days=i),
        )
    assert calendar.posts.count() == 7
```

- [ ] **Paso 2: Ejecutar para verificar que falla**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/content_pipeline/tests/test_models.py -v 2>&1 | tail -5
```
Esperado: ERROR — módulo no encontrado.

- [ ] **Paso 3: Crear estructura de carpetas**

```bash
mkdir -p core/content_pipeline/generators
mkdir -p core/content_pipeline/templates/content_pipeline
mkdir -p core/content_pipeline/tests
touch core/content_pipeline/__init__.py
touch core/content_pipeline/generators/__init__.py
touch core/content_pipeline/tests/__init__.py
```

- [ ] **Paso 4: Crear apps.py**

Crear `core/content_pipeline/apps.py`:
```python
from django.apps import AppConfig


class ContentPipelineConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core.content_pipeline'
    verbose_name = 'Content Pipeline'
```

- [ ] **Paso 5: Crear models.py**

Crear `core/content_pipeline/models.py`:
```python
import uuid
from django.db import models
from core.brand_dna.models import BrandDNA


class ContentCalendar(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brand_dna = models.OneToOneField(BrandDNA, on_delete=models.CASCADE, related_name='calendar')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'content_pipeline_calendar'

    def __str__(self):
        return f"Calendar — {self.brand_dna.business_name}"


class ContentPost(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pendiente'),
        (STATUS_SENT, 'Enviado'),
        (STATUS_FAILED, 'Fallido'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    calendar = models.ForeignKey(ContentCalendar, on_delete=models.CASCADE, related_name='posts')
    day_number = models.IntegerField()
    caption = models.TextField()
    image_url = models.URLField(max_length=1000)
    suggested_time = models.TimeField()
    hashtags = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    scheduled_at = models.DateTimeField()
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'content_pipeline_post'
        ordering = ['day_number']

    def __str__(self):
        return f"Día {self.day_number} — {self.calendar.brand_dna.business_name}"
```

- [ ] **Paso 6: Registrar en INSTALLED_APPS**

En `saas_chatbot/settings.py`, agregar después de `'core.brand_dna.apps.BrandDnaConfig'`:
```python
    'core.content_pipeline.apps.ContentPipelineConfig',
```

- [ ] **Paso 7: Crear y aplicar migración**

```bash
docker exec agente-cosmic-backend-1 python manage.py makemigrations content_pipeline
docker exec agente-cosmic-backend-1 python manage.py migrate
```

- [ ] **Paso 8: Ejecutar tests**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/content_pipeline/tests/test_models.py -v
```
Esperado: 3 tests PASSED.

- [ ] **Paso 9: Commit**

```bash
git add core/content_pipeline/ saas_chatbot/settings.py
GIT_EDITOR=true git commit -m "feat: add content_pipeline app with ContentCalendar and ContentPost models"
```

---

## Tarea 3: WebScraper

**Archivos:**
- Crear: `core/brand_dna/extractors/web_scraper.py`
- Crear: `core/brand_dna/tests/test_web_scraper.py`

- [ ] **Paso 1: Escribir tests**

Crear `core/brand_dna/tests/test_web_scraper.py`:
```python
import pytest
from unittest.mock import patch, MagicMock
from core.brand_dna.extractors.web_scraper import WebScraper

MOCK_HTML = """
<html>
<head><title>Tu Web MX — Diseño Web Profesional</title></head>
<body>
<h1>Diseño web que convierte</h1>
<p>Creamos sitios web modernos para empresas en México. Especialistas en e-commerce y landing pages.</p>
<meta name="description" content="Agencia de diseño web en México">
</body>
</html>
"""

MOCK_GEMINI_RESPONSE = '''{
  "business_name": "Tu Web MX",
  "description": "Agencia de diseño web profesional especializada en e-commerce",
  "keywords": ["diseño web", "e-commerce", "landing pages", "México"],
  "audience": "Empresas medianas y emprendedores en México que buscan presencia digital profesional",
  "tone": "profesional"
}'''


@pytest.fixture
def scraper():
    return WebScraper(gemini_api_key='test-key', gemini_model='gemini-2.5-flash')


def test_extract_returns_required_keys(scraper):
    with patch('requests.get') as mock_get, \
         patch('core.brand_dna.extractors.web_scraper.GeminiAdapter') as MockGemini:
        mock_get.return_value.text = MOCK_HTML
        mock_get.return_value.status_code = 200
        MockGemini.return_value.generate_response.return_value = MOCK_GEMINI_RESPONSE

        result = scraper.extract('https://tuwebmx.com')

    assert 'business_name' in result
    assert 'description' in result
    assert 'keywords' in result
    assert 'audience' in result
    assert 'tone' in result
    assert isinstance(result['keywords'], list)


def test_extract_parses_gemini_json(scraper):
    with patch('requests.get') as mock_get, \
         patch('core.brand_dna.extractors.web_scraper.GeminiAdapter') as MockGemini:
        mock_get.return_value.text = MOCK_HTML
        mock_get.return_value.status_code = 200
        MockGemini.return_value.generate_response.return_value = MOCK_GEMINI_RESPONSE

        result = scraper.extract('https://tuwebmx.com')

    assert result['business_name'] == 'Tu Web MX'
    assert result['tone'] == 'profesional'


def test_extract_handles_request_error(scraper):
    with patch('requests.get') as mock_get, \
         patch('core.brand_dna.extractors.web_scraper.GeminiAdapter') as MockGemini:
        mock_get.side_effect = Exception('Connection error')

        result = scraper.extract('https://sitio-invalido.com')

    assert result['business_name'] == 'Negocio'
    assert result['tone'] == 'profesional'
    assert isinstance(result['keywords'], list)
```

- [ ] **Paso 2: Ejecutar para verificar que falla**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/brand_dna/tests/test_web_scraper.py -v 2>&1 | tail -5
```
Esperado: ERROR — módulo no encontrado.

- [ ] **Paso 3: Implementar WebScraper**

Crear `core/brand_dna/extractors/web_scraper.py`:
```python
import json
import logging
import requests
from bs4 import BeautifulSoup
from core.agent.infrastructure.gemini_adapter import GeminiAdapter

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """
Analiza el siguiente HTML de un sitio web de negocio y extrae su información de marca.
Responde ÚNICAMENTE con un JSON válido, sin markdown, con esta estructura exacta:
{{
  "business_name": "nombre del negocio",
  "description": "qué hace el negocio en 1-2 oraciones",
  "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"],
  "audience": "descripción del cliente ideal en 1 oración",
  "tone": "uno de: formal, casual, inspiracional, urgente, profesional, amigable"
}}

HTML:
{html}
"""

_FALLBACK = {
    'business_name': 'Negocio',
    'description': 'Empresa con presencia digital.',
    'keywords': [],
    'audience': 'Clientes generales',
    'tone': 'profesional',
}


class WebScraper:
    def __init__(self, gemini_api_key: str, gemini_model: str = 'gemini-2.5-flash'):
        self._api_key = gemini_api_key
        self._model = gemini_model

    def extract(self, url: str) -> dict:
        try:
            html = self._fetch_html(url)
            return self._analyze_with_gemini(html)
        except Exception as e:
            logger.error(f"WebScraper error para {url}: {e}")
            return _FALLBACK.copy()

    def _fetch_html(self, url: str) -> str:
        response = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(response.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer']):
            tag.decompose()
        return soup.get_text(separator=' ', strip=True)[:4000]

    def _analyze_with_gemini(self, text: str) -> dict:
        adapter = GeminiAdapter()
        prompt = _PROMPT_TEMPLATE.format(html=text)
        raw = adapter.generate_response(prompt, api_key=self._api_key, model_name=self._model)
        raw = raw.strip().lstrip('```json').lstrip('```').rstrip('```').strip()
        return json.loads(raw)
```

- [ ] **Paso 4: Ejecutar tests**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/brand_dna/tests/test_web_scraper.py -v
```
Esperado: 3 tests PASSED.

- [ ] **Paso 5: Commit**

```bash
git add core/brand_dna/extractors/web_scraper.py core/brand_dna/tests/test_web_scraper.py
GIT_EDITOR=true git commit -m "feat: add WebScraper extractor using BeautifulSoup + Gemini"
```

---

## Tarea 4: LogoAnalyzer

**Archivos:**
- Crear: `core/brand_dna/extractors/logo_analyzer.py`
- Crear: `core/brand_dna/tests/test_logo_analyzer.py`

- [ ] **Paso 1: Escribir tests**

Crear `core/brand_dna/tests/test_logo_analyzer.py`:
```python
import pytest
from unittest.mock import patch, MagicMock
from core.brand_dna.extractors.logo_analyzer import LogoAnalyzer


@pytest.fixture
def analyzer():
    return LogoAnalyzer(gemini_api_key='test-key')


def _mock_vision_response():
    mock = MagicMock()
    color1 = MagicMock()
    color1.color.red = 26
    color1.color.green = 26
    color1.color.blue = 46
    color1.pixel_fraction = 0.6
    color2 = MagicMock()
    color2.color.red = 233
    color2.color.green = 69
    color2.color.blue = 96
    color2.pixel_fraction = 0.3
    mock.image_properties_annotation.dominant_colors.colors = [color1, color2]
    mock.label_annotations = []
    return mock


def test_analyze_returns_required_keys(analyzer):
    with patch('core.brand_dna.extractors.logo_analyzer.vision') as mock_vision, \
         patch('core.brand_dna.extractors.logo_analyzer.GeminiAdapter') as MockGemini:
        mock_vision.ImageAnnotatorClient.return_value.annotate_image.return_value = _mock_vision_response()
        MockGemini.return_value.generate_response.return_value = 'Tipografía sans-serif moderna, diseño minimalista'

        result = analyzer.analyze(b'fake-image-bytes', 'image/png')

    assert 'primary_colors' in result
    assert 'logo_elements' in result
    assert isinstance(result['primary_colors'], list)


def test_analyze_extracts_hex_colors(analyzer):
    with patch('core.brand_dna.extractors.logo_analyzer.vision') as mock_vision, \
         patch('core.brand_dna.extractors.logo_analyzer.GeminiAdapter') as MockGemini:
        mock_vision.ImageAnnotatorClient.return_value.annotate_image.return_value = _mock_vision_response()
        MockGemini.return_value.generate_response.return_value = 'Tipografía moderna'

        result = analyzer.analyze(b'fake-image-bytes', 'image/png')

    assert '#1a1a2e' in result['primary_colors']
    assert '#e94560' in result['primary_colors']


def test_analyze_handles_vision_error(analyzer):
    with patch('core.brand_dna.extractors.logo_analyzer.vision') as mock_vision, \
         patch('core.brand_dna.extractors.logo_analyzer.GeminiAdapter'):
        mock_vision.ImageAnnotatorClient.return_value.annotate_image.side_effect = Exception('API error')

        result = analyzer.analyze(b'fake-image-bytes', 'image/png')

    assert result['primary_colors'] == []
    assert result['logo_elements'] == ''
```

- [ ] **Paso 2: Ejecutar para verificar que falla**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/brand_dna/tests/test_logo_analyzer.py -v 2>&1 | tail -5
```

- [ ] **Paso 3: Implementar LogoAnalyzer**

Crear `core/brand_dna/extractors/logo_analyzer.py`:
```python
import base64
import logging
from google.cloud import vision
from core.agent.infrastructure.gemini_adapter import GeminiAdapter

logger = logging.getLogger(__name__)

_FALLBACK = {'primary_colors': [], 'logo_elements': ''}

_GEMINI_PROMPT = """
Analiza esta imagen de logo de marca. Describe en 1-2 oraciones:
- Estilo tipográfico (si lo hay)
- Estilo gráfico (minimalista, ilustrativo, geométrico, etc.)
- Sensación general de la marca
Responde solo con la descripción, sin listas ni formato.
"""


class LogoAnalyzer:
    def __init__(self, gemini_api_key: str, gemini_model: str = 'gemini-2.5-flash'):
        self._api_key = gemini_api_key
        self._model = gemini_model

    def analyze(self, image_bytes: bytes, mime_type: str) -> dict:
        try:
            colors = self._extract_colors(image_bytes, mime_type)
            elements = self._describe_with_gemini(image_bytes, mime_type)
            return {'primary_colors': colors, 'logo_elements': elements}
        except Exception as e:
            logger.error(f"LogoAnalyzer error: {e}")
            return _FALLBACK.copy()

    def _extract_colors(self, image_bytes: bytes, mime_type: str) -> list[str]:
        client = vision.ImageAnnotatorClient()
        image = vision.Image(content=image_bytes)
        features = [
            vision.Feature(type_=vision.Feature.Type.IMAGE_PROPERTIES),
        ]
        request = vision.AnnotateImageRequest(image=image, features=features)
        response = client.annotate_image(request=request)
        colors = response.image_properties_annotation.dominant_colors.colors
        hex_colors = []
        for c in sorted(colors, key=lambda x: x.pixel_fraction, reverse=True)[:5]:
            r, g, b = int(c.color.red), int(c.color.green), int(c.color.blue)
            hex_colors.append(f'#{r:02x}{g:02x}{b:02x}')
        return hex_colors

    def _describe_with_gemini(self, image_bytes: bytes, mime_type: str) -> str:
        from google.genai import types
        import google.genai as genai
        client = genai.Client(api_key=self._api_key)
        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        response = client.models.generate_content(
            model=self._model,
            contents=[_GEMINI_PROMPT, image_part],
        )
        return response.text.strip()
```

- [ ] **Paso 4: Ejecutar tests**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/brand_dna/tests/test_logo_analyzer.py -v
```
Esperado: 3 tests PASSED.

- [ ] **Paso 5: Commit**

```bash
git add core/brand_dna/extractors/logo_analyzer.py core/brand_dna/tests/test_logo_analyzer.py
GIT_EDITOR=true git commit -m "feat: add LogoAnalyzer using Cloud Vision + Gemini Vision"
```

---

## Tarea 5: PostsAnalyzer

**Archivos:**
- Crear: `core/brand_dna/extractors/posts_analyzer.py`
- Crear: `core/brand_dna/tests/test_posts_analyzer.py`

- [ ] **Paso 1: Escribir tests**

Crear `core/brand_dna/tests/test_posts_analyzer.py`:
```python
import pytest
from unittest.mock import patch, MagicMock
from core.brand_dna.extractors.posts_analyzer import PostsAnalyzer

MOCK_TEXT_POSTS = """
Post 1: ¡Nuevo proyecto terminado! 🎉 Diseñamos el sitio web de @ClienteMX. #diseñoweb #webdesign
Post 2: ¿Tu sitio web convierte visitas en clientes? Nosotros te ayudamos. Escríbenos hoy. #marketing
Post 3: Creatividad + estrategia = resultados. Así trabajamos en Tu Web MX. #agenciadigital
"""

MOCK_GEMINI_JSON = '''{
  "posting_style": "Posts cortos y directos con call to action claro, uso de emojis moderado",
  "avg_caption_length": 120,
  "common_hashtags": ["#diseñoweb", "#webdesign", "#marketing", "#agenciadigital"]
}'''


@pytest.fixture
def analyzer():
    return PostsAnalyzer(gemini_api_key='test-key')


def test_analyze_text_returns_required_keys(analyzer):
    with patch('core.brand_dna.extractors.posts_analyzer.GeminiAdapter') as MockGemini:
        MockGemini.return_value.generate_response.return_value = MOCK_GEMINI_JSON
        result = analyzer.analyze(text=MOCK_TEXT_POSTS)

    assert 'posting_style' in result
    assert 'avg_caption_length' in result
    assert 'common_hashtags' in result
    assert isinstance(result['common_hashtags'], list)


def test_analyze_text_parses_correctly(analyzer):
    with patch('core.brand_dna.extractors.posts_analyzer.GeminiAdapter') as MockGemini:
        MockGemini.return_value.generate_response.return_value = MOCK_GEMINI_JSON
        result = analyzer.analyze(text=MOCK_TEXT_POSTS)

    assert result['avg_caption_length'] == 120
    assert '#diseñoweb' in result['common_hashtags']


def test_analyze_with_no_input_returns_defaults(analyzer):
    result = analyzer.analyze()
    assert result['posting_style'] == ''
    assert result['avg_caption_length'] == 150
    assert result['common_hashtags'] == []


def test_analyze_handles_gemini_error(analyzer):
    with patch('core.brand_dna.extractors.posts_analyzer.GeminiAdapter') as MockGemini:
        MockGemini.return_value.generate_response.side_effect = Exception('API error')
        result = analyzer.analyze(text='Algún texto de posts')

    assert result['posting_style'] == ''
    assert result['avg_caption_length'] == 150
```

- [ ] **Paso 2: Ejecutar para verificar que falla**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/brand_dna/tests/test_posts_analyzer.py -v 2>&1 | tail -5
```

- [ ] **Paso 3: Implementar PostsAnalyzer**

Crear `core/brand_dna/extractors/posts_analyzer.py`:
```python
import base64
import json
import logging
import requests
from bs4 import BeautifulSoup
from core.agent.infrastructure.gemini_adapter import GeminiAdapter

logger = logging.getLogger(__name__)

_FALLBACK = {'posting_style': '', 'avg_caption_length': 150, 'common_hashtags': []}

_TEXT_PROMPT = """
Analiza los siguientes posts de redes sociales de una marca y extrae su estilo de comunicación.
Responde ÚNICAMENTE con JSON válido, sin markdown:
{{
  "posting_style": "descripción del estilo en 1-2 oraciones",
  "avg_caption_length": número_entero_aproximado,
  "common_hashtags": ["#tag1", "#tag2", "#tag3"]
}}

Posts:
{posts}
"""

_IMAGE_PROMPT = """
Analiza estas imágenes de posts de redes sociales de una marca.
Describe el estilo visual y de comunicación. Responde ÚNICAMENTE con JSON válido, sin markdown:
{{
  "posting_style": "descripción del estilo visual y textual en 1-2 oraciones",
  "avg_caption_length": 150,
  "common_hashtags": []
}}
"""


class PostsAnalyzer:
    def __init__(self, gemini_api_key: str, gemini_model: str = 'gemini-2.5-flash'):
        self._api_key = gemini_api_key
        self._model = gemini_model

    def analyze(
        self,
        images: list[bytes] | None = None,
        text: str | None = None,
        profile_url: str | None = None,
    ) -> dict:
        if not images and not text and not profile_url:
            return _FALLBACK.copy()
        try:
            if images:
                return self._analyze_images(images)
            if text:
                return self._analyze_text(text)
            if profile_url:
                scraped = self._scrape_profile(profile_url)
                if scraped:
                    return self._analyze_text(scraped)
            return _FALLBACK.copy()
        except Exception as e:
            logger.error(f"PostsAnalyzer error: {e}")
            return _FALLBACK.copy()

    def _analyze_text(self, text: str) -> dict:
        adapter = GeminiAdapter()
        prompt = _TEXT_PROMPT.format(posts=text[:3000])
        raw = adapter.generate_response(prompt, api_key=self._api_key, model_name=self._model)
        raw = raw.strip().lstrip('```json').lstrip('```').rstrip('```').strip()
        return json.loads(raw)

    def _analyze_images(self, images: list[bytes]) -> dict:
        import google.genai as genai
        from google.genai import types
        client = genai.Client(api_key=self._api_key)
        parts = [_IMAGE_PROMPT]
        for img_bytes in images[:5]:
            parts.append(types.Part.from_bytes(data=img_bytes, mime_type='image/jpeg'))
        response = client.models.generate_content(model=self._model, contents=parts)
        raw = response.text.strip().lstrip('```json').lstrip('```').rstrip('```').strip()
        return json.loads(raw)

    def _scrape_profile(self, url: str) -> str:
        try:
            resp = requests.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            soup = BeautifulSoup(resp.text, 'html.parser')
            texts = [p.get_text() for p in soup.find_all(['p', 'span', 'div']) if len(p.get_text()) > 20]
            return '\n'.join(texts[:20])
        except Exception:
            return ''
```

- [ ] **Paso 4: Ejecutar tests**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/brand_dna/tests/test_posts_analyzer.py -v
```
Esperado: 4 tests PASSED.

- [ ] **Paso 5: Commit**

```bash
git add core/brand_dna/extractors/posts_analyzer.py core/brand_dna/tests/test_posts_analyzer.py
GIT_EDITOR=true git commit -m "feat: add PostsAnalyzer for images, text and profile URL"
```

---

## Tarea 6: analyze_brand_task (RQ job principal)

**Archivos:**
- Crear: `core/brand_dna/tasks.py`
- Crear: `core/brand_dna/tests/test_tasks.py`

- [ ] **Paso 1: Escribir tests**

Crear `core/brand_dna/tests/test_tasks.py`:
```python
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings
from core.brand_dna.models import AnalysisJob, BrandDNA

pytestmark = pytest.mark.django_db

_WEB_RESULT = {
    'business_name': 'Tu Web MX', 'description': 'Agencia digital',
    'keywords': ['diseño'], 'audience': 'PYMEs', 'tone': 'profesional',
}
_LOGO_RESULT = {'primary_colors': ['#1a1a2e'], 'logo_elements': 'Tipografía moderna'}
_POSTS_RESULT = {'posting_style': 'Directo', 'avg_caption_length': 120, 'common_hashtags': []}


@pytest.fixture
def pending_job():
    return AnalysisJob.objects.create(email='test@example.com', business_url='https://tuwebmx.com')


@override_settings(GEMINI_API_KEY='test-key', AI_MODEL='gemini-2.5-flash', GOOGLE_CLOUD_PROJECT='proj')
def test_task_creates_brand_dna(pending_job):
    with patch('core.brand_dna.tasks.WebScraper') as MockScraper, \
         patch('core.brand_dna.tasks.LogoAnalyzer') as MockLogo, \
         patch('core.brand_dna.tasks.PostsAnalyzer') as MockPosts, \
         patch('core.brand_dna.tasks.django_rq') as mock_rq:
        MockScraper.return_value.extract.return_value = _WEB_RESULT
        MockLogo.return_value.analyze.return_value = _LOGO_RESULT
        MockPosts.return_value.analyze.return_value = _POSTS_RESULT

        from core.brand_dna.tasks import analyze_brand_task
        analyze_brand_task(str(pending_job.id))

    pending_job.refresh_from_db()
    assert pending_job.status == AnalysisJob.STATUS_PROCESSING
    assert BrandDNA.objects.filter(job=pending_job).exists()
    dna = BrandDNA.objects.get(job=pending_job)
    assert dna.business_name == 'Tu Web MX'
    assert dna.tone == 'profesional'


@override_settings(GEMINI_API_KEY='test-key', AI_MODEL='gemini-2.5-flash', GOOGLE_CLOUD_PROJECT='proj')
def test_task_enqueues_content_generation(pending_job):
    with patch('core.brand_dna.tasks.WebScraper') as MockScraper, \
         patch('core.brand_dna.tasks.LogoAnalyzer') as MockLogo, \
         patch('core.brand_dna.tasks.PostsAnalyzer') as MockPosts, \
         patch('core.brand_dna.tasks.django_rq') as mock_rq:
        MockScraper.return_value.extract.return_value = _WEB_RESULT
        MockLogo.return_value.analyze.return_value = _LOGO_RESULT
        MockPosts.return_value.analyze.return_value = _POSTS_RESULT

        from core.brand_dna.tasks import analyze_brand_task
        analyze_brand_task(str(pending_job.id))

    mock_rq.enqueue.assert_called_once()


@override_settings(GEMINI_API_KEY='test-key', AI_MODEL='gemini-2.5-flash', GOOGLE_CLOUD_PROJECT='proj')
def test_task_marks_failed_on_error(pending_job):
    with patch('core.brand_dna.tasks.WebScraper') as MockScraper, \
         patch('core.brand_dna.tasks.LogoAnalyzer'), \
         patch('core.brand_dna.tasks.PostsAnalyzer'), \
         patch('core.brand_dna.tasks.django_rq'):
        MockScraper.return_value.extract.side_effect = Exception('Fatal error')

        from core.brand_dna.tasks import analyze_brand_task
        analyze_brand_task(str(pending_job.id))

    pending_job.refresh_from_db()
    assert pending_job.status == AnalysisJob.STATUS_FAILED
    assert 'Fatal error' in pending_job.error_message
```

- [ ] **Paso 2: Ejecutar para verificar que falla**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/brand_dna/tests/test_tasks.py -v 2>&1 | tail -5
```

- [ ] **Paso 3: Implementar analyze_brand_task**

Crear `core/brand_dna/tasks.py`:
```python
import logging
import os
import django_rq
from django.conf import settings
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.brand_dna.extractors.web_scraper import WebScraper
from core.brand_dna.extractors.logo_analyzer import LogoAnalyzer
from core.brand_dna.extractors.posts_analyzer import PostsAnalyzer

logger = logging.getLogger(__name__)


def analyze_brand_task(job_id: str) -> None:
    job = AnalysisJob.objects.get(id=job_id)
    job.status = AnalysisJob.STATUS_PROCESSING
    job.save(update_fields=['status'])

    try:
        api_key = settings.GEMINI_API_KEY
        model = settings.AI_MODEL

        # Stage 1: Web scraping
        job.update_progress(AnalysisJob.STAGE_WEB, 10)
        scraper = WebScraper(gemini_api_key=api_key, gemini_model=model)
        web_data = scraper.extract(job.business_url)
        job.update_progress(AnalysisJob.STAGE_WEB, 30)

        # Stage 2: Logo analysis
        job.update_progress(AnalysisJob.STAGE_LOGO, 35)
        logo_data = {'primary_colors': [], 'logo_elements': ''}
        if job.logo_file_path:
            logo_path = os.path.join(settings.MEDIA_ROOT, job.logo_file_path)
            if os.path.exists(logo_path):
                with open(logo_path, 'rb') as f:
                    logo_bytes = f.read()
                mime = 'image/png' if logo_path.endswith('.png') else 'image/jpeg'
                analyzer = LogoAnalyzer(gemini_api_key=api_key, gemini_model=model)
                logo_data = analyzer.analyze(logo_bytes, mime)
        job.update_progress(AnalysisJob.STAGE_LOGO, 55)

        # Stage 3: Posts analysis
        job.update_progress(AnalysisJob.STAGE_POSTS, 58)
        posts_images = []
        for img_path in job.post_images_paths:
            full_path = os.path.join(settings.MEDIA_ROOT, img_path)
            if os.path.exists(full_path):
                with open(full_path, 'rb') as f:
                    posts_images.append(f.read())
        posts_analyzer = PostsAnalyzer(gemini_api_key=api_key, gemini_model=model)
        posts_data = posts_analyzer.analyze(
            images=posts_images if posts_images else None,
            text=job.posts_text if job.posts_text else None,
            profile_url=job.profile_url if job.profile_url else None,
        )
        job.update_progress(AnalysisJob.STAGE_POSTS, 75)

        # Crear BrandDNA
        BrandDNA.objects.create(
            job=job,
            business_name=web_data.get('business_name', 'Mi Negocio'),
            business_url=job.business_url,
            description=web_data.get('description', ''),
            keywords=web_data.get('keywords', []),
            audience=web_data.get('audience', ''),
            tone=web_data.get('tone', 'profesional'),
            primary_colors=logo_data.get('primary_colors', []),
            logo_elements=logo_data.get('logo_elements', ''),
            posting_style=posts_data.get('posting_style', ''),
            avg_caption_length=posts_data.get('avg_caption_length', 150),
            common_hashtags=posts_data.get('common_hashtags', []),
        )
        job.update_progress(AnalysisJob.STAGE_CONTENT, 78)

        # Encolar generación de contenido
        from core.content_pipeline.tasks import content_generation_task
        django_rq.enqueue(content_generation_task, str(job_id))

    except Exception as e:
        logger.error(f"analyze_brand_task error para job {job_id}: {e}")
        job.mark_failed(str(e))
```

- [ ] **Paso 4: Ejecutar tests**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/brand_dna/tests/test_tasks.py -v
```
Esperado: 3 tests PASSED.

- [ ] **Paso 5: Commit**

```bash
git add core/brand_dna/tasks.py core/brand_dna/tests/test_tasks.py
GIT_EDITOR=true git commit -m "feat: add analyze_brand_task RQ job orchestrating web+logo+posts analysis"
```

---

## Tarea 7: TextGenerator + ImageGenerator

**Archivos:**
- Crear: `core/content_pipeline/generators/text_generator.py`
- Crear: `core/content_pipeline/generators/image_generator.py`
- Crear: `core/content_pipeline/tests/test_text_generator.py`
- Crear: `core/content_pipeline/tests/test_image_generator.py`

- [ ] **Paso 1: Escribir tests del TextGenerator**

Crear `core/content_pipeline/tests/test_text_generator.py`:
```python
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.generators.text_generator import TextGenerator

pytestmark = pytest.mark.django_db

MOCK_GEMINI_RESPONSE = '''[
  {"caption": "Post 1: diseño que convierte", "hashtags": ["#diseñoweb"], "suggested_time": "19:00"},
  {"caption": "Post 2: presencia digital", "hashtags": ["#marketing"], "suggested_time": "12:00"},
  {"caption": "Post 3: tu marca online", "hashtags": ["#branding"], "suggested_time": "19:00"},
  {"caption": "Post 4: resultados reales", "hashtags": ["#resultados"], "suggested_time": "09:00"},
  {"caption": "Post 5: clientes felices", "hashtags": ["#testimonios"], "suggested_time": "19:00"},
  {"caption": "Post 6: innovación digital", "hashtags": ["#tech"], "suggested_time": "12:00"},
  {"caption": "Post 7: cierra la semana", "hashtags": ["#viernes"], "suggested_time": "17:00"}
]'''


@pytest.fixture
def brand_dna():
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://tuwebmx.com')
    return BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseño', 'web'],
        audience='PYMEs', tone='profesional', primary_colors=['#1a1a2e'],
    )


@override_settings(GEMINI_API_KEY='test-key', AI_MODEL='gemini-2.5-flash')
def test_generate_returns_7_posts(brand_dna):
    with patch('core.content_pipeline.generators.text_generator.GeminiAdapter') as MockGemini:
        MockGemini.return_value.generate_response.return_value = MOCK_GEMINI_RESPONSE
        gen = TextGenerator()
        result = gen.generate(brand_dna)

    assert len(result) == 7


@override_settings(GEMINI_API_KEY='test-key', AI_MODEL='gemini-2.5-flash')
def test_generate_post_has_required_keys(brand_dna):
    with patch('core.content_pipeline.generators.text_generator.GeminiAdapter') as MockGemini:
        MockGemini.return_value.generate_response.return_value = MOCK_GEMINI_RESPONSE
        gen = TextGenerator()
        result = gen.generate(brand_dna)

    post = result[0]
    assert 'caption' in post
    assert 'hashtags' in post
    assert 'suggested_time' in post
    assert isinstance(post['hashtags'], list)
```

- [ ] **Paso 2: Escribir tests del ImageGenerator**

Crear `core/content_pipeline/tests/test_image_generator.py`:
```python
import pytest
from unittest.mock import patch, MagicMock
from core.content_pipeline.generators.image_generator import ImageGenerator


def test_build_prompt_includes_colors():
    gen = ImageGenerator(bucket_name='test-bucket')
    prompt = gen._build_prompt(
        caption='Diseño web profesional para tu empresa',
        colors=['#1a1a2e', '#e94560'],
        tone='profesional',
    )
    assert '#1a1a2e' in prompt
    assert 'profesional' in prompt


def test_generate_returns_url():
    gen = ImageGenerator(bucket_name='test-bucket')
    with patch('requests.get') as mock_get, \
         patch.object(gen, '_upload_to_storage', return_value='https://storage.googleapis.com/test/img.jpg'):
        mock_get.return_value.status_code = 200
        mock_get.return_value.content = b'fake-image-content'
        url = gen.generate(
            caption='Diseño web profesional',
            colors=['#1a1a2e'],
            tone='profesional',
            filename='test-img',
        )
    assert url.startswith('https://')


def test_generate_returns_fallback_on_error():
    gen = ImageGenerator(bucket_name='test-bucket')
    with patch('requests.get') as mock_get:
        mock_get.side_effect = Exception('Connection error')
        url = gen.generate(
            caption='Diseño web profesional',
            colors=['#1a1a2e'],
            tone='profesional',
            filename='test-img',
        )
    assert url == ''
```

- [ ] **Paso 3: Ejecutar para verificar que fallan**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/content_pipeline/tests/test_text_generator.py core/content_pipeline/tests/test_image_generator.py -v 2>&1 | tail -5
```

- [ ] **Paso 4: Implementar TextGenerator**

Crear `core/content_pipeline/generators/text_generator.py`:
```python
import json
import logging
from django.conf import settings
from core.agent.infrastructure.gemini_adapter import GeminiAdapter
from core.brand_dna.models import BrandDNA

logger = logging.getLogger(__name__)

_PROMPT = """
Eres un experto en marketing de contenidos. Genera exactamente 7 posts para redes sociales
para la siguiente marca. Cada post debe ser único y usar el tono y audiencia de la marca.

MARCA: {business_name}
DESCRIPCIÓN: {description}
AUDIENCIA: {audience}
TONO: {tone}
KEYWORDS: {keywords}
ESTILO DE POSTS PREVIOS: {posting_style}
HASHTAGS COMUNES: {hashtags}

Responde ÚNICAMENTE con un array JSON de 7 objetos, sin markdown:
[
  {{
    "caption": "texto del post, máximo {avg_length} caracteres",
    "hashtags": ["#tag1", "#tag2", "#tag3"],
    "suggested_time": "HH:MM"
  }}
]

Los horarios sugeridos deben variar entre 09:00, 12:00, 17:00 y 19:00.
"""


class TextGenerator:
    def generate(self, brand_dna: BrandDNA) -> list[dict]:
        adapter = GeminiAdapter()
        prompt = _PROMPT.format(
            business_name=brand_dna.business_name,
            description=brand_dna.description,
            audience=brand_dna.audience,
            tone=brand_dna.tone,
            keywords=', '.join(brand_dna.keywords),
            posting_style=brand_dna.posting_style or 'No disponible',
            hashtags=', '.join(brand_dna.common_hashtags),
            avg_length=brand_dna.avg_caption_length,
        )
        raw = adapter.generate_response(
            prompt,
            api_key=settings.GEMINI_API_KEY,
            model_name=settings.AI_MODEL,
        )
        raw = raw.strip().lstrip('```json').lstrip('```').rstrip('```').strip()
        posts = json.loads(raw)
        return posts[:7]
```

- [ ] **Paso 5: Implementar ImageGenerator**

Crear `core/content_pipeline/generators/image_generator.py`:
```python
import logging
import urllib.parse
import requests
from google.cloud import storage

logger = logging.getLogger(__name__)

_POLLINATIONS_URL = 'https://image.pollinations.ai/prompt/{prompt}?width=1080&height=1080&nologo=true'


class ImageGenerator:
    def __init__(self, bucket_name: str):
        self._bucket = bucket_name

    def generate(self, caption: str, colors: list[str], tone: str, filename: str) -> str:
        try:
            prompt = self._build_prompt(caption, colors, tone)
            image_bytes = self._fetch_from_pollinations(prompt)
            return self._upload_to_storage(image_bytes, filename)
        except Exception as e:
            logger.error(f"ImageGenerator error: {e}")
            return ''

    def _build_prompt(self, caption: str, colors: list[str], tone: str) -> str:
        color_str = ', '.join(colors[:3]) if colors else 'modern colors'
        return (
            f"Social media post image for: {caption[:100]}. "
            f"Brand colors: {color_str}. Style: {tone}, professional, clean, "
            f"high quality photography, no text overlay, square format."
        )

    def _fetch_from_pollinations(self, prompt: str) -> bytes:
        encoded = urllib.parse.quote(prompt)
        url = _POLLINATIONS_URL.format(prompt=encoded)
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.content

    def _upload_to_storage(self, image_bytes: bytes, filename: str) -> str:
        client = storage.Client()
        bucket = client.bucket(self._bucket)
        blob = bucket.blob(f'posts/{filename}.jpg')
        blob.upload_from_string(image_bytes, content_type='image/jpeg')
        blob.make_public()
        return blob.public_url
```

- [ ] **Paso 6: Ejecutar tests**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/content_pipeline/tests/test_text_generator.py core/content_pipeline/tests/test_image_generator.py -v
```
Esperado: 5 tests PASSED.

- [ ] **Paso 7: Commit**

```bash
git add core/content_pipeline/generators/ core/content_pipeline/tests/test_text_generator.py core/content_pipeline/tests/test_image_generator.py
GIT_EDITOR=true git commit -m "feat: add TextGenerator (Gemini) and ImageGenerator (Pollinations+CloudStorage)"
```

---

## Tarea 8: EmailSender + Templates

**Archivos:**
- Crear: `core/content_pipeline/email_sender.py`
- Crear: `core/content_pipeline/templates/content_pipeline/email_initial.html`
- Crear: `core/content_pipeline/templates/content_pipeline/email_daily.html`
- Crear: `core/content_pipeline/tests/test_email_sender.py`

- [ ] **Paso 1: Escribir tests**

Crear `core/content_pipeline/tests/test_email_sender.py`:
```python
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost
from core.content_pipeline.email_sender import EmailSender

pytestmark = pytest.mark.django_db


@pytest.fixture
def full_setup():
    job = AnalysisJob.objects.create(email='cliente@ejemplo.com', business_url='https://tuwebmx.com')
    dna = BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseño'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    calendar = ContentCalendar.objects.create(brand_dna=dna)
    posts = []
    for i in range(1, 8):
        posts.append(ContentPost.objects.create(
            calendar=calendar, day_number=i,
            caption=f'Post del día {i}',
            image_url=f'https://storage.googleapis.com/bucket/img{i}.jpg',
            suggested_time='19:00',
            hashtags=['#diseñoweb'],
            scheduled_at=timezone.now() + timedelta(days=i),
        ))
    return job, dna, calendar, posts


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_initial_email_calls_django_send(full_setup):
    job, dna, calendar, posts = full_setup
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_initial(job=job, brand_dna=dna, calendar=calendar)
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert job.email in call_kwargs[1]['recipient_list']


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_initial_email_subject_contains_business_name(full_setup):
    job, dna, calendar, posts = full_setup
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_initial(job=job, brand_dna=dna, calendar=calendar)
    subject = mock_send.call_args[0][0]
    assert 'Tu Web MX' in subject


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_daily_email_marks_post_sent(full_setup):
    job, dna, calendar, posts = full_setup
    post = posts[0]
    with patch('core.content_pipeline.email_sender.send_mail'):
        sender = EmailSender()
        sender.send_daily(post=post)
    post.refresh_from_db()
    assert post.status == ContentPost.STATUS_SENT
    assert post.sent_at is not None
```

- [ ] **Paso 2: Ejecutar para verificar que falla**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/content_pipeline/tests/test_email_sender.py -v 2>&1 | tail -5
```

- [ ] **Paso 3: Crear template email inicial**

Crear `core/content_pipeline/templates/content_pipeline/email_initial.html`:
```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Tu ADN de Marca — Agente Cosmic</title></head>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">
  <h1 style="color: #1a1a2e;">✨ Tu ADN de Marca está listo</h1>
  <p>Hola, aquí está el análisis completo de <strong>{{ brand_dna.business_name }}</strong>.</p>

  <h2 style="border-bottom: 2px solid #e94560; padding-bottom: 8px;">ADN de Marca</h2>
  <p><strong>Descripción:</strong> {{ brand_dna.description }}</p>
  <p><strong>Audiencia:</strong> {{ brand_dna.audience }}</p>
  <p><strong>Tono:</strong> {{ brand_dna.tone }}</p>
  <p><strong>Keywords:</strong> {{ brand_dna.keywords|join:", " }}</p>
  {% if brand_dna.primary_colors %}
  <p><strong>Colores de marca:</strong> {{ brand_dna.primary_colors|join:", " }}</p>
  {% endif %}

  <h2 style="border-bottom: 2px solid #e94560; padding-bottom: 8px;">Tu Calendario de Contenido</h2>
  {% for post in posts %}
  <div style="background: #f9f9f9; padding: 12px; margin: 8px 0; border-left: 4px solid #e94560;">
    <strong>Día {{ post.day_number }}</strong> — {{ post.suggested_time }}
    <p>{{ post.caption }}</p>
    <small>{{ post.hashtags|join:" " }}</small>
  </div>
  {% endfor %}

  <h2 style="border-bottom: 2px solid #e94560; padding-bottom: 8px;">Contenido del Día 1</h2>
  <p>{{ day1.caption }}</p>
  {% if day1.image_url %}
  <img src="{{ day1.image_url }}" style="max-width: 100%; border-radius: 8px;" alt="Post día 1">
  {% endif %}
  <p>Hashtags: {{ day1.hashtags|join:" " }}</p>
  <p><em>Mañana recibirás el contenido del Día 2 en este correo.</em></p>

  <hr>
  <p style="font-size: 12px; color: #999;">Agente Cosmic — Powered by Google Cloud</p>
</body>
</html>
```

- [ ] **Paso 4: Crear template email diario**

Crear `core/content_pipeline/templates/content_pipeline/email_daily.html`:
```html
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>Día {{ post.day_number }} — Agente Cosmic</title></head>
<body style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; color: #333;">
  <h1 style="color: #1a1a2e;">📅 Día {{ post.day_number }} de tu calendario</h1>
  <p>Contenido listo para publicar hoy en <strong>{{ post.calendar.brand_dna.business_name }}</strong>.</p>

  <div style="background: #f9f9f9; padding: 20px; border-radius: 8px; margin: 20px 0;">
    <h2 style="margin-top: 0;">Tu post de hoy</h2>
    <p style="font-size: 16px;">{{ post.caption }}</p>
    {% if post.image_url %}
    <img src="{{ post.image_url }}" style="max-width: 100%; border-radius: 8px; margin: 12px 0;" alt="Imagen del post">
    {% endif %}
    <p><strong>Horario sugerido:</strong> {{ post.suggested_time }}</p>
    <p><strong>Hashtags:</strong> {{ post.hashtags|join:" " }}</p>
  </div>

  <hr>
  <p style="font-size: 12px; color: #999;">Agente Cosmic — Powered by Google Cloud</p>
</body>
</html>
```

- [ ] **Paso 5: Implementar EmailSender**

Crear `core/content_pipeline/email_sender.py`:
```python
import logging
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils import timezone
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost

logger = logging.getLogger(__name__)


class EmailSender:
    def send_initial(self, job: AnalysisJob, brand_dna: BrandDNA, calendar: ContentCalendar) -> None:
        posts = list(calendar.posts.order_by('day_number'))
        day1 = posts[0] if posts else None
        html = render_to_string('content_pipeline/email_initial.html', {
            'brand_dna': brand_dna,
            'posts': posts,
            'day1': day1,
        })
        send_mail(
            subject=f'Tu ADN de Marca está listo — {brand_dna.business_name}',
            message=f'Tu ADN de Marca de {brand_dna.business_name} está listo.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[job.email],
            html_message=html,
            fail_silently=False,
        )
        logger.info(f"Email inicial enviado a {job.email} para job {job.id}")

    def send_daily(self, post: ContentPost) -> None:
        html = render_to_string('content_pipeline/email_daily.html', {'post': post})
        business_name = post.calendar.brand_dna.business_name
        email = post.calendar.brand_dna.job.email
        send_mail(
            subject=f'Día {post.day_number} de tu calendario — {business_name}',
            message=f'Tu contenido del día {post.day_number} está listo.',
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            html_message=html,
            fail_silently=False,
        )
        post.status = ContentPost.STATUS_SENT
        post.sent_at = timezone.now()
        post.save(update_fields=['status', 'sent_at'])
        logger.info(f"Email día {post.day_number} enviado a {email}")
```

- [ ] **Paso 6: Ejecutar tests**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/content_pipeline/tests/test_email_sender.py -v
```
Esperado: 3 tests PASSED.

- [ ] **Paso 7: Commit**

```bash
git add core/content_pipeline/email_sender.py core/content_pipeline/templates/ core/content_pipeline/tests/test_email_sender.py
GIT_EDITOR=true git commit -m "feat: add EmailSender with HTML templates for initial and daily emails"
```

---

## Tarea 9: Scheduler + content_generation_task

**Archivos:**
- Crear: `core/content_pipeline/scheduler.py`
- Crear: `core/content_pipeline/tasks.py`
- Crear: `core/content_pipeline/tests/test_scheduler.py`
- Crear: `core/content_pipeline/tests/test_tasks.py`

- [ ] **Paso 1: Escribir tests del scheduler**

Crear `core/content_pipeline/tests/test_scheduler.py`:
```python
import pytest
from unittest.mock import patch, MagicMock
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost
from core.content_pipeline.scheduler import schedule_daily_emails

pytestmark = pytest.mark.django_db


@pytest.fixture
def calendar_with_7_posts():
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://tuwebmx.com')
    dna = BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Test', keywords=[], audience='Test', tone='profesional', primary_colors=[],
    )
    cal = ContentCalendar.objects.create(brand_dna=dna)
    for i in range(1, 8):
        ContentPost.objects.create(
            calendar=cal, day_number=i, caption=f'Post {i}',
            image_url='https://example.com/img.jpg', suggested_time='19:00',
            hashtags=[], scheduled_at=timezone.now() + timedelta(days=i),
        )
    return cal


def test_schedule_daily_emails_enqueues_6_jobs(calendar_with_7_posts):
    with patch('core.content_pipeline.scheduler.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue
        schedule_daily_emails(calendar_with_7_posts)

    assert mock_queue.enqueue_in.call_count == 6


def test_schedule_daily_emails_skips_day_1(calendar_with_7_posts):
    with patch('core.content_pipeline.scheduler.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue
        schedule_daily_emails(calendar_with_7_posts)

    calls = mock_queue.enqueue_in.call_args_list
    for call in calls:
        post_id = str(call[0][2])
        post = ContentPost.objects.get(id=post_id)
        assert post.day_number != 1
```

- [ ] **Paso 2: Ejecutar para verificar que falla**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/content_pipeline/tests/test_scheduler.py -v 2>&1 | tail -5
```

- [ ] **Paso 3: Implementar scheduler.py**

Crear `core/content_pipeline/scheduler.py`:
```python
import logging
from datetime import timedelta
import django_rq
from django.utils import timezone
from core.content_pipeline.models import ContentCalendar, ContentPost

logger = logging.getLogger(__name__)


def schedule_daily_emails(calendar: ContentCalendar) -> None:
    queue = django_rq.get_queue('default')
    from core.content_pipeline.tasks import send_daily_email_task
    now = timezone.now()
    posts = list(calendar.posts.filter(day_number__gt=1).order_by('day_number'))
    for post in posts:
        delta = post.scheduled_at - now
        if delta.total_seconds() < 0:
            delta = timedelta(minutes=1)
        queue.enqueue_in(delta, send_daily_email_task, str(post.id))
        logger.info(f"Día {post.day_number} programado en {delta}")
```

- [ ] **Paso 4: Escribir tests de content_generation_task**

Crear `core/content_pipeline/tests/test_tasks.py`:
```python
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost

pytestmark = pytest.mark.django_db

_MOCK_POSTS = [
    {'caption': f'Post {i}', 'hashtags': ['#test'], 'suggested_time': '19:00'}
    for i in range(1, 8)
]


@pytest.fixture
def job_with_dna():
    job = AnalysisJob.objects.create(
        email='t@t.com', business_url='https://tuwebmx.com',
        status=AnalysisJob.STATUS_PROCESSING, stage=AnalysisJob.STAGE_CONTENT, progress=78,
    )
    BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseño'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    return job


@override_settings(GEMINI_API_KEY='test-key', AI_MODEL='gemini-2.5-flash',
                   GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket', DEFAULT_FROM_EMAIL='noreply@test.com')
def test_content_generation_creates_calendar(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender') as MockEmail, \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    assert ContentCalendar.objects.filter(brand_dna__job=job_with_dna).exists()
    assert ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna).count() == 7


@override_settings(GEMINI_API_KEY='test-key', AI_MODEL='gemini-2.5-flash',
                   GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket', DEFAULT_FROM_EMAIL='noreply@test.com')
def test_content_generation_marks_job_done(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    job_with_dna.refresh_from_db()
    assert job_with_dna.status == AnalysisJob.STATUS_DONE
    assert job_with_dna.progress == 100
```

- [ ] **Paso 5: Implementar tasks.py de content_pipeline**

Crear `core/content_pipeline/tasks.py`:
```python
import logging
from datetime import datetime, time, timedelta
import django_rq
from django.conf import settings
from django.utils import timezone
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost
from core.content_pipeline.generators.text_generator import TextGenerator
from core.content_pipeline.generators.image_generator import ImageGenerator
from core.content_pipeline.email_sender import EmailSender
from core.content_pipeline.scheduler import schedule_daily_emails

logger = logging.getLogger(__name__)


def content_generation_task(job_id: str) -> None:
    job = AnalysisJob.objects.get(id=job_id)
    brand_dna = job.brand_dna

    try:
        job.update_progress(AnalysisJob.STAGE_CONTENT, 80)

        # Generar 7 captions
        text_gen = TextGenerator()
        posts_data = text_gen.generate(brand_dna)
        job.update_progress(AnalysisJob.STAGE_CONTENT, 87)

        # Crear calendario
        calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        now = timezone.now()

        for i, post_data in enumerate(posts_data, start=1):
            hour, minute = map(int, post_data.get('suggested_time', '19:00').split(':'))
            scheduled = (now + timedelta(days=i)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            image_url = image_gen.generate(
                caption=post_data['caption'],
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                filename=f"{job_id}-day{i}",
            )
            ContentPost.objects.create(
                calendar=calendar,
                day_number=i,
                caption=post_data['caption'],
                image_url=image_url,
                suggested_time=f"{hour:02d}:{minute:02d}",
                hashtags=post_data.get('hashtags', []),
                scheduled_at=scheduled,
            )

        job.update_progress(AnalysisJob.STAGE_CONTENT, 95)

        # Enviar email inicial
        EmailSender().send_initial(job=job, brand_dna=brand_dna, calendar=calendar)

        # Programar emails días 2-7
        schedule_daily_emails(calendar)

        # Marcar completo
        job.stage = AnalysisJob.STAGE_COMPLETE
        job.progress = 100
        job.status = AnalysisJob.STATUS_DONE
        job.save(update_fields=['stage', 'progress', 'status'])
        logger.info(f"Job {job_id} completado exitosamente")

    except Exception as e:
        logger.error(f"content_generation_task error para job {job_id}: {e}")
        job.mark_failed(str(e))


def send_daily_email_task(post_id: str) -> None:
    post = ContentPost.objects.select_related(
        'calendar__brand_dna__job'
    ).get(id=post_id)
    EmailSender().send_daily(post=post)
```

- [ ] **Paso 6: Ejecutar todos los tests nuevos**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/content_pipeline/tests/ -v
```
Esperado: todos PASSED.

- [ ] **Paso 7: Commit**

```bash
git add core/content_pipeline/scheduler.py core/content_pipeline/tasks.py core/content_pipeline/tests/test_scheduler.py core/content_pipeline/tests/test_tasks.py
GIT_EDITOR=true git commit -m "feat: add content_generation_task, send_daily_email_task and scheduler"
```

---

## Tarea 10: Views, URLs y endpoint de polling

**Archivos:**
- Crear: `core/brand_dna/views.py`
- Crear: `core/brand_dna/urls.py`
- Crear: `core/brand_dna/tests/test_views.py`
- Modificar: `saas_chatbot/urls.py`
- Modificar: `saas_chatbot/settings.py` (MEDIA_ROOT)

- [ ] **Paso 1: Agregar MEDIA_ROOT a settings.py**

En `saas_chatbot/settings.py`, agregar después de `STATIC_URL`:
```python
import os
MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')
```

- [ ] **Paso 2: Escribir tests**

Crear `core/brand_dna/tests/test_views.py`:
```python
import pytest
import json
from unittest.mock import patch
from django.test import Client, override_settings
from django.urls import reverse
from core.brand_dna.models import AnalysisJob, BrandDNA

pytestmark = pytest.mark.django_db
client = Client()


def test_landing_page_returns_200():
    c = Client()
    response = c.get('/')
    assert response.status_code == 200


def test_analyze_submit_creates_job():
    c = Client()
    with patch('core.brand_dna.views.django_rq') as mock_rq:
        response = c.post('/analizar/', {
            'email': 'test@example.com',
            'business_url': 'https://tuwebmx.com',
        })
    assert response.status_code == 302
    assert AnalysisJob.objects.filter(email='test@example.com').exists()


def test_analyze_submit_enqueues_task():
    c = Client()
    with patch('core.brand_dna.views.django_rq') as mock_rq:
        c.post('/analizar/', {
            'email': 'test@example.com',
            'business_url': 'https://tuwebmx.com',
        })
    mock_rq.enqueue.assert_called_once()


def test_status_api_returns_progress():
    c = Client()
    job = AnalysisJob.objects.create(
        email='t@t.com', business_url='https://tuwebmx.com',
        status='processing', stage='logo', progress=50,
    )
    response = c.get(f'/api/brand-dna/status/{job.id}/')
    assert response.status_code == 200
    data = json.loads(response.content)
    assert data['progress'] == 50
    assert data['stage'] == 'logo'
    assert data['status'] == 'processing'


def test_status_api_returns_brand_dna_when_done():
    c = Client()
    job = AnalysisJob.objects.create(
        email='t@t.com', business_url='https://tuwebmx.com',
        status='done', stage='complete', progress=100,
    )
    BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseño'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    response = c.get(f'/api/brand-dna/status/{job.id}/')
    data = json.loads(response.content)
    assert data['brand_dna'] is not None
    assert data['brand_dna']['business_name'] == 'Tu Web MX'


def test_results_page_returns_200():
    c = Client()
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://tuwebmx.com')
    response = c.get(f'/resultados/{job.id}/')
    assert response.status_code == 200
```

- [ ] **Paso 3: Ejecutar para verificar que fallan**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/brand_dna/tests/test_views.py -v 2>&1 | tail -8
```

- [ ] **Paso 4: Implementar views.py**

Crear `core/brand_dna/views.py`:
```python
import os
import django_rq
from django.conf import settings
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from core.brand_dna.models import AnalysisJob, BrandDNA


def landing(request):
    return render(request, 'brand_dna/landing.html')


def analyze_submit(request):
    if request.method != 'POST':
        return redirect('landing')

    email = request.POST.get('email', '').strip()
    business_url = request.POST.get('business_url', '').strip()
    posts_text = request.POST.get('posts_text', '').strip()
    profile_url = request.POST.get('profile_url', '').strip()

    job = AnalysisJob.objects.create(
        email=email,
        business_url=business_url,
        posts_text=posts_text,
        profile_url=profile_url,
    )

    # Guardar logo si se subió
    if 'logo' in request.FILES:
        logo_file = request.FILES['logo']
        ext = logo_file.name.rsplit('.', 1)[-1].lower()
        logo_path = f'uploads/logo_{job.id}.{ext}'
        full_path = os.path.join(settings.MEDIA_ROOT, logo_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'wb') as f:
            for chunk in logo_file.chunks():
                f.write(chunk)
        job.logo_file_path = logo_path
        job.save(update_fields=['logo_file_path'])

    # Guardar imágenes de posts si se subieron
    post_paths = []
    for i, img_file in enumerate(request.FILES.getlist('post_images')):
        img_path = f'uploads/post_{job.id}_{i}.jpg'
        full_path = os.path.join(settings.MEDIA_ROOT, img_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'wb') as f:
            for chunk in img_file.chunks():
                f.write(chunk)
        post_paths.append(img_path)

    if post_paths:
        job.post_images_paths = post_paths
        job.save(update_fields=['post_images_paths'])

    from core.brand_dna.tasks import analyze_brand_task
    django_rq.enqueue(analyze_brand_task, str(job.id))

    return redirect('results', job_id=str(job.id))


def results(request, job_id):
    job = get_object_or_404(AnalysisJob, id=job_id)
    brand_dna = getattr(job, 'brand_dna', None)
    calendar = None
    if brand_dna:
        calendar = getattr(brand_dna, 'calendar', None)
    return render(request, 'brand_dna/results.html', {
        'job': job,
        'brand_dna': brand_dna,
        'calendar': calendar,
    })


def status_api(request, job_id):
    job = get_object_or_404(AnalysisJob, id=job_id)
    brand_dna = getattr(job, 'brand_dna', None)
    brand_dna_data = None
    calendar_data = None

    if brand_dna:
        brand_dna_data = {
            'business_name': brand_dna.business_name,
            'description': brand_dna.description,
            'audience': brand_dna.audience,
            'tone': brand_dna.tone,
            'keywords': brand_dna.keywords,
            'primary_colors': brand_dna.primary_colors,
            'logo_elements': brand_dna.logo_elements,
            'posting_style': brand_dna.posting_style,
        }
        calendar = getattr(brand_dna, 'calendar', None)
        if calendar:
            calendar_data = [
                {
                    'day_number': p.day_number,
                    'caption': p.caption,
                    'image_url': p.image_url,
                    'suggested_time': str(p.suggested_time),
                    'hashtags': p.hashtags,
                }
                for p in calendar.posts.all()
            ]

    return JsonResponse({
        'status': job.status,
        'stage': job.stage,
        'progress': job.progress,
        'error': job.error_message,
        'brand_dna': brand_dna_data,
        'calendar': calendar_data,
    })
```

- [ ] **Paso 5: Crear urls.py**

Crear `core/brand_dna/urls.py`:
```python
from django.urls import path
from . import views

urlpatterns = [
    path('', views.landing, name='landing'),
    path('analizar/', views.analyze_submit, name='analyze_submit'),
    path('resultados/<uuid:job_id>/', views.results, name='results'),
    path('api/brand-dna/status/<uuid:job_id>/', views.status_api, name='status_api'),
]
```

- [ ] **Paso 6: Registrar URLs en saas_chatbot/urls.py**

Modificar `saas_chatbot/urls.py` — reemplazar `path('', health_check, name='health_check'),` por:
```python
from django.conf.urls.static import static
from django.conf import settings

urlpatterns = [
    path('health/', health_check, name='health'),
    path('admin/', admin.site.urls),
    path('api/v1/agent/', include('core.agent.interfaces.urls')),
    path('', include('core.brand_dna.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
```

- [ ] **Paso 7: Ejecutar tests**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/brand_dna/tests/test_views.py -v
```
Esperado: 6 tests PASSED.

- [ ] **Paso 8: Commit**

```bash
git add core/brand_dna/views.py core/brand_dna/urls.py core/brand_dna/tests/test_views.py saas_chatbot/urls.py saas_chatbot/settings.py
GIT_EDITOR=true git commit -m "feat: add landing, analyze, results and polling views with URLs"
```

---

## Tarea 11: Templates HTML (landing + resultados)

**Archivos:**
- Crear: `core/brand_dna/templates/brand_dna/landing.html`
- Crear: `core/brand_dna/templates/brand_dna/results.html`
- Modificar: `saas_chatbot/settings.py` (TEMPLATES dirs)

- [ ] **Paso 1: Verificar configuración de templates en settings.py**

En `saas_chatbot/settings.py`, en `TEMPLATES`, verificar que `'APP_DIRS': True` esté en `True`. Si está en `False` o falta, cambiarlo a `True`. Esto habilita que Django busque templates en `<app>/templates/`.

- [ ] **Paso 2: Crear directorios de templates**

```bash
mkdir -p core/brand_dna/templates/brand_dna
```

- [ ] **Paso 3: Crear landing.html**

Crear `core/brand_dna/templates/brand_dna/landing.html`:
```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Agente Cosmic — ADN de Marca + Contenido Automático</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', sans-serif; background: #0d0d1a; color: #f0f0f0; min-height: 100vh; }
    .hero { text-align: center; padding: 60px 20px 40px; }
    .hero h1 { font-size: 2.5rem; color: #e94560; margin-bottom: 12px; }
    .hero p { font-size: 1.1rem; color: #aaa; max-width: 600px; margin: 0 auto 40px; }
    .form-card { background: #1a1a2e; max-width: 600px; margin: 0 auto; padding: 40px; border-radius: 16px; }
    .form-group { margin-bottom: 20px; }
    label { display: block; font-size: 0.9rem; color: #aaa; margin-bottom: 6px; }
    input[type="text"], input[type="email"], input[type="url"], textarea {
      width: 100%; padding: 12px; border-radius: 8px; border: 1px solid #333;
      background: #0d0d1a; color: #f0f0f0; font-size: 1rem;
    }
    textarea { height: 100px; resize: vertical; }
    .section-title { font-size: 1rem; color: #e94560; margin: 28px 0 16px; font-weight: 600; }
    .optional-badge { font-size: 0.75rem; background: #333; color: #aaa; padding: 2px 8px; border-radius: 10px; margin-left: 8px; }
    input[type="file"] { width: 100%; padding: 10px; background: #0d0d1a; border: 1px dashed #444; border-radius: 8px; color: #aaa; }
    .btn { width: 100%; padding: 16px; background: #e94560; color: #fff; border: none; border-radius: 8px; font-size: 1.1rem; font-weight: 600; cursor: pointer; margin-top: 12px; }
    .btn:hover { background: #c73652; }
    .features { display: flex; justify-content: center; gap: 32px; padding: 40px 20px; flex-wrap: wrap; }
    .feature { text-align: center; max-width: 160px; }
    .feature .icon { font-size: 2rem; margin-bottom: 8px; }
    .feature p { font-size: 0.85rem; color: #aaa; }
  </style>
</head>
<body>
  <div class="hero">
    <h1>✨ Agente Cosmic</h1>
    <p>Da la URL de tu negocio y genera automáticamente 7 días de contenido listo para publicar — posts, imágenes y más.</p>
  </div>

  <div class="form-card">
    <form method="POST" action="/analizar/" enctype="multipart/form-data">
      {% csrf_token %}
      <div class="form-group">
        <label>Tu correo electrónico</label>
        <input type="email" name="email" placeholder="tu@correo.com" required>
      </div>
      <div class="form-group">
        <label>URL de tu negocio</label>
        <input type="url" name="business_url" placeholder="https://tuempresa.com" required>
      </div>
      <div class="form-group">
        <label>Logo de tu marca <span class="optional-badge">opcional</span></label>
        <input type="file" name="logo" accept="image/*">
      </div>

      <div class="section-title">Posts anteriores <span class="optional-badge">opcional — mejora el resultado</span></div>
      <div class="form-group">
        <label>Sube hasta 5 imágenes de tus posts</label>
        <input type="file" name="post_images" accept="image/*" multiple>
      </div>
      <div class="form-group">
        <label>O pega el texto de tus últimos posts</label>
        <textarea name="posts_text" placeholder="Post 1: Hoy lanzamos...&#10;Post 2: ¿Sabías que..."></textarea>
      </div>
      <div class="form-group">
        <label>O ingresa la URL de tu perfil público</label>
        <input type="url" name="profile_url" placeholder="https://facebook.com/tupagina">
      </div>

      <button type="submit" class="btn">→ Analizar mi marca</button>
    </form>
  </div>

  <div class="features">
    <div class="feature"><div class="icon">🔬</div><p>Análisis del sitio web</p></div>
    <div class="feature"><div class="icon">🎨</div><p>Extracción de colores del logo</p></div>
    <div class="feature"><div class="icon">📅</div><p>7 días de contenido</p></div>
    <div class="feature"><div class="icon">📧</div><p>Entregado por email</p></div>
    <div class="feature"><div class="icon">☁️</div><p>Google Cloud Vision</p></div>
  </div>
</body>
</html>
```

- [ ] **Paso 4: Crear results.html**

Crear `core/brand_dna/templates/brand_dna/results.html`:
```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Analizando tu marca — Agente Cosmic</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', sans-serif; background: #0d0d1a; color: #f0f0f0; min-height: 100vh; padding: 40px 20px; }
    .container { max-width: 800px; margin: 0 auto; }
    h1 { color: #e94560; margin-bottom: 8px; }
    .subtitle { color: #aaa; margin-bottom: 32px; }
    .progress-card { background: #1a1a2e; border-radius: 12px; padding: 28px; margin-bottom: 24px; }
    .stage-list { list-style: none; }
    .stage-item { display: flex; align-items: center; gap: 12px; padding: 10px 0; color: #aaa; }
    .stage-item.active { color: #f0f0f0; }
    .stage-item.done { color: #4caf50; }
    .stage-icon { width: 24px; text-align: center; }
    .progress-bar-bg { background: #333; border-radius: 8px; height: 8px; margin-top: 16px; }
    .progress-bar { background: #e94560; border-radius: 8px; height: 8px; transition: width 0.5s; }
    .progress-text { text-align: right; font-size: 0.85rem; color: #aaa; margin-top: 6px; }
    .results-section { display: none; }
    .dna-card { background: #1a1a2e; border-radius: 12px; padding: 28px; margin-bottom: 24px; }
    .dna-card h2 { color: #e94560; margin-bottom: 16px; }
    .dna-row { display: flex; gap: 8px; margin-bottom: 10px; }
    .dna-label { color: #aaa; min-width: 110px; font-size: 0.9rem; }
    .color-swatch { display: inline-block; width: 20px; height: 20px; border-radius: 4px; margin-right: 6px; vertical-align: middle; }
    .calendar-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 16px; }
    .post-card { background: #1a1a2e; border-radius: 10px; overflow: hidden; }
    .post-card img { width: 100%; aspect-ratio: 1; object-fit: cover; }
    .post-card .post-body { padding: 14px; }
    .post-card .day { font-size: 0.8rem; color: #e94560; margin-bottom: 6px; }
    .post-card .caption { font-size: 0.9rem; color: #ddd; line-height: 1.4; }
    .email-note { background: #1a2e1a; border: 1px solid #4caf50; border-radius: 10px; padding: 16px; margin-top: 24px; color: #aaa; }
    .error-card { background: #2e1a1a; border: 1px solid #e94560; border-radius: 12px; padding: 28px; }
  </style>
</head>
<body>
<div class="container">
  <h1>✨ Agente Cosmic</h1>
  <p class="subtitle">Analizando: <strong>{{ job.business_url }}</strong></p>

  <div class="progress-card" id="progressCard">
    <ul class="stage-list" id="stageList">
      <li class="stage-item" id="stage-web"><span class="stage-icon">⟳</span> Analizando sitio web</li>
      <li class="stage-item" id="stage-logo"><span class="stage-icon">○</span> Analizando logo</li>
      <li class="stage-item" id="stage-posts"><span class="stage-icon">○</span> Analizando posts anteriores</li>
      <li class="stage-item" id="stage-content"><span class="stage-icon">○</span> Generando contenido</li>
    </ul>
    <div class="progress-bar-bg"><div class="progress-bar" id="progressBar" style="width: 5%"></div></div>
    <div class="progress-text" id="progressText">5%</div>
  </div>

  <div class="results-section" id="resultsSection">
    <div class="dna-card">
      <h2>🔬 ADN de Marca</h2>
      <div id="dnaContent"></div>
    </div>
    <h2 style="margin-bottom:16px; color:#e94560;">📅 Tu Calendario de Contenido</h2>
    <div class="calendar-grid" id="calendarGrid"></div>
    <div class="email-note">
      ✉️ Revisa tu correo — te enviamos el ADN completo y el contenido del Día 1. Recibirás un email por día durante 7 días.
    </div>
  </div>

  <div class="error-card" id="errorCard" style="display:none;">
    <h2>❌ Error en el análisis</h2>
    <p id="errorMessage" style="margin-top:12px; color:#aaa;"></p>
  </div>
</div>

<script>
  const jobId = '{{ job.id }}';
  const statusUrl = `/api/brand-dna/status/${jobId}/`;
  const stageOrder = ['web', 'logo', 'posts', 'content', 'complete'];
  const stageLabels = {web:'Analizando sitio web', logo:'Analizando logo', posts:'Analizando posts', content:'Generando contenido', complete:'¡Completo!'};
  let pollInterval;

  function updateStages(currentStage) {
    const idx = stageOrder.indexOf(currentStage);
    stageOrder.forEach((s, i) => {
      const el = document.getElementById(`stage-${s}`);
      if (!el) return;
      if (i < idx) { el.className = 'stage-item done'; el.querySelector('.stage-icon').textContent = '✓'; }
      else if (i === idx) { el.className = 'stage-item active'; el.querySelector('.stage-icon').textContent = '⟳'; }
      else { el.className = 'stage-item'; el.querySelector('.stage-icon').textContent = '○'; }
    });
  }

  function renderDna(dna) {
    const colors = (dna.primary_colors || []).map(c => `<span class="color-swatch" style="background:${c}"></span>${c}`).join(' ');
    document.getElementById('dnaContent').innerHTML = `
      <div class="dna-row"><span class="dna-label">Negocio</span><span>${dna.business_name}</span></div>
      <div class="dna-row"><span class="dna-label">Descripción</span><span>${dna.description}</span></div>
      <div class="dna-row"><span class="dna-label">Audiencia</span><span>${dna.audience}</span></div>
      <div class="dna-row"><span class="dna-label">Tono</span><span>${dna.tone}</span></div>
      <div class="dna-row"><span class="dna-label">Keywords</span><span>${(dna.keywords||[]).join(', ')}</span></div>
      <div class="dna-row"><span class="dna-label">Colores</span><span>${colors || 'No disponibles'}</span></div>
    `;
  }

  function renderCalendar(posts) {
    document.getElementById('calendarGrid').innerHTML = posts.map(p => `
      <div class="post-card">
        ${p.image_url ? `<img src="${p.image_url}" alt="Día ${p.day_number}" onerror="this.style.display='none'">` : ''}
        <div class="post-body">
          <div class="day">Día ${p.day_number} · ${p.suggested_time}</div>
          <div class="caption">${p.caption}</div>
        </div>
      </div>
    `).join('');
  }

  async function poll() {
    try {
      const res = await fetch(statusUrl);
      const data = await res.json();
      const progress = data.progress || 0;
      document.getElementById('progressBar').style.width = `${progress}%`;
      document.getElementById('progressText').textContent = `${progress}%`;
      updateStages(data.stage);

      if (data.status === 'done') {
        clearInterval(pollInterval);
        document.getElementById('progressCard').style.display = 'none';
        document.getElementById('resultsSection').style.display = 'block';
        if (data.brand_dna) renderDna(data.brand_dna);
        if (data.calendar) renderCalendar(data.calendar);
      } else if (data.status === 'failed') {
        clearInterval(pollInterval);
        document.getElementById('progressCard').style.display = 'none';
        document.getElementById('errorCard').style.display = 'block';
        document.getElementById('errorMessage').textContent = data.error || 'Error desconocido.';
      }
    } catch (e) { console.error('Polling error:', e); }
  }

  pollInterval = setInterval(poll, 3000);
  poll();
</script>
</body>
</html>
```

- [ ] **Paso 5: Verificar que las páginas cargan**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/brand_dna/tests/test_views.py -v
```
Esperado: 6 tests PASSED.

- [ ] **Paso 6: Commit**

```bash
git add core/brand_dna/templates/
GIT_EDITOR=true git commit -m "feat: add landing and results HTML templates with polling JS"
```

---

## Tarea 12: Verificación end-to-end

- [ ] **Paso 1: Correr toda la suite de tests**

```bash
docker exec agente-cosmic-backend-1 python -m pytest core/brand_dna/ core/content_pipeline/ -v --tb=short
```
Esperado: todos los tests PASSED.

- [ ] **Paso 2: Levantar el stack y verificar la landing**

```bash
docker compose up -d
```
Abrir `http://localhost:3002/` en el navegador. Verificar que se ve el formulario.

- [ ] **Paso 3: Hacer un análisis de prueba**

Llenar el formulario con:
- Email: tu correo real
- URL: `https://tuwebmx.com`
- Logo: subir una imagen de prueba

Verificar que:
1. Redirige a `/resultados/<uuid>/`
2. La barra de progreso se mueve
3. Llega el email inicial con el ADN y el contenido del Día 1

- [ ] **Paso 4: Commit final**

```bash
GIT_EDITOR=true git commit -m "feat: agente-cosmic MVP — brand DNA extraction + 7-day content calendar via email"
```
