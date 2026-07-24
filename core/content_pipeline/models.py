import uuid
from django.db import models
from core.brand_dna.models import BrandDNA


class ContentCalendar(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brand_dna = models.OneToOneField(BrandDNA, on_delete=models.CASCADE, related_name='calendar')
    created_at = models.DateTimeField(auto_now_add=True)
    next_week_generating = models.BooleanField(default=False)

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
    image_url = models.URLField(max_length=1000)
    # Carrusel (H20 + roadmap #5): lista ordenada de URLs de slides, vacia para posts
    # normales. image_url sigue siendo la portada/slide 1 para retrocompatibilidad
    # (email, thumbnail del dashboard, endpoint de descarga por default).
    image_urls = models.JSONField(default=list, blank=True)
    # Reel (roadmap #7): URL del MP4 final. image_url guarda el poster frame
    # (segundo 1 del video) para retrocompatibilidad con email/thumbnail.
    video_url = models.URLField(max_length=1000, blank=True, default='')
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

    class Meta:
        db_table = 'content_pipeline_post'
        ordering = ['day_number']

    def __str__(self):
        return f"Día {self.day_number} — {self.calendar.brand_dna.business_name}"
