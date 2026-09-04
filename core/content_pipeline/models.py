import uuid
from django.db import models
from core.brand_dna.models import BrandDNA


class ContentCalendar(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brand_dna = models.OneToOneField(BrandDNA, on_delete=models.CASCADE, related_name='calendar')
    created_at = models.DateTimeField(auto_now_add=True)
    next_week_generating = models.BooleanField(default=False)
    last_reactivation_email_at = models.DateTimeField(null=True, blank=True)
    # Momento en que el usuario se llevo su primer contenido. Es la senal de
    # valor entregado: dispara el banner de venta anticipada, que antes salia
    # antes de que el usuario tocara nada.
    first_download_at = models.DateTimeField(null=True, blank=True)

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

    FORMAT_SINGLE = 'single'
    FORMAT_CAROUSEL = 'carousel'
    FORMAT_REEL = 'reel'
    FORMAT_CHOICES = [
        (FORMAT_SINGLE, 'Imagen única'),
        (FORMAT_CAROUSEL, 'Carrusel'),
        (FORMAT_REEL, 'Reel'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    calendar = models.ForeignKey(ContentCalendar, on_delete=models.CASCADE, related_name='posts')
    day_number = models.IntegerField()
    caption = models.TextField()
    # Línea comercial objetivo de esta pieza (p. ej. "medicina veterinaria" o
    # "salud humana"). Se conserva para que la generación visual asíncrona y
    # las regeneraciones mantengan la misma audiencia que el caption.
    commercial_line = models.CharField(max_length=255, blank=True, default='')
    image_url = models.URLField(max_length=1000)
    # Carrusel (H20 + roadmap #5): lista ordenada de URLs de slides, vacia para posts
    # normales. image_url sigue siendo la portada/slide 1 para retrocompatibilidad
    # (email, thumbnail del dashboard, endpoint de descarga por default).
    image_urls = models.JSONField(default=list, blank=True)
    # Reel (roadmap #7): URL del MP4 final. image_url guarda el poster frame
    # (segundo 1 del video) para retrocompatibilidad con email/thumbnail.
    video_url = models.URLField(max_length=1000, blank=True, default='')
    # Fondo limpio (foto real editada por nano banana, SIN overlay) -- solo se
    # llena para posts del camino de foto real (generate_from_product_photo/
    # regenerate_with_reference). Vacio para el resto, igual que image_urls
    # hoy solo se llena para carruseles. Se guarda aparte de image_url (que
    # SIEMPRE es la imagen final, con overlay si se pudo componer) para que
    # la regeneracion pueda editar el fondo limpio en vez de una imagen con
    # texto ya horneado encima.
    product_photo_background_url = models.URLField(max_length=1000, blank=True, default='')
    format = models.CharField(max_length=20, choices=FORMAT_CHOICES, default=FORMAT_SINGLE)
    suggested_time = models.TimeField()
    hashtags = models.JSONField(default=list)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    scheduled_at = models.DateTimeField()
    sent_at = models.DateTimeField(null=True, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    downloaded_at = models.DateTimeField(null=True, blank=True)

    USER_STATUS_PENDING = 'pending'
    USER_STATUS_APPROVED = 'approved'
    USER_STATUS_EDITED = 'edited'
    USER_STATUS_CHANGE_REQUESTED = 'change_requested'
    USER_STATUS_CHOICES = [
        ('pending', 'Pendiente revisión'),
        ('approved', 'Aprobado'),
        ('edited', 'Editado por usuario'),
        ('change_requested', 'Cambio solicitado'),
    ]
    user_status = models.CharField(
        max_length=20, choices=USER_STATUS_CHOICES, default='pending'
    )
    user_note = models.TextField(blank=True, default='')
    regen_count = models.PositiveIntegerField(default=0)
    edit_count = models.PositiveIntegerField(default=0)
    # True mientras regenerate_post_image_task corre en RQ (solo camino con foto
    # real de producto). El frontend hace polling de post_regen_status_api hasta
    # que vuelve a False.
    regenerating = models.BooleanField(default=False)

    class Meta:
        db_table = 'content_pipeline_post'
        ordering = ['day_number']

    def __str__(self):
        return f"Día {self.day_number} — {self.calendar.brand_dna.business_name}"


class GenerationAuditEvent(models.Model):
    DECISION_STARTED = 'started'
    DECISION_ACCEPTED = 'accepted'
    DECISION_REJECTED = 'rejected'
    DECISION_FALLBACK = 'fallback'
    DECISION_ERROR = 'error'
    DECISION_SKIPPED = 'skipped'
    DECISION_CHOICES = [
        (DECISION_STARTED, 'Iniciado'),
        (DECISION_ACCEPTED, 'Aceptado'),
        (DECISION_REJECTED, 'Rechazado'),
        (DECISION_FALLBACK, 'Fallback'),
        (DECISION_ERROR, 'Error'),
        (DECISION_SKIPPED, 'Omitido'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        'brand_dna.AnalysisJob', on_delete=models.CASCADE,
        related_name='generation_audit_events',
    )
    post = models.ForeignKey(
        ContentPost, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='generation_audit_events',
    )
    reference_asset = models.ForeignKey(
        'brand_dna.ProductReferenceAsset', on_delete=models.SET_NULL,
        null=True, blank=True, related_name='generation_audit_events',
    )
    stage = models.CharField(max_length=100)
    attempt = models.PositiveIntegerField(default=1)
    decision = models.CharField(max_length=20, choices=DECISION_CHOICES)
    flags = models.JSONField(default=dict, blank=True)
    prompt_hash = models.CharField(max_length=64, blank=True, default='')
    response_hash = models.CharField(max_length=64, blank=True, default='')
    prompt_preview = models.TextField(blank=True, default='')
    response_preview = models.TextField(blank=True, default='')
    duration_ms = models.PositiveIntegerField(null=True, blank=True)
    provider = models.CharField(max_length=50, blank=True, default='')
    model = models.CharField(max_length=150, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'content_pipeline_generation_audit_event'
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['job', 'stage', 'created_at'], name='audit_job_stage_idx'),
            models.Index(fields=['decision', 'created_at'], name='audit_decision_idx'),
        ]
