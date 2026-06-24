# Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Instrument agente-cosmic with Prometheus metrics covering operational health and business intelligence, exposed via a dedicated scrape port.

**Architecture:** `django-prometheus` provides automatic HTTP/DB metrics. Custom metrics defined in a central `core/shared/metrics.py` module are emitted at instrumentation points (tasks, views, generators, extractors). A `track_external_api` context manager wraps all external API calls. Custom Prometheus collectors report RQ queue state and active users on each scrape. The `/metrics` endpoint is served by Django on port 8000, exposed via docker-compose on `0.0.0.0:9091`, and blocked by nginx on the public port.

**Tech Stack:** django-prometheus >= 2.3.1, prometheus_client (transitive dependency), Django 5.2, Redis/RQ, google-genai

## Global Constraints

- All metric names prefixed with `cosmic_`
- All commits use: `GIT_EDITOR=true git commit -m "msg"` — NEVER heredoc
- DB engine: `django_prometheus.db.backends.postgresql` (wraps existing psycopg2)
- No explicit CACHES config exists (Django uses LocMemCache) — no cache wrapper needed
- MIDDLEWARE order: PrometheusBeforeMiddleware first, PrometheusAfterMiddleware last
- Port 9091 exposed as `0.0.0.0:9091:8000` (VPC-accessible, not localhost-only)
- nginx blocks `/metrics` on port 80 (public traffic)
- Tests run with: `python -m pytest <path> -v --ds=saas_chatbot.settings`
- Project uses `google.genai` (not `vertexai` SDK directly) with `vertexai=True` client option
- The `resp` from `client.models.generate_content()` has `resp.usage_metadata` with `prompt_token_count` and `candidates_token_count` attributes
- Tests directory pattern: `core/<app>/tests/test_*.py`

---

### Task 1: Foundation — django-prometheus, metrics module, endpoint, infrastructure

**Files:**
- Modify: `requirements.txt`
- Modify: `saas_chatbot/settings.py:210-261` (INSTALLED_APPS, MIDDLEWARE, DATABASES)
- Modify: `saas_chatbot/urls.py:28-32`
- Modify: `docker-compose.yml:36-53` (backend ports)
- Modify: `nginx.dev.conf` (add /metrics block)
- Create: `core/shared/metrics.py`
- Create: `core/shared/metrics_utils.py`
- Test: `core/shared/tests/test_metrics.py`
- Test: `core/shared/tests/test_metrics_endpoint.py`

**Interfaces:**
- Consumes: nothing (foundation task)
- Produces:
  - `core/shared/metrics.py` exports all metric objects:
    - `ANALYSIS_JOBS_TOTAL: Counter` (labels: `status`)
    - `ANALYSIS_DURATION: Histogram`
    - `CONTENT_GENERATION_DURATION: Histogram`
    - `IMAGE_GENERATION_DURATION: Histogram`
    - `RQ_JOBS: Gauge` (labels: `state`)
    - `LOGIN_ATTEMPTS: Counter` (labels: `result`)
    - `EXTERNAL_API_REQUESTS: Counter` (labels: `service`, `status`)
    - `EXTERNAL_API_DURATION: Histogram` (labels: `service`)
    - `EXTERNAL_API_ERRORS: Counter` (labels: `service`, `error_type`)
    - `GEMINI_TOKENS: Counter` (labels: `direction`)
    - `IMAGEN_GENERATIONS: Counter`
    - `GCS_OPERATIONS: Counter` (labels: `operation`)
    - `REGISTRATIONS: Counter` (labels: `method`)
    - `EMAIL_VERIFICATIONS: Counter` (labels: `result`)
    - `INVITATION_CODES_REDEEMED: Counter`
    - `EMAILS_SENT: Counter` (labels: `type`)
    - `POST_ACTIONS: Counter` (labels: `action`)
    - `ACTIVE_USERS: Gauge`
    - `CALENDARS_CREATED: Counter`
  - `core/shared/metrics_utils.py` exports:
    - `track_external_api(service: str)` — context manager that records requests, duration, errors
    - `record_tokens(resp, service: str)` — extracts token counts from genai response

- [ ] **Step 1: Add django-prometheus to requirements.txt**

Add after the last line of `requirements.txt`:

```
django-prometheus>=2.3.1
```

- [ ] **Step 2: Update settings.py — INSTALLED_APPS**

In `saas_chatbot/settings.py`, add `'django_prometheus'` to `INSTALLED_APPS` after `'anymail'`:

```python
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_otp',
    'django_otp.plugins.otp_totp',
    'rest_framework',
    'corsheaders',
    'django_rq',
    'anymail',
    'django_prometheus',
    'core.tenant_management.apps.TenantManagementConfig',
    'core.shared.apps.SharedConfig',
    'core.brand_dna.apps.BrandDnaConfig',
    'core.content_pipeline.apps.ContentPipelineConfig',
]
```

- [ ] **Step 3: Update settings.py — MIDDLEWARE**

Add `'django_prometheus.middleware.PrometheusBeforeMiddleware'` as the very first middleware, and `'django_prometheus.middleware.PrometheusAfterMiddleware'` as the very last:

```python
MIDDLEWARE = [
    'django_prometheus.middleware.PrometheusBeforeMiddleware',
    'core.shared.middleware.security.HostHeaderValidationMiddleware',
    'core.shared.middleware.request_limits.RequestSizeLimitMiddleware',
    'core.shared.middleware.request_limits.RequestTimeoutMiddleware',
    'core.shared.middleware.request_limits.RequestBodyValidationMiddleware',
    'core.shared.middleware.security.HTTPSRedirectMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'core.shared.middleware.security.SecurityHeadersMiddleware',
    'core.shared.middleware.request_limits.SecurityHeadersEnforcementMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django_otp.middleware.OTPMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_prometheus.middleware.PrometheusAfterMiddleware',
]
```

- [ ] **Step 4: Update settings.py — DATABASES engine**

Change the engine from `django.db.backends.postgresql_psycopg2` to the prometheus wrapper:

