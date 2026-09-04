import uuid
from django.conf import settings
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
    MODE_FULL = 'full'
    MODE_SAMPLE_IMAGE = 'sample_image'
    MODE_SAMPLE_REEL = 'sample_reel'
    MODE_CHOICES = [
        (MODE_FULL, 'Calendario completo'),
        (MODE_SAMPLE_IMAGE, 'Muestra: imagen'),
        (MODE_SAMPLE_REEL, 'Muestra: reel'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField()
    business_url = models.URLField(blank=True, default='')
    business_description = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default=STAGE_WEB)
    progress = models.IntegerField(default=0)
    error_message = models.TextField(blank=True, default='')
    logo_file_path = models.CharField(max_length=500, blank=True, default='')
    product_reference_image_paths = models.JSONField(default=list, blank=True)
    post_images_paths = models.JSONField(default=list, blank=True)
    posts_text = models.TextField(blank=True, default='')
    profile_url = models.URLField(blank=True, default='')
    generation_mode = models.CharField(max_length=20, choices=MODE_CHOICES, default=MODE_FULL)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='analysis_jobs',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

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
    business_url = models.URLField(blank=True, default='')
    description = models.TextField()
    keywords = models.JSONField(default=list)
    audience = models.TextField()
    tone = models.CharField(max_length=50)
    primary_colors = models.JSONField(default=list)
    logo_url = models.URLField(blank=True, default='')
    logo_elements = models.TextField(blank=True, default='')
    posting_style = models.TextField(blank=True, default='')
    avg_caption_length = models.IntegerField(default=150)
    common_hashtags = models.JSONField(default=list)
    product_photo_analysis = models.TextField(blank=True, default='')
    product_category = models.CharField(max_length=100, blank=True, default='')
    # Perfil de hechos confirmados para validación de afirmaciones (Claim Guard)
    # Estructura: confirmed_offerings, confirmed_materials, confirmed_capabilities,
    # confirmed_commercial_terms, confirmed_service_area, confirmed_certifications,
    # allowed_moderate_claims, differentiating_terms, unknowns_requiring_confirmation,
    # source_fragments
    brand_fact_profile = models.JSONField(default=dict, blank=True)
    # Nombres de campo (de _BRAND_DNA_EDITABLE_FIELDS en views.py) que el usuario ya
    # revisó y aprobó explícitamente. Editar o reanalizar un campo lo quita de esta
    # lista automáticamente — el resto de los campos conserva su estado. Se usa para
    # bloquear la regeneración de contenido hasta que TODOS los campos editables
    # estén aprobados (evita regenerar con partes del ADN de marca sin revisar).
    approved_fields = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'brand_dna_brand_dna'

    def __str__(self):
        return f"BrandDNA — {self.business_name}"


class ProductPhotoPrecheckAttempt(models.Model):
    """Registra cada llamada real al precheck de copyright/marca de foto de
    producto (core/brand_dna/extractors/product_photo_copyright_precheck.py) —
    solo cuando la llamada a Gemini se completó (éxito o rechazo), nunca en
    fail-open. Usado por can_precheck_photo (rate_limits.py) para limitar
    abuso de costo: un usuario autenticado probando muchas fotos seguidas."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'brand_dna_product_photo_precheck_attempt'


class ProductReferenceAsset(models.Model):
    """Normalized, auditable metadata for one product-reference upload.

    ``AnalysisJob.product_reference_image_paths`` intentionally remains the
    compatibility contract while the generation pipeline migrates to assets.
    """

    RELATIONSHIP_MAKER = 'maker'
    RELATIONSHIP_RESELLER = 'reseller'
    RELATIONSHIP_SERVICE = 'service'
    RELATIONSHIP_UNKNOWN = 'unknown'
    RELATIONSHIP_CHOICES = [
        (RELATIONSHIP_MAKER, 'Fabricante'),
        (RELATIONSHIP_RESELLER, 'Distribuidor'),
        (RELATIONSHIP_SERVICE, 'Servicio'),
        (RELATIONSHIP_UNKNOWN, 'Desconocida'),
    ]

    USAGE_EDIT_ALLOWED = 'edit_allowed'
    USAGE_PRESERVE_ONLY = 'preserve_only'
    USAGE_CONTEXT_ONLY = 'context_only'
    USAGE_CHOICES = [
        (USAGE_EDIT_ALLOWED, 'Edicion creativa permitida'),
        (USAGE_PRESERVE_ONLY, 'Conservar pixeles originales'),
        (USAGE_CONTEXT_ONLY, 'Solo contexto'),
    ]

    TRIAGE_PENDING = 'pending'
    TRIAGE_COMPLETE = 'complete'
    TRIAGE_FAILED = 'failed'
    TRIAGE_CHOICES = [
        (TRIAGE_PENDING, 'Pendiente'),
        (TRIAGE_COMPLETE, 'Completo'),
        (TRIAGE_FAILED, 'Fallido'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job = models.ForeignKey(
        AnalysisJob, on_delete=models.CASCADE, related_name='product_reference_assets',
    )
    position = models.PositiveIntegerField()
    storage_path = models.CharField(max_length=500)
    sha256 = models.CharField(max_length=64)
    mime_type = models.CharField(max_length=100, blank=True, default='')
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    analysis_description = models.TextField(blank=True, default='')
    product_category = models.CharField(max_length=100, blank=True, default='')
    commercial_relationship = models.CharField(
        max_length=20, choices=RELATIONSHIP_CHOICES, default=RELATIONSHIP_UNKNOWN,
    )
    usage_mode = models.CharField(
        max_length=20, choices=USAGE_CHOICES, default=USAGE_PRESERVE_ONLY,
    )
    risk_flags = models.JSONField(default=dict, blank=True)
    visible_brands = models.JSONField(default=list, blank=True)
    visible_text_summary = models.TextField(blank=True, default='')
    triage_status = models.CharField(
        max_length=20, choices=TRIAGE_CHOICES, default=TRIAGE_PENDING,
    )
    triage_version = models.CharField(max_length=50, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'brand_dna_product_reference_asset'
        ordering = ['position', 'created_at']
        constraints = [
            models.UniqueConstraint(fields=['job', 'position'], name='unique_job_asset_position'),
            models.UniqueConstraint(fields=['job', 'sha256'], name='unique_job_asset_sha256'),
        ]
        indexes = [
            models.Index(fields=['job', 'usage_mode'], name='asset_job_usage_idx'),
            models.Index(fields=['triage_status'], name='asset_triage_status_idx'),
        ]
