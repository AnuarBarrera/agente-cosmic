# Observability — Application Metrics for Agente Cosmic

## Goal

Instrument agente-cosmic with Prometheus metrics covering operational health and business intelligence, exposed via a dedicated port for scraping by the existing Prometheus instance on GCP-Monitor over VPC internal network.

## Architecture

### Stack

- **django-prometheus** — automatic instrumentation of HTTP requests, PostgreSQL queries, Redis cache operations
- **prometheus_client** — custom application-level metrics (counters, histograms, gauges)
- **Prometheus (GCP-Monitor)** — scrapes metrics via VPC internal IP on port 9091
- **Grafana (GCP-Monitor)** — queries Prometheus for visualization (dashboard creation out of scope)

### Data flow

```
Django app (gunicorn :8000)
  ├── nginx (:80) ──> public traffic (blocks /metrics → 404)
  └── docker port 0.0.0.0:9091 ──> Prometheus (GCP-Monitor, VPC internal)
                                         │
                                    Grafana ← PromQL queries
```

### Exposure

- Docker-compose exposes `0.0.0.0:9091:8000` — NOT bound to localhost so the VPC network can reach it
- GCP firewall rule restricts TCP 9091 to only the IP of GCP-Monitor
- nginx blocks `/metrics` with `return 404` to prevent public access via the main domain

## Metrics

### Operational health

| Metric | Type | Labels | Source |
|--------|------|--------|--------|
| `cosmic_analysis_jobs_total` | Counter | `status` (completed, failed) | `core/brand_dna/tasks.py` |
| `cosmic_analysis_duration_seconds` | Histogram | — | `core/brand_dna/tasks.py` |
| `cosmic_content_generation_duration_seconds` | Histogram | — | `core/content_pipeline/tasks.py` |
| `cosmic_image_generation_duration_seconds` | Histogram | — | `core/content_pipeline/generators/image_generator.py` |
| `cosmic_rq_jobs` | Gauge | `state` (queued, started, finished, failed) | `core/shared/metrics.py` (collector) |
| `cosmic_login_attempts_total` | Counter | `result` (success, failed, locked) | `core/brand_dna/auth_views.py` |

### External API health

| Metric | Type | Labels | Source |
|--------|------|--------|--------|
| `cosmic_external_api_requests_total` | Counter | `service`, `status` | Extractors, generators |
| `cosmic_external_api_duration_seconds` | Histogram | `service` | Extractors, generators |
| `cosmic_external_api_errors_total` | Counter | `service`, `error_type` | Extractors, generators |

Services tracked: `gemini`, `imagen3`, `mailgun`, `gcs`.

### GCP resource usage

| Metric | Type | Labels | Source |
|--------|------|--------|--------|
| `cosmic_gemini_tokens_total` | Counter | `direction` (input, output) | `TextGenerator`, extractors |
| `cosmic_imagen_generations_total` | Counter | — | `ImageGenerator` |
| `cosmic_gcs_storage_operations_total` | Counter | `operation` (upload, download) | `ImageGenerator` |

### Onboarding

| Metric | Type | Labels | Source |
|--------|------|--------|--------|
| `cosmic_registrations_total` | Counter | `method` (email, google_oauth) | `auth_views.py` |
| `cosmic_email_verifications_total` | Counter | `result` (completed, expired) | `auth_views.py` |
| `cosmic_invitation_codes_redeemed_total` | Counter | — | `auth_views.py` |
| `cosmic_emails_sent_total` | Counter | `type` (verification, daily_post, password_reset, weekly_feedback) | `email_sender.py`, `auth_views.py` |

### Content feedback

| Metric | Type | Labels | Source |
|--------|------|--------|--------|
| `cosmic_post_actions_total` | Counter | `action` (approved, rejected, regenerated) | `views.py` |
| `cosmic_active_users` | Gauge | — | `core/shared/metrics.py` (collector) |
| `cosmic_calendars_created_total` | Counter | — | `views.py` |

## Instrumentation points

### Module: `core/shared/metrics.py`

Central module that defines all Prometheus metric objects. Imported wherever metrics are emitted. Also contains:

- **RQ collector**: a custom Prometheus collector that queries the Redis-backed RQ queue on each scrape to report `cosmic_rq_jobs` gauge by state.
- **Active users collector**: queries `User.objects.filter(last_login__gte=now-24h).count()` on each scrape.

### Module: `core/shared/metrics_middleware.py`

Thin middleware that wraps external API calls. Provides a context manager / decorator `track_external_api(service_name)` that:
1. Starts a timer
2. Catches exceptions and classifies error type (timeout, http_4xx, http_5xx, connection_error)
3. Records `cosmic_external_api_requests_total`, `cosmic_external_api_duration_seconds`, and `cosmic_external_api_errors_total`

### Changes to existing files

**`saas_chatbot/settings.py`:**
- Add `django_prometheus` to `INSTALLED_APPS`
- Add `django_prometheus.middleware.PrometheusBeforeMiddleware` as first middleware
- Add `django_prometheus.middleware.PrometheusAfterMiddleware` as last middleware
- Change `DATABASES` engine to `django_prometheus.db.backends.postgresql`
- Change `CACHES` backend to `django_prometheus.cache.backends.redis.RedisCache`

**`saas_chatbot/urls.py`:**
- Add `path('', include('django_prometheus.urls'))` for the `/metrics` endpoint

**`docker-compose.yml`:**
- Add port `0.0.0.0:9091:8000` to backend service

**`nginx.dev.conf`:**
- Add `location /metrics { return 404; }` block before the catch-all

**`requirements.txt`:**
- Add `django-prometheus>=2.3.1`

**`core/brand_dna/tasks.py`:**
- Import metrics, wrap `analyze_brand_task` with duration histogram and job counter

**`core/content_pipeline/tasks.py`:**
- Import metrics, wrap `content_generation_task` with duration histogram

**`core/content_pipeline/generators/image_generator.py`:**
- Import `track_external_api`, wrap Imagen 3 calls
- Increment `cosmic_imagen_generations_total`

**`core/content_pipeline/generators/text_generator.py`:**
- Import `track_external_api`, wrap Gemini calls
- Record token usage from response metadata

**`core/brand_dna/extractors/web_scraper.py`:**
- Import `track_external_api`, wrap Gemini calls

**`core/brand_dna/extractors/posts_analyzer.py`:**
- Import `track_external_api`, wrap Gemini calls

**`core/brand_dna/extractors/logo_analyzer.py`:**
- Import `track_external_api`, wrap Gemini calls

**`core/brand_dna/auth_views.py`:**
- Import metrics, increment registration/verification/login counters

**`core/content_pipeline/email_sender.py`:**
- Import metrics, increment `cosmic_emails_sent_total` by type

**`core/brand_dna/views.py`:**
- Import metrics, increment post action and calendar counters

## Testing

- **`tests/test_metrics.py`**: Unit tests for `core/shared/metrics.py` — verify metric objects are created, counters increment, histograms observe, collectors return expected format.
- **`tests/test_metrics_endpoint.py`**: Integration test — HTTP GET to `/metrics` returns 200 with `text/plain` content containing expected metric names.
- **No tests for django-prometheus internals** — that is the library's responsibility.

## Out of scope

- Grafana dashboard JSON provisioning
- Alerting rules (Alertmanager configuration)
- Tracing (OpenTelemetry spans)
- Prometheus server configuration changes (manual task on GCP-Monitor)
- GCP firewall rule creation (manual infrastructure task)