```python
DATABASES = {
    'default': {
        'ENGINE': 'django_prometheus.db.backends.postgresql',
        'NAME': get_env('DB_NAME'),
        'USER': get_env('DB_USER'),
        'PASSWORD': get_env('DB_PASSWORD'),
        'HOST': get_env('DB_HOST', default='localhost'),
        'PORT': get_env('DB_PORT', default='5432'),
        'OPTIONS': {
            'sslmode': 'prefer',
            'connect_timeout': 10,
            'options': '-c default_transaction_isolation=serializable'
        },
        'CONN_MAX_AGE': 300,
    }
}
```

- [ ] **Step 5: Update urls.py — add prometheus URLs**

In `saas_chatbot/urls.py`, add `path('', include('django_prometheus.urls')),` to urlpatterns:

```python
urlpatterns = [
    path('health/', health_check, name='health'),
    path('admin/', cosmic_admin.urls),
    path('', include('django_prometheus.urls')),
    path('', include('core.brand_dna.urls')),
]
```

- [ ] **Step 6: Update docker-compose.yml — expose port 9091**

Add port `0.0.0.0:9091:8000` to the backend service. The backend service currently has no `ports` key:

```yaml
  backend:
    build:
      context: .
      dockerfile: Dockerfile
    volumes:
      - .:/app
      - ${HOME}/.config/gcloud/application_default_credentials.json:/root/.config/gcloud/application_default_credentials.json:ro
    environment:
      - GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json
    env_file:
      - ./.env
    dns:
      - 8.8.8.8
      - 1.1.1.1
    ports:
      - "0.0.0.0:9091:8000"
    depends_on:
      - db
      - redis
    networks:
      - cosmic-net
```

- [ ] **Step 7: Update nginx.dev.conf — block /metrics**

Add a location block that returns 404 for `/metrics` BEFORE the catch-all `location /`:

```nginx
    location /metrics {
        return 404;
    }

    location / {
        set $backend backend:8000;
```

- [ ] **Step 8: Create core/shared/metrics.py**

```python
from prometheus_client import Counter, Gauge, Histogram

ANALYSIS_JOBS_TOTAL = Counter(
    'cosmic_analysis_jobs_total',
    'Total analysis jobs',
    ['status'],
)

ANALYSIS_DURATION = Histogram(
    'cosmic_analysis_duration_seconds',
    'Duration of the full analysis pipeline',
    buckets=[10, 30, 60, 120, 300, 600],
)

CONTENT_GENERATION_DURATION = Histogram(
    'cosmic_content_generation_duration_seconds',
    'Duration of content generation task',
    buckets=[10, 30, 60, 120, 300, 600],
)

IMAGE_GENERATION_DURATION = Histogram(
    'cosmic_image_generation_duration_seconds',
    'Duration of single image generation',
    buckets=[5, 10, 20, 40, 60, 120],
)

RQ_JOBS = Gauge(
    'cosmic_rq_jobs',
    'RQ jobs by state',
    ['state'],
)

LOGIN_ATTEMPTS = Counter(
    'cosmic_login_attempts_total',
    'Login attempts',
    ['result'],
)

EXTERNAL_API_REQUESTS = Counter(
    'cosmic_external_api_requests_total',
    'External API requests',
    ['service', 'status'],
)

EXTERNAL_API_DURATION = Histogram(
    'cosmic_external_api_duration_seconds',
    'External API call duration',
    ['service'],
    buckets=[0.5, 1, 2, 5, 10, 30, 60],
)

EXTERNAL_API_ERRORS = Counter(
    'cosmic_external_api_errors_total',
    'External API errors',
    ['service', 'error_type'],
)

GEMINI_TOKENS = Counter(
    'cosmic_gemini_tokens_total',
    'Gemini tokens consumed',
    ['direction'],
)

IMAGEN_GENERATIONS = Counter(
    'cosmic_imagen_generations_total',
    'Imagen 3 images generated',
)

GCS_OPERATIONS = Counter(
    'cosmic_gcs_storage_operations_total',
    'Google Cloud Storage operations',
    ['operation'],
)

REGISTRATIONS = Counter(
    'cosmic_registrations_total',
    'User registrations',
    ['method'],
)

EMAIL_VERIFICATIONS = Counter(
    'cosmic_email_verifications_total',
    'Email verifications',
    ['result'],
)

INVITATION_CODES_REDEEMED = Counter(
    'cosmic_invitation_codes_redeemed_total',
    'Invitation codes redeemed',
)

EMAILS_SENT = Counter(
    'cosmic_emails_sent_total',
    'Emails sent',
    ['type'],
)

POST_ACTIONS = Counter(
    'cosmic_post_actions_total',
    'Content post actions',
    ['action'],
)

ACTIVE_USERS = Gauge(
    'cosmic_active_users',
    'Users with login in last 24h',
)

CALENDARS_CREATED = Counter(
    'cosmic_calendars_created_total',
    'Content calendars created',
)
```

- [ ] **Step 9: Create core/shared/metrics_utils.py**

