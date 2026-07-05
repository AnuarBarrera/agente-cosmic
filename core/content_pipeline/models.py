import uuid
from django.db import models
from core.brand_dna.models import BrandDNA


class ContentCalendar(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    brand_dna = models.OneToOneField(BrandDNA, on_delete=models.CASCADE, related_name='calendar')
    created_at = models.DateTimeField(auto_now_add=True)
    active_product_images = models.JSONField(default=list, blank=True)

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
    published_at = models.DateTimeField(null=True, blank=True)

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


class WeeklyFeedback(models.Model):
    CONTINUE_PENDING = 'pending'
    CONTINUE_YES = 'yes'
    CONTINUE_NO = 'no'
    CONTINUE_CHOICES = [
        (CONTINUE_PENDING, 'Pendiente'),
        (CONTINUE_YES, 'Sí'),
        (CONTINUE_NO, 'No'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    calendar = models.ForeignKey(ContentCalendar, on_delete=models.CASCADE, related_name='feedback_entries')
    week_number = models.IntegerField()
    rating = models.IntegerField(null=True, blank=True)
    comment = models.TextField(blank=True, default='')
    continue_decision = models.CharField(max_length=10, choices=CONTINUE_CHOICES, default=CONTINUE_PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'content_pipeline_weekly_feedback'
        unique_together = ('calendar', 'week_number')
        ordering = ['week_number']

    def __str__(self):
        return f"Feedback semana {self.week_number} — {self.calendar.brand_dna.business_name}"
