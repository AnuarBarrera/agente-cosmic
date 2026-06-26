import logging
from datetime import timedelta

from prometheus_client import Counter, Histogram
from prometheus_client.core import GaugeMetricFamily, CounterMetricFamily, REGISTRY

# ---------------------------------------------------------------------------
# Pipeline de análisis
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Generación de contenido
# ---------------------------------------------------------------------------
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

CALENDARS_CREATED = Counter(
    'cosmic_calendars_created_total',
    'Content calendars created',
)

POST_ACTIONS = Counter(
    'cosmic_post_actions_total',
    'Content post actions',
    ['action'],
)

# ---------------------------------------------------------------------------
# Autenticación y usuarios
# ---------------------------------------------------------------------------
LOGIN_ATTEMPTS = Counter(
    'cosmic_login_attempts_total',
    'Login attempts',
    ['result'],
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

# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------
EMAILS_SENT = Counter(
    'cosmic_emails_sent_total',
    'Emails sent',
    ['type'],
)

# ---------------------------------------------------------------------------
# APIs externas — latencia, errores, éxito (por proceso — Gunicorn workers)
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# GCS
# ---------------------------------------------------------------------------
GCS_OPERATIONS = Counter(
    'cosmic_gcs_storage_operations_total',
    'Google Cloud Storage operations',
    ['operation'],
)

# ---------------------------------------------------------------------------
# Métricas LLM / Imagen — fuente autoritativa: Redis (ver RedisMetricsCollector)
# Las claves Redis son actualizadas por todos los procesos (Gunicorn + rqworkers).
# NO se usan prometheus_client Counters para estos — se evita doble conteo.
# ---------------------------------------------------------------------------

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Colector Redis — agrega métricas de TODOS los procesos y contenedores
# ---------------------------------------------------------------------------

class RedisMetricsCollector:
    """Lee contadores acumulados en Redis (escritos por Gunicorn workers y rqworkers)."""

    def describe(self):
        return []

    def collect(self):
        try:
            import django_rq
            r = django_rq.get_connection('default')

            # --- Tokens Gemini por dirección y operación ---
            c_tokens = CounterMetricFamily(
                'cosmic_gemini_tokens_total',
                'Gemini tokens consumed — all processes',
                labels=['direction', 'operation'],
            )
            for raw_key in r.scan_iter('cosmic:prom:G:*'):
                key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
                parts = key.split(':')
                if len(parts) == 5:
                    direction, op = parts[3], parts[4]
                    val = float(r.get(raw_key) or 0)
                    if val > 0:
                        c_tokens.add_metric([direction, op], val)
            yield c_tokens

            # --- Costo estimado Gemini (microdólares) ---
            c_cost_g = CounterMetricFamily(
                'cosmic_gemini_cost_microdollars_total',
                'Estimated Gemini cost microdollars — all processes',
                labels=['operation'],
            )
            for raw_key in r.scan_iter('cosmic:prom:GC:*'):
                key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
                parts = key.split(':')
                if len(parts) == 4:
                    op = parts[3]
                    val = float(r.get(raw_key) or 0)
                    if val > 0:
                        c_cost_g.add_metric([op], val)
            yield c_cost_g

            # --- Generaciones Imagen 3 por tipo ---
            c_img = CounterMetricFamily(
                'cosmic_imagen_generations_by_type_total',
                'Imagen 3 images generated by type — all processes',
                labels=['type'],
            )
            for img_type in ('generate', 'bgswap', 'qc_retry'):
                raw_key = f'cosmic:prom:I:{img_type}'
                val = float(r.get(raw_key) or 0)
                if val > 0:
                    c_img.add_metric([img_type], val)
            yield c_img

            # --- Costo estimado Imagen 3 (microdólares) ---
            c_cost_i = CounterMetricFamily(
                'cosmic_imagen_cost_microdollars_total',
                'Estimated Imagen 3 cost microdollars — all processes',
                labels=['type'],
            )
            for img_type in ('generate', 'bgswap'):
                raw_key = f'cosmic:prom:IC:{img_type}'
                val = float(r.get(raw_key) or 0)
                if val > 0:
                    c_cost_i.add_metric([img_type], val)
            yield c_cost_i

            # --- Llamadas LLM por operación y resultado ---
            c_llm = CounterMetricFamily(
                'cosmic_llm_calls_total',
                'LLM API calls by operation and result — all processes',
                labels=['operation', 'result'],
            )
            for raw_key in r.scan_iter('cosmic:prom:L:*'):
                key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
                parts = key.split(':')
                if len(parts) == 5:
                    op, result = parts[3], parts[4]
                    val = float(r.get(raw_key) or 0)
                    if val > 0:
                        c_llm.add_metric([op, result], val)
            yield c_llm

        except Exception as e:
            _logger.warning('RedisMetricsCollector error: %s', e)


# ---------------------------------------------------------------------------
# Colectores de BD — scraped en cada pull de Prometheus
# ---------------------------------------------------------------------------

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
            g.add_metric(['scheduled'], queue.scheduled_job_registry.count)
            yield g
        except Exception as e:
            _logger.warning('RQJobsCollector error: %s', e)


class ActiveUsersCollector:
    def describe(self):
        return []

    def collect(self):
        try:
            from django.contrib.auth import get_user_model
            from django.utils import timezone
            User = get_user_model()
            cutoff = timezone.now() - timedelta(hours=24)
            count = User.objects.filter(last_login__gte=cutoff).count()
            g = GaugeMetricFamily('cosmic_active_users', 'Users with login in last 24h')
            g.add_metric([], count)
            yield g
        except Exception as e:
            _logger.warning('ActiveUsersCollector error: %s', e)


class OperationalCollector:
    """Métricas operacionales de BD — scraped en cada pull de Prometheus."""

    def describe(self):
        return []

    def collect(self):
        try:
            from django.contrib.auth import get_user_model
            from django.utils import timezone
            from core.content_pipeline.models import ContentCalendar, ContentPost
            from core.brand_dna.models import AnalysisJob

            User = get_user_model()
            now = timezone.now()

            g_users = GaugeMetricFamily('cosmic_total_users', 'Total registered users')
            g_users.add_metric([], User.objects.count())
            yield g_users

            g_cals = GaugeMetricFamily('cosmic_active_calendars', 'Active calendars (not deleted)')
            g_cals.add_metric([], ContentCalendar.objects.filter(
                brand_dna__job__deleted_at__isnull=True
            ).count())
            yield g_cals

            g_pending = GaugeMetricFamily('cosmic_pending_posts', 'Posts pending email delivery')
            g_pending.add_metric([], ContentPost.objects.filter(
                calendar__brand_dna__job__deleted_at__isnull=True,
                scheduled_at__gt=now,
            ).count())
            yield g_pending

            cutoff_24h = now - timedelta(hours=24)
            g_delivered = GaugeMetricFamily('cosmic_posts_delivered_24h', 'Posts delivered in last 24h')
            g_delivered.add_metric([], ContentPost.objects.filter(
                scheduled_at__gte=cutoff_24h,
                scheduled_at__lte=now,
                image_url__gt='',
            ).count())
            yield g_delivered

            g_failed = GaugeMetricFamily('cosmic_failed_analysis_jobs', 'Analysis jobs in failed state')
            g_failed.add_metric([], AnalysisJob.objects.filter(status='failed').count())
            yield g_failed

            g_no_img = GaugeMetricFamily('cosmic_posts_without_image', 'Posts with no generated image')
            g_no_img.add_metric([], ContentPost.objects.filter(
                image_url='',
                calendar__brand_dna__job__deleted_at__isnull=True,
            ).count())
            yield g_no_img

            g_per_user = GaugeMetricFamily(
                'cosmic_calendars_per_user', 'Active calendars per user', labels=['user_email']
            )
            for user in User.objects.filter(is_active=True):
                count = ContentCalendar.objects.filter(
                    brand_dna__job__user=user,
                    brand_dna__job__deleted_at__isnull=True,
                ).count()
                if count > 0:
                    g_per_user.add_metric([user.email], count)
            yield g_per_user

        except Exception as e:
            _logger.warning('OperationalCollector error: %s', e)


REGISTRY.register(RQJobsCollector())
REGISTRY.register(ActiveUsersCollector())
REGISTRY.register(OperationalCollector())
REGISTRY.register(RedisMetricsCollector())