```python
import time
import logging
from contextlib import contextmanager
from core.shared.metrics import (
    EXTERNAL_API_REQUESTS,
    EXTERNAL_API_DURATION,
    EXTERNAL_API_ERRORS,
    GEMINI_TOKENS,
)

logger = logging.getLogger(__name__)


@contextmanager
def track_external_api(service: str):
    start = time.monotonic()
    try:
        yield
        elapsed = time.monotonic() - start
        EXTERNAL_API_REQUESTS.labels(service=service, status='success').inc()
        EXTERNAL_API_DURATION.labels(service=service).observe(elapsed)
    except Exception as exc:
        elapsed = time.monotonic() - start
        EXTERNAL_API_DURATION.labels(service=service).observe(elapsed)
        error_type = _classify_error(exc)
        EXTERNAL_API_REQUESTS.labels(service=service, status='error').inc()
        EXTERNAL_API_ERRORS.labels(service=service, error_type=error_type).inc()
        raise


def record_tokens(resp, service: str = 'gemini'):
    try:
        usage = getattr(resp, 'usage_metadata', None)
        if usage:
            prompt = getattr(usage, 'prompt_token_count', 0) or 0
            candidates = getattr(usage, 'candidates_token_count', 0) or 0
            if prompt:
                GEMINI_TOKENS.labels(direction='input').inc(prompt)
            if candidates:
                GEMINI_TOKENS.labels(direction='output').inc(candidates)
    except Exception:
        pass


def _classify_error(exc: Exception) -> str:
    msg = str(exc).lower()
    if 'timeout' in msg or 'timed out' in msg:
        return 'timeout'
    if '429' in msg:
        return 'rate_limit'
    if '4' in msg[:1] and any(c in msg for c in ('400', '401', '403', '404')):
        return 'client_error'
    if '5' in msg[:1] and any(c in msg for c in ('500', '502', '503')):
        return 'server_error'
    if 'connection' in msg:
        return 'connection_error'
    return 'unknown'
```

- [ ] **Step 10: Write test for metrics module**

Create `core/shared/tests/test_metrics.py`:

```python
import pytest
from core.shared.metrics import (
    ANALYSIS_JOBS_TOTAL,
    ANALYSIS_DURATION,
    EXTERNAL_API_REQUESTS,
    GEMINI_TOKENS,
    CALENDARS_CREATED,
)
from core.shared.metrics_utils import track_external_api, record_tokens


@pytest.mark.django_db
class TestMetricsDefinitions:
    def test_counter_increments(self):
        before = CALENDARS_CREATED._value.get()
        CALENDARS_CREATED.inc()
        assert CALENDARS_CREATED._value.get() == before + 1

    def test_counter_with_labels(self):
        ANALYSIS_JOBS_TOTAL.labels(status='completed').inc()
        val = ANALYSIS_JOBS_TOTAL.labels(status='completed')._value.get()
        assert val >= 1

    def test_histogram_observes(self):
        ANALYSIS_DURATION.observe(5.0)
        assert ANALYSIS_DURATION._sum.get() >= 5.0


class TestTrackExternalApi:
    def test_success_increments_counter(self):
        before = EXTERNAL_API_REQUESTS.labels(service='test_svc', status='success')._value.get()
        with track_external_api('test_svc'):
            pass
        after = EXTERNAL_API_REQUESTS.labels(service='test_svc', status='success')._value.get()
        assert after == before + 1

    def test_error_increments_error_counter(self):
        before = EXTERNAL_API_REQUESTS.labels(service='test_err', status='error')._value.get()
        with pytest.raises(ValueError):
            with track_external_api('test_err'):
                raise ValueError('test error')
        after = EXTERNAL_API_REQUESTS.labels(service='test_err', status='error')._value.get()
        assert after == before + 1

    def test_timeout_classified(self):
        with pytest.raises(TimeoutError):
            with track_external_api('test_timeout'):
                raise TimeoutError('connection timed out')


class TestRecordTokens:
    def test_records_tokens_from_response(self):
        before_in = GEMINI_TOKENS.labels(direction='input')._value.get()
        before_out = GEMINI_TOKENS.labels(direction='output')._value.get()

        class FakeUsage:
            prompt_token_count = 100
            candidates_token_count = 50

        class FakeResp:
            usage_metadata = FakeUsage()

        record_tokens(FakeResp())
        assert GEMINI_TOKENS.labels(direction='input')._value.get() == before_in + 100
        assert GEMINI_TOKENS.labels(direction='output')._value.get() == before_out + 50

    def test_handles_missing_usage(self):
        class FakeResp:
            pass
        record_tokens(FakeResp())
```

- [ ] **Step 11: Write test for /metrics endpoint**

Create `core/shared/tests/test_metrics_endpoint.py`:

```python
import pytest
from django.test import Client


@pytest.mark.django_db
class TestMetricsEndpoint:
    def test_metrics_returns_200(self):
        client = Client()
        resp = client.get('/metrics')
        assert resp.status_code == 200

    def test_metrics_contains_django_prometheus(self):
        client = Client()
        resp = client.get('/metrics')
        body = resp.content.decode()
        assert 'django_http_requests_total' in body

    def test_metrics_contains_custom_metrics(self):
        client = Client()
        resp = client.get('/metrics')
        body = resp.content.decode()
        assert 'cosmic_analysis_jobs_total' in body
        assert 'cosmic_external_api_requests_total' in body
        assert 'cosmic_calendars_created_total' in body
```

- [ ] **Step 12: Run tests**

Run: `python -m pytest core/shared/tests/test_metrics.py core/shared/tests/test_metrics_endpoint.py -v --ds=saas_chatbot.settings`

Expected: all tests PASS

- [ ] **Step 13: Commit**

```bash
git add requirements.txt saas_chatbot/settings.py saas_chatbot/urls.py docker-compose.yml nginx.dev.conf core/shared/metrics.py core/shared/metrics_utils.py core/shared/tests/test_metrics.py core/shared/tests/test_metrics_endpoint.py
GIT_EDITOR=true git commit -m "feat(observability): django-prometheus + custom metrics module + /metrics endpoint"
```

---

### Task 2: Instrument extractors and generators — external API tracking

**Files:**
- Modify: `core/content_pipeline/generators/text_generator.py:39-57`
- Modify: `core/content_pipeline/generators/image_generator.py:173-206,208-242,244-273,288-326,356-390,392-427,447-515,566-605,607-613`
- Modify: `core/brand_dna/extractors/web_scraper.py:66-125`
- Modify: `core/brand_dna/extractors/logo_analyzer.py:26-56`
- Modify: `core/brand_dna/extractors/posts_analyzer.py:47-100`

**Interfaces:**
- Consumes from Task 1:
  - `from core.shared.metrics_utils import track_external_api, record_tokens`
  - `from core.shared.metrics import IMAGEN_GENERATIONS, GCS_OPERATIONS, IMAGE_GENERATION_DURATION`
