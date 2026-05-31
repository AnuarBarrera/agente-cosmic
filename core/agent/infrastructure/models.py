import uuid as _uuid

from django.db import models
from pgvector.django import VectorField


class AgentSession(models.Model):
    chat_id = models.BigIntegerField(unique=True)
    username = models.CharField(max_length=255, blank=True, default='')
    full_name = models.CharField(max_length=255, blank=True, default='')
    is_authorized = models.BooleanField(default=False)
    ROLE_ADMIN = 'admin'
    ROLE_VIEWER = 'viewer'
    ROLE_CHOICES = [(ROLE_ADMIN, 'Admin'), (ROLE_VIEWER, 'Viewer')]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='viewer')
    created_at = models.DateTimeField(auto_now_add=True)
    last_active_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'agent_session'
        verbose_name = 'Sesión del agente'
        verbose_name_plural = 'Sesiones del agente'

    def __str__(self):
        return f"{self.full_name} ({self.chat_id})"


class AgentMemory(models.Model):
    ROLE_USER = 'user'
    ROLE_ASSISTANT = 'assistant'
    ROLE_CHOICES = [(ROLE_USER, 'Usuario'), (ROLE_ASSISTANT, 'Asistente')]

    session = models.ForeignKey(AgentSession, on_delete=models.CASCADE, related_name='memories')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)
    embedding = VectorField(dimensions=768, null=True, blank=True)

    class Meta:
        db_table = 'agent_memory'
        verbose_name = 'Memoria del agente'
        verbose_name_plural = 'Memorias del agente'
        ordering = ['timestamp']

    def __str__(self):
        return f"[{self.role}] {self.content[:60]}"


class BrowserSession(models.Model):
    PLATFORMS = [
        ('instagram', 'Instagram'),
        ('facebook', 'Facebook'),
        ('tiktok', 'TikTok'),
        ('linkedin', 'LinkedIn'),
        ('twitter', 'Twitter/X'),
    ]
    platform = models.CharField(max_length=50, choices=PLATFORMS)
    username = models.CharField(max_length=255)
    cookies = models.JSONField(default=list)
    user_agent = models.CharField(max_length=512, blank=True, default='')
    is_valid = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'agent_browser_session'
        unique_together = ('platform', 'username')
        verbose_name = 'Sesión de navegador'
        verbose_name_plural = 'Sesiones de navegador'

    def __str__(self):
        return f"{self.platform}:{self.username}"


class AgentRequest(models.Model):
    session = models.ForeignKey(AgentSession, on_delete=models.CASCADE, related_name='requests')
    user_message = models.TextField()
    ai_response = models.TextField()
    model_used = models.CharField(max_length=100)
    tool_used = models.CharField(max_length=100, null=True, blank=True)
    duration_ms = models.IntegerField(default=0)
    estimated_tokens = models.IntegerField(default=0)
    success = models.BooleanField(default=True)
    error_message = models.TextField(null=True, blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'agent_request'
        verbose_name = 'Solicitud al agente'
        verbose_name_plural = 'Solicitudes al agente'
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.session} — {self.timestamp:%Y-%m-%d %H:%M}"


class AgentDocument(models.Model):
    filename = models.CharField(max_length=255)
    doc_type = models.CharField(max_length=50, default='general')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    num_chunks = models.IntegerField(default=0)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return self.filename


class AgentDocumentChunk(models.Model):
    document = models.ForeignKey(AgentDocument, on_delete=models.CASCADE, related_name='chunks')
    chunk_index = models.IntegerField()
    content = models.TextField()
    embedding = VectorField(dimensions=768, null=True, blank=True)

    class Meta:
        ordering = ['document', 'chunk_index']
        unique_together = [('document', 'chunk_index')]


class PendingJob(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
    ]
    job_id = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False)
    chat_id = models.CharField(max_length=50)
    command = models.CharField(max_length=50)
    workflow = models.CharField(max_length=100)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = 'agent'

    def __str__(self):
        return f'PendingJob({self.workflow}, {self.status})'


class ProspectLead(models.Model):
    place_id = models.CharField(max_length=255)
    chat_id = models.CharField(max_length=50)
    name = models.CharField(max_length=255)
    address = models.CharField(max_length=500, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    website = models.CharField(max_length=500, blank=True)
    rating = models.FloatField(null=True, blank=True)
    reviews_total = models.IntegerField(default=0)
    giro = models.CharField(max_length=255, blank=True)
    lat = models.FloatField(null=True, blank=True)
    lng = models.FloatField(null=True, blank=True)
    score = models.IntegerField(null=True, blank=True)
    score_reason = models.CharField(max_length=255, blank=True)
    contacted = models.BooleanField(default=False)
    contacted_at = models.DateTimeField(null=True, blank=True)
    searched_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('place_id', 'chat_id')]
        ordering = ['-searched_at', '-score']
