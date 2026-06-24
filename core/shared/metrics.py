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