- Produces: All external API calls wrapped with `track_external_api`, token usage recorded via `record_tokens`

- [ ] **Step 1: Instrument TextGenerator**

In `core/content_pipeline/generators/text_generator.py`, add import at top and wrap the Gemini call:

```python
import json
import logging
import re
import google.genai as genai
from django.conf import settings
from core.brand_dna.models import BrandDNA
from core.shared.metrics_utils import track_external_api, record_tokens

logger = logging.getLogger(__name__)
```

Replace the `generate` method body (lines 39-57):

```python
    def generate(self, brand_dna: BrandDNA) -> list[dict]:
        client = _vertex_client()
        prompt = _PROMPT.format(
            business_name=brand_dna.business_name,
            description=brand_dna.description,
            audience=brand_dna.audience,
            tone=brand_dna.tone,
            keywords=', '.join(brand_dna.keywords or []),
            posting_style=brand_dna.posting_style or 'No disponible',
            hashtags=', '.join(brand_dna.common_hashtags or []),
            avg_length=brand_dna.avg_caption_length,
        )
        with track_external_api('gemini'):
            resp = client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, contents=prompt)
        record_tokens(resp)
        raw = resp.text.strip()
        raw = re.sub(r'^```(?:json)?\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw)
        raw = raw.strip()
        posts = json.loads(raw)
        return posts[:7]
```

- [ ] **Step 2: Instrument ImageGenerator — _analyze_product_style**

In `core/content_pipeline/generators/image_generator.py`, add import at top (after existing imports):

```python
from core.shared.metrics import IMAGEN_GENERATIONS, GCS_OPERATIONS, IMAGE_GENERATION_DURATION
from core.shared.metrics_utils import track_external_api, record_tokens
```

In `_analyze_product_style` (line 196-200), wrap the Gemini call:

Replace:
```python
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL,
                contents=[image_part, prompt],
            )
            result = resp.text.strip().strip('"').strip("'")
```

With:
```python
            with track_external_api('gemini'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[image_part, prompt],
                )
            record_tokens(resp)
            result = resp.text.strip().strip('"').strip("'")
```

- [ ] **Step 3: Instrument ImageGenerator — _bgswap_product**

In `_bgswap_product` (line 215-238), wrap the Imagen 3 call:

Replace:
```python
            resp = client.models.edit_image(
                model=settings.VERTEX_IMAGE_EDIT_MODEL,
                prompt=environment_prompt,
                reference_images=[
```

Through:
```python
            if resp.generated_images:
                logger.info("BGSWAP exitoso — producto sobre entorno premium")
                return resp.generated_images[0].image.image_bytes, True
```

With:
```python
            with track_external_api('imagen3'):
                resp = client.models.edit_image(
                    model=settings.VERTEX_IMAGE_EDIT_MODEL,
                    prompt=environment_prompt,
                    reference_images=[
                        types.RawReferenceImage(
                            reference_image=types.Image(image_bytes=product_image_bytes, mime_type=mime),
                            reference_id=1,
                        ),
                        types.MaskReferenceImage(
                            reference_id=2,
                            config=types.MaskReferenceConfig(
                                mask_mode=types.MaskReferenceMode.MASK_MODE_BACKGROUND,
                            ),
                        ),
                    ],
                    config=types.EditImageConfig(
                        edit_mode=types.EditMode.EDIT_MODE_BGSWAP,
                        number_of_images=1,
                        aspect_ratio='1:1',
                    ),
                )
            if resp.generated_images:
                IMAGEN_GENERATIONS.inc()
                logger.info("BGSWAP exitoso — producto sobre entorno premium")
                return resp.generated_images[0].image.image_bytes, True
```

- [ ] **Step 4: Instrument ImageGenerator — _generate_svg_overlay**

In `_generate_svg_overlay` (line 262-266), wrap the Gemini call:

Replace:
```python
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL,
                contents=[image_part, prompt],
            )
            raw = resp.text.strip()
```

With:
```python
            with track_external_api('gemini'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[image_part, prompt],
                )
            record_tokens(resp)
            raw = resp.text.strip()
```

- [ ] **Step 5: Instrument ImageGenerator — _analyze_brand_scene**

In `_analyze_brand_scene` (line 313-319), wrap the Gemini call:

Replace:
```python
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL,
                contents=prompt,
            )
            result = resp.text.strip().strip('"').strip("'")
```

With:
```python
            with track_external_api('gemini'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=prompt,
                )
            record_tokens(resp)
            result = resp.text.strip().strip('"').strip("'")
```

- [ ] **Step 6: Instrument ImageGenerator — _validate_background**

In `_validate_background` (line 373-378), wrap the Gemini call:

Replace:
```python
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL,
                contents=[image_part, prompt],
            )
            raw = resp.text.strip()
```

With:
```python
            with track_external_api('gemini'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[image_part, prompt],
                )
            record_tokens(resp)
            raw = resp.text.strip()
```

- [ ] **Step 7: Instrument ImageGenerator — _validate_final_image**

In `_validate_final_image` (line 412-416), wrap the Gemini call:

Replace:
```python
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL,
                contents=[image_part, prompt],
            )
            raw = resp.text.strip()
```

With:
```python
            with track_external_api('gemini'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[image_part, prompt],
                )
            record_tokens(resp)
            raw = resp.text.strip()
```

- [ ] **Step 8: Instrument ImageGenerator — _generate_post_content**

In `_generate_post_content` (line 491-503), wrap the Gemini call:

Replace:
```python
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
```

With:
```python
            with track_external_api('gemini'):
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
            record_tokens(resp)
            raw = resp.text.strip()
