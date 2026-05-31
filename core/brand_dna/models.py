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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'brand_dna_brand_dna'

    def __str__(self):
        return f"BrandDNA — {self.business_name}"