```

- [ ] **Step 9: Instrument ImageGenerator — _generate_with_vertex**

In `_generate_with_vertex` (line 580-605), wrap both Imagen/Gemini image generation paths:

Replace the full method body:
```python
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
```

With:
```python
    def _generate_with_vertex(self, prompt: str) -> bytes:
        client = _vertex_client()
        model = settings.VERTEX_IMAGE_MODEL
        if 'imagen' in model:
            with track_external_api('imagen3'):
                resp = client.models.generate_images(
                    model=model,
                    prompt=prompt,
                    config=types.GenerateImagesConfig(
                        number_of_images=1,
                        aspect_ratio='1:1',
                    ),
                )
            if resp.generated_images:
                IMAGEN_GENERATIONS.inc()
                return resp.generated_images[0].image.image_bytes
            raise ValueError("No image returned by Imagen")
        with track_external_api('gemini'):
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=['IMAGE', 'TEXT']
                ),
            )
        record_tokens(resp)
        for part in resp.candidates[0].content.parts:
            if part.inline_data:
                return part.inline_data.data
        raise ValueError("No image returned by Vertex AI")
```

- [ ] **Step 10: Instrument ImageGenerator — _upload_to_storage**

In `_upload_to_storage` (line 607-613):

Replace:
```python
    def _upload_to_storage(self, image_bytes: bytes, filename: str) -> str:
        client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
        bucket = client.bucket(self._bucket)
        blob = bucket.blob(f'posts/{filename}.png')
        blob.upload_from_string(image_bytes, content_type='image/png')
        blob.make_public()
        return blob.public_url
```

With:
```python
    def _upload_to_storage(self, image_bytes: bytes, filename: str) -> str:
        with track_external_api('gcs'):
            client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
            bucket = client.bucket(self._bucket)
            blob = bucket.blob(f'posts/{filename}.png')
            blob.upload_from_string(image_bytes, content_type='image/png')
            blob.make_public()
        GCS_OPERATIONS.labels(operation='upload').inc()
        return blob.public_url
```

- [ ] **Step 11: Instrument WebScraper**

In `core/brand_dna/extractors/web_scraper.py`, add import:

```python
from core.shared.metrics_utils import track_external_api, record_tokens
```

In `_analyze_with_vertex` (line 114-125), wrap the Gemini call:

Replace:
```python
    def _analyze_with_vertex(self, text: str, css_colors: list[str]) -> dict:
        client = _vertex_client()
        colors_str = ', '.join(css_colors) if css_colors else 'No se detectaron colores'
        prompt = _PROMPT_TEMPLATE.format(html=text, css_colors=colors_str)
        resp = client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, contents=prompt)
        raw = resp.text.strip()
```

With:
```python
    def _analyze_with_vertex(self, text: str, css_colors: list[str]) -> dict:
        client = _vertex_client()
        colors_str = ', '.join(css_colors) if css_colors else 'No se detectaron colores'
        prompt = _PROMPT_TEMPLATE.format(html=text, css_colors=colors_str)
        with track_external_api('gemini'):
            resp = client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, contents=prompt)
        record_tokens(resp)
        raw = resp.text.strip()
```

- [ ] **Step 12: Instrument LogoAnalyzer**

In `core/brand_dna/extractors/logo_analyzer.py`, add import:

```python
from core.shared.metrics_utils import track_external_api, record_tokens
```

In `_extract_colors` (line 36-47), wrap the Vision API call:

Replace:
```python
    def _extract_colors(self, image_bytes: bytes) -> list[str]:
        client = vision.ImageAnnotatorClient()
        image = vision.Image(content=image_bytes)
        features = [vision.Feature(type_=vision.Feature.Type.IMAGE_PROPERTIES)]
        request = vision.AnnotateImageRequest(image=image, features=features)
        response = client.annotate_image(request=request)
```

With:
```python
    def _extract_colors(self, image_bytes: bytes) -> list[str]:
        client = vision.ImageAnnotatorClient()
        image = vision.Image(content=image_bytes)
        features = [vision.Feature(type_=vision.Feature.Type.IMAGE_PROPERTIES)]
        request = vision.AnnotateImageRequest(image=image, features=features)
        with track_external_api('cloud_vision'):
            response = client.annotate_image(request=request)
```

In `_describe_with_vertex` (line 49-56), wrap the Gemini call:

Replace:
```python
    def _describe_with_vertex(self, image_bytes: bytes, mime_type: str) -> str:
        client = _vertex_client()
        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        resp = client.models.generate_content(
            model=settings.VERTEX_TEXT_MODEL,
            contents=[_VISION_PROMPT, image_part],
        )
        return resp.text.strip()
```

With:
```python
    def _describe_with_vertex(self, image_bytes: bytes, mime_type: str) -> str:
        client = _vertex_client()
        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        with track_external_api('gemini'):
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL,
                contents=[_VISION_PROMPT, image_part],
            )
        record_tokens(resp)
        return resp.text.strip()
```

- [ ] **Step 13: Instrument PostsAnalyzer**

In `core/brand_dna/extractors/posts_analyzer.py`, add import:

```python
from core.shared.metrics_utils import track_external_api, record_tokens
```

In `_analyze_text` (line 70-78), wrap the Gemini call:

Replace:
```python
    def _analyze_text(self, text: str) -> dict:
        client = _vertex_client()
        prompt = _TEXT_PROMPT.format(posts=text[:3000])
        resp = client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, contents=prompt)
        raw = resp.text.strip()
```

With:
```python
    def _analyze_text(self, text: str) -> dict:
        client = _vertex_client()
        prompt = _TEXT_PROMPT.format(posts=text[:3000])
        with track_external_api('gemini'):
            resp = client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, contents=prompt)
        record_tokens(resp)
        raw = resp.text.strip()
```

In `_analyze_images` (line 80-90), wrap the Gemini call:

Replace:
```python
    def _analyze_images(self, images: list[bytes]) -> dict:
        client = _vertex_client()
        parts = [_IMAGE_PROMPT]
        for img_bytes in images[:5]:
            parts.append(types.Part.from_bytes(data=img_bytes, mime_type='image/jpeg'))
        resp = client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, contents=parts)
        raw = resp.text.strip()
```

With:
```python
    def _analyze_images(self, images: list[bytes]) -> dict:
        client = _vertex_client()
        parts = [_IMAGE_PROMPT]
        for img_bytes in images[:5]:
            parts.append(types.Part.from_bytes(data=img_bytes, mime_type='image/jpeg'))
        with track_external_api('gemini'):
            resp = client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, contents=parts)
        record_tokens(resp)
        raw = resp.text.strip()
```

- [ ] **Step 14: Run existing tests to verify no regressions**

Run: `python -m pytest core/content_pipeline/tests/test_text_generator.py core/content_pipeline/tests/test_image_generator.py -v --ds=saas_chatbot.settings`

Expected: existing tests PASS (mocks still work — the `track_external_api` context manager is transparent)

- [ ] **Step 15: Commit**

```bash
git add core/content_pipeline/generators/text_generator.py core/content_pipeline/generators/image_generator.py core/brand_dna/extractors/web_scraper.py core/brand_dna/extractors/logo_analyzer.py core/brand_dna/extractors/posts_analyzer.py
GIT_EDITOR=true git commit -m "feat(observability): instrument extractors and generators with Prometheus metrics"
```

---

### Task 3: Instrument pipeline tasks — duration and job counters

**Files:**
- Modify: `core/brand_dna/tasks.py:1-72`
- Modify: `core/content_pipeline/tasks.py:47-117`

**Interfaces:**
- Consumes from Task 1:
  - `from core.shared.metrics import ANALYSIS_JOBS_TOTAL, ANALYSIS_DURATION, CONTENT_GENERATION_DURATION, CALENDARS_CREATED`
- Produces: Pipeline duration recorded in histograms, job completion/failure tracked by counters

- [ ] **Step 1: Instrument analyze_brand_task**

In `core/brand_dna/tasks.py`, add imports:

```python
import logging
import os
import time
import django_rq
from django.conf import settings
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.brand_dna.extractors.web_scraper import WebScraper
from core.brand_dna.extractors.logo_analyzer import LogoAnalyzer
from core.brand_dna.extractors.posts_analyzer import PostsAnalyzer
from core.content_pipeline.image_utils import normalize_image
from core.shared.metrics import ANALYSIS_JOBS_TOTAL, ANALYSIS_DURATION

logger = logging.getLogger(__name__)
```

Wrap the `analyze_brand_task` function body with timing:

```python
def analyze_brand_task(job_id: str) -> None:
    job = AnalysisJob.objects.get(id=job_id)
    job.status = AnalysisJob.STATUS_PROCESSING
    job.save(update_fields=['status'])

    start = time.monotonic()
    try:
        job.update_progress(AnalysisJob.STAGE_WEB, 10)
        scraper = WebScraper()
        web_data = scraper.extract(job.business_url)
        job.update_progress(AnalysisJob.STAGE_WEB, 30)

        job.update_progress(AnalysisJob.STAGE_LOGO, 35)
        logo_data = {'primary_colors': [], 'logo_elements': ''}
        if job.logo_file_path:
            logo_path = os.path.join(settings.MEDIA_ROOT, job.logo_file_path)
            if os.path.exists(logo_path):
                with open(logo_path, 'rb') as f:
                    logo_bytes = normalize_image(f.read())
                analyzer = LogoAnalyzer()
                logo_data = analyzer.analyze(logo_bytes, 'image/webp')
        job.update_progress(AnalysisJob.STAGE_LOGO, 55)

        job.update_progress(AnalysisJob.STAGE_POSTS, 58)
        posts_images = []
        for img_path in (job.post_images_paths or []):
            full_path = os.path.join(settings.MEDIA_ROOT, img_path)
            if os.path.exists(full_path):
                with open(full_path, 'rb') as f:
                    posts_images.append(normalize_image(f.read()))
        posts_analyzer = PostsAnalyzer()
        posts_data = posts_analyzer.analyze(
            images=posts_images if posts_images else None,
            text=job.posts_text if job.posts_text else None,
            profile_url=job.profile_url if job.profile_url else None,
        )
        job.update_progress(AnalysisJob.STAGE_POSTS, 75)

        BrandDNA.objects.create(
            job=job,
            business_name=web_data.get('business_name', 'Mi Negocio'),
            business_url=job.business_url,
            description=web_data.get('description', ''),
            keywords=web_data.get('keywords', []),
            audience=web_data.get('audience', ''),
            tone=web_data.get('tone', 'profesional'),
            primary_colors=logo_data.get('primary_colors') or web_data.get('brand_colors', []),
            logo_elements=logo_data.get('logo_elements', ''),
            posting_style=posts_data.get('posting_style', ''),
            avg_caption_length=posts_data.get('avg_caption_length', 150),
            common_hashtags=posts_data.get('common_hashtags', []),
        )
        job.update_progress(AnalysisJob.STAGE_CONTENT, 78)

        ANALYSIS_DURATION.observe(time.monotonic() - start)
        ANALYSIS_JOBS_TOTAL.labels(status='completed').inc()

        from core.content_pipeline.tasks import content_generation_task
        django_rq.enqueue(content_generation_task, str(job_id))

    except Exception as e:
        ANALYSIS_DURATION.observe(time.monotonic() - start)
        ANALYSIS_JOBS_TOTAL.labels(status='failed').inc()
        logger.error(f"analyze_brand_task error para job {job_id}: {e}")
        job.mark_failed(str(e))
```

- [ ] **Step 2: Instrument content_generation_task**

In `core/content_pipeline/tasks.py`, add imports:

```python
import logging
import os
import time
from datetime import datetime as dt_datetime, timedelta, timezone as dt_timezone
from django.conf import settings
from django.utils import timezone
from core.shared.metrics import CONTENT_GENERATION_DURATION, CALENDARS_CREATED
```

(Keep all other existing imports unchanged.)

Wrap `content_generation_task` body with timing:

```python
def content_generation_task(job_id: str) -> None:
    job = AnalysisJob.objects.get(id=job_id)
    brand_dna = job.brand_dna

    start = time.monotonic()
    try:
        job.update_progress(AnalysisJob.STAGE_CONTENT, 80)

        text_gen = TextGenerator()
        posts_data = text_gen.generate(brand_dna)
        job.update_progress(AnalysisJob.STAGE_CONTENT, 87)

        calendar = ContentCalendar.objects.create(
            brand_dna=brand_dna,
            active_product_images=job.product_image_paths[:7],
        )
        CALENDARS_CREATED.inc()
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        now = timezone.now()
        mexico_today = now.astimezone(MEXICO_TZ).date()

        scheduled_dates = smart_schedule_dates(brand_dna, base_date=mexico_today, count=len(posts_data))

        product_images_bytes = _load_product_images(calendar.active_product_images)

        for i, post_data in enumerate(posts_data, start=1):
            hour, minute = map(int, post_data.get('suggested_time', '19:00').split(':'))
            scheduled = scheduled_dates[i - 1]

            day_product = _product_image_for_day(i, product_images_bytes)
            if i == 1:
                image_url = image_gen.generate(
                    caption=post_data['caption'],
                    colors=brand_dna.primary_colors,
                    tone=brand_dna.tone,
                    filename=f"{job_id}-day{i}",
                    brand_name=brand_dna.business_name,
                    keywords=brand_dna.keywords,
                    description=brand_dna.description,
                    product_image_bytes=day_product,
                )
            else:
                image_url = ''

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

        try:
            EmailSender().send_initial(job=job, brand_dna=brand_dna, calendar=calendar)
            schedule_daily_emails(calendar)
        except Exception as email_err:
            logger.error(f"Email falló para job {job_id} (no fatal): {email_err}")

        job.stage = AnalysisJob.STAGE_COMPLETE
        job.progress = 100
        job.status = AnalysisJob.STATUS_DONE
        job.save(update_fields=['stage', 'progress', 'status'])
        CONTENT_GENERATION_DURATION.observe(time.monotonic() - start)
        logger.info(f"Job {job_id} completado exitosamente")

    except Exception as e:
        CONTENT_GENERATION_DURATION.observe(time.monotonic() - start)
        logger.error(f"content_generation_task error para job {job_id}: {e}")
        job.mark_failed(str(e))
```

- [ ] **Step 3: Run existing task tests**

Run: `python -m pytest core/content_pipeline/tests/test_tasks.py -v --ds=saas_chatbot.settings`

Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add core/brand_dna/tasks.py core/content_pipeline/tasks.py
GIT_EDITOR=true git commit -m "feat(observability): instrument pipeline tasks with duration histograms and job counters"
```

---

### Task 4: Instrument auth views, email sending, and content actions

**Files:**
- Modify: `core/brand_dna/auth_views.py:1-416`
- Modify: `core/brand_dna/views.py:1-411`
- Modify: `core/content_pipeline/email_sender.py:1-53`

**Interfaces:**
- Consumes from Task 1:
  - `from core.shared.metrics import LOGIN_ATTEMPTS, REGISTRATIONS, EMAIL_VERIFICATIONS, INVITATION_CODES_REDEEMED, EMAILS_SENT, POST_ACTIONS`
- Produces: All auth/business events tracked via counters

- [ ] **Step 1: Instrument auth_views.py — login**

In `core/brand_dna/auth_views.py`, add import:

```python
from core.shared.metrics import (
    LOGIN_ATTEMPTS, REGISTRATIONS, EMAIL_VERIFICATIONS,
    INVITATION_CODES_REDEEMED, EMAILS_SENT,
)
```

In `login_view`, add counter increments at the three outcomes:

After `cache.delete(cache_key)` and before `login(request, user)` (successful login):
```python
                    cache.delete(cache_key)
                    LOGIN_ATTEMPTS.labels(result='success').inc()
                    login(request, user)
```

After `cache.set(cache_key, attempts + 1, _LOGIN_LOCKOUT_SECONDS)` (failed login):
```python
                cache.set(cache_key, attempts + 1, _LOGIN_LOCKOUT_SECONDS)
                LOGIN_ATTEMPTS.labels(result='failed').inc()
                error = 'Correo o contraseña incorrectos.'
```

After `error = 'Demasiados intentos...'` (locked out):
```python
            if attempts >= _LOGIN_MAX_ATTEMPTS:
                LOGIN_ATTEMPTS.labels(result='locked').inc()
                error = 'Demasiados intentos. Intenta de nuevo en 5 minutos.'
```

- [ ] **Step 2: Instrument auth_views.py — register**

In `register_view`, after sending the verification email (after `fail_silently=False,`):

```python
            REGISTRATIONS.labels(method='email').inc()
            EMAILS_SENT.labels(type='verification').inc()
```

- [ ] **Step 3: Instrument auth_views.py — verify_email**

In `verify_email_view`, after `verification.save(update_fields=['is_used'])`:

```python
    EMAIL_VERIFICATIONS.labels(result='completed').inc()
```

After the `not verification.is_valid()` check (early return for expired):

```python
    if not verification.is_valid():
        EMAIL_VERIFICATIONS.labels(result='expired').inc()
        return redirect('login')
```

- [ ] **Step 4: Instrument auth_views.py — apply_code**

In `apply_code_view`, after `if code_obj.redeem(request.user):`:

```python
        if code_obj.redeem(request.user):
            INVITATION_CODES_REDEEMED.inc()
            logger.info(f"Codigo {code_str} aplicado por {request.user.email}")
```

- [ ] **Step 5: Instrument auth_views.py — google_callback**

In `google_callback_view`, after the `if created:` block (after `notify_admin_new_user(user)`):

```python
        notify_admin_new_user(user)
        REGISTRATIONS.labels(method='google_oauth').inc()
```

- [ ] **Step 6: Instrument auth_views.py — forgot_password and reset_password**

In `forgot_password_view`, after `AuthService.initiate_password_reset(email)`:

```python
                AuthService.initiate_password_reset(email)
                EMAILS_SENT.labels(type='password_reset').inc()
```

In `reset_password_view`, after the successful reset email send_mail:

```python
                    EMAILS_SENT.labels(type='password_reset_confirm').inc()
```

- [ ] **Step 7: Instrument views.py — post_action_api**

In `core/brand_dna/views.py`, add import:

```python
from core.shared.metrics import POST_ACTIONS
```

In `post_action_api`, add increments for each action:

After `if action == 'approve':` and `post.save(...)`:
```python
        POST_ACTIONS.labels(action='approved').inc()
```

After `if action == 'edit':` and `post.save(...)`:
```python
        POST_ACTIONS.labels(action='edited').inc()
```

After `if action == 'regenerate':` and after the regeneration logic completes (before `return JsonResponse`):
```python
        POST_ACTIONS.labels(action='regenerated').inc()
```

- [ ] **Step 8: Instrument email_sender.py**

In `core/content_pipeline/email_sender.py`, add import:

```python
from core.shared.metrics import EMAILS_SENT
```

In `send_initial`, after `send_mail(...)`:

```python
        EMAILS_SENT.labels(type='initial_calendar').inc()
```

In `send_daily`, after `post.save(update_fields=['status', 'sent_at'])`:

```python
        EMAILS_SENT.labels(type='daily_post').inc()
```

- [ ] **Step 9: Run tests**

Run: `python -m pytest core/brand_dna/tests/ core/tenant_management/tests/test_auth_security.py -v --ds=saas_chatbot.settings`

Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add core/brand_dna/auth_views.py core/brand_dna/views.py core/content_pipeline/email_sender.py
GIT_EDITOR=true git commit -m "feat(observability): instrument auth views, email sending, and content actions"
```

---

### Task 5: Custom Prometheus collectors — RQ jobs and active users

**Files:**
- Modify: `core/shared/metrics.py` (add collectors)
- Create: `core/shared/tests/test_collectors.py`

**Interfaces:**
- Consumes from Task 1:
  - `core/shared/metrics.py` already defines `RQ_JOBS` (Gauge) and `ACTIVE_USERS` (Gauge)
- Produces: Collectors that update gauges on each Prometheus scrape

- [ ] **Step 1: Write test for RQ collector**

Create `core/shared/tests/test_collectors.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from core.shared.metrics import RQJobsCollector, ActiveUsersCollector


class TestRQJobsCollector:
    def test_collect_returns_gauge_family(self):
        collector = RQJobsCollector()
        mock_queue = MagicMock()
        mock_queue.count = 5
        mock_queue.started_job_registry.count = 2
        mock_queue.finished_job_registry.count = 10
        mock_queue.failed_job_registry.count = 1

        with patch('django_rq.get_queue', return_value=mock_queue):
            metrics = list(collector.collect())

        assert len(metrics) == 1
        metric = metrics[0]
        assert metric.name == 'cosmic_rq_jobs'
        samples = {s.labels['state']: s.value for s in metric.samples}
        assert samples['queued'] == 5
        assert samples['started'] == 2
        assert samples['finished'] == 10
        assert samples['failed'] == 1

    def test_collect_handles_error(self):
        collector = RQJobsCollector()
        with patch('django_rq.get_queue', side_effect=Exception('redis down')):
            metrics = list(collector.collect())
        assert metrics == []


@pytest.mark.django_db
class TestActiveUsersCollector:
    def test_collect_returns_gauge_family(self):
        collector = ActiveUsersCollector()
        with patch('core.shared.metrics.User') as MockUser:
            MockUser.objects.filter.return_value.count.return_value = 7
            metrics = list(collector.collect())

        assert len(metrics) == 1
        assert metrics[0].samples[0].value == 7

    def test_collect_handles_error(self):
        collector = ActiveUsersCollector()
        with patch('core.shared.metrics.User') as MockUser:
            MockUser.objects.filter.side_effect = Exception('db error')
            metrics = list(collector.collect())
        assert metrics == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest core/shared/tests/test_collectors.py -v --ds=saas_chatbot.settings`

Expected: FAIL — `RQJobsCollector` and `ActiveUsersCollector` not defined yet

- [ ] **Step 3: Add collectors to core/shared/metrics.py**

Append to the end of `core/shared/metrics.py`:

```python
import logging
from datetime import timedelta
from prometheus_client.core import GaugeMetricFamily, REGISTRY

logger = logging.getLogger(__name__)


class RQJobsCollector:
    def describe(self):
        return []

    def collect(self):
        try:
            import django_rq
            queue = django_rq.get_queue('default')
            g = GaugeMetricFamily('cosmic_rq_jobs', 'RQ jobs by state', labels=['state'])
            g.add_metric(['queued'], queue.count)
            g.add_metric(['started'], queue.started_job_registry.count)
            g.add_metric(['finished'], queue.finished_job_registry.count)
            g.add_metric(['failed'], queue.failed_job_registry.count)
            yield g
        except Exception as e:
            logger.warning(f'RQJobsCollector error: {e}')


class ActiveUsersCollector:
    def describe(self):
        return []

    def collect(self):
        try:
            from django.contrib.auth import get_user_model
            from django.utils import timezone
            global User
            User = get_user_model()
            cutoff = timezone.now() - timedelta(hours=24)
            count = User.objects.filter(last_login__gte=cutoff).count()
            g = GaugeMetricFamily('cosmic_active_users', 'Users with login in last 24h')
            g.add_metric([], count)
            yield g
        except Exception as e:
            logger.warning(f'ActiveUsersCollector error: {e}')


REGISTRY.register(RQJobsCollector())
REGISTRY.register(ActiveUsersCollector())
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest core/shared/tests/test_collectors.py -v --ds=saas_chatbot.settings`

Expected: all tests PASS

- [ ] **Step 5: Run full metrics test suite**

Run: `python -m pytest core/shared/tests/test_metrics.py core/shared/tests/test_metrics_endpoint.py core/shared/tests/test_collectors.py -v --ds=saas_chatbot.settings`

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add core/shared/metrics.py core/shared/tests/test_collectors.py
GIT_EDITOR=true git commit -m "feat(observability): custom Prometheus collectors for RQ jobs and active users"
```
