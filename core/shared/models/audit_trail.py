"""
Audit Trail Models for DIALOGIX
Comprehensive audit logging for all data modifications and system events
"""

from django.db import models
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.contrib.contenttypes.fields import GenericForeignKey
from django.utils import timezone
from django.conf import settings
import json
import uuid

User = get_user_model()

class AuditEvent(models.Model):
    """
    Core audit event model for tracking all system activities
    """
    
    EVENT_TYPES = [
        ('CREATE', 'Create'),
        ('UPDATE', 'Update'), 
        ('DELETE', 'Delete'),
        ('LOGIN', 'Login'),
        ('LOGOUT', 'Logout'),
        ('ACCESS', 'Data Access'),
        ('EXPORT', 'Data Export'),
        ('PERMISSION_CHANGE', 'Permission Change'),
        ('SYSTEM_CONFIG', 'System Configuration Change'),
        ('SECURITY_EVENT', 'Security Event'),
        ('ERROR', 'Error/Exception'),
        ('API_CALL', 'API Call'),
        ('FILE_UPLOAD', 'File Upload'),
        ('FILE_DOWNLOAD', 'File Download'),
    ]
    
    SEVERITY_LEVELS = [
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('CRITICAL', 'Critical'),
    ]
    
    # Primary audit fields
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    event_type = models.CharField(max_length=50, choices=EVENT_TYPES, db_index=True)
    severity = models.CharField(max_length=20, choices=SEVERITY_LEVELS, default='LOW')
    
    # User and session information
    user = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='audit_events'
    )
    session_id = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    tenant = models.ForeignKey(
        'tenant_management.TenantModel',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='audit_events'
    )
    
    # Request information
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    request_method = models.CharField(max_length=10, null=True, blank=True)
    request_path = models.CharField(max_length=500, null=True, blank=True)
    
    # Event details
    description = models.TextField()
    category = models.CharField(max_length=100, db_index=True)  # e.g., 'user_management', 'conversation'
    
    # Target object information (what was modified)
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, null=True, blank=True)
    object_id = models.CharField(max_length=255, null=True, blank=True)
    content_object = GenericForeignKey('content_type', 'object_id')
    
    # Change details
    old_values = models.JSONField(null=True, blank=True)  # Previous state
    new_values = models.JSONField(null=True, blank=True)  # New state
    changed_fields = models.JSONField(null=True, blank=True)  # List of changed field names
    
    # Additional metadata
    metadata = models.JSONField(default=dict, blank=True)  # Additional context
    
    # Security flags
    is_security_event = models.BooleanField(default=False, db_index=True)
    requires_attention = models.BooleanField(default=False, db_index=True)
    
    class Meta:
        db_table = 'audit_events'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['timestamp', 'event_type']),
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['tenant', 'timestamp']),
            models.Index(fields=['category', 'timestamp']),
            models.Index(fields=['is_security_event', 'timestamp']),
        ]
    
    def __str__(self):
        user_info = f" by {self.user}" if self.user else ""
        return f"{self.event_type} - {self.description[:50]}{user_info} at {self.timestamp}"
    
    def save(self, *args, **kwargs):
        # Sanitize sensitive data before saving
        self.old_values = self._sanitize_sensitive_data(self.old_values)
        self.new_values = self._sanitize_sensitive_data(self.new_values)
        super().save(*args, **kwargs)
    
    def _sanitize_sensitive_data(self, data):
        """
        Remove sensitive information from audit data
        """
        if not isinstance(data, dict):
            return data
        
        sensitive_fields = [
            'password', 'secret', 'key', 'token', 'credential',
            'api_key', 'private', 'csrf', 'session'
        ]
        
        sanitized = {}
        for key, value in data.items():
            if any(sensitive in key.lower() for sensitive in sensitive_fields):
                sanitized[key] = '[REDACTED]'
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize_sensitive_data(value)
            else:
                sanitized[key] = value
        
        return sanitized


class SecurityEvent(models.Model):
    """
    Specialized model for security-related events requiring immediate attention
    """
    
    SECURITY_EVENT_TYPES = [
        ('FAILED_LOGIN', 'Failed Login Attempt'),
        ('BRUTE_FORCE', 'Brute Force Attack'),
        ('SUSPICIOUS_ACCESS', 'Suspicious Access Pattern'),
        ('PRIVILEGE_ESCALATION', 'Privilege Escalation Attempt'),
        ('DATA_BREACH', 'Data Breach Incident'),
        ('UNAUTHORIZED_ACCESS', 'Unauthorized Access'),
        ('MALICIOUS_REQUEST', 'Malicious Request'),
        ('RATE_LIMIT_EXCEEDED', 'Rate Limit Exceeded'),
        ('ACCOUNT_LOCKOUT', 'Account Lockout'),
        ('PASSWORD_RESET', 'Password Reset'),
        ('PERMISSION_DENIED', 'Permission Denied'),
        ('CSRF_ATTACK', 'CSRF Attack Attempt'),
        ('XSS_ATTEMPT', 'XSS Attack Attempt'),
        ('SQL_INJECTION', 'SQL Injection Attempt'),
    ]
    
    STATUS_CHOICES = [
        ('OPEN', 'Open'),
        ('INVESTIGATING', 'Under Investigation'),
        ('RESOLVED', 'Resolved'),
        ('FALSE_POSITIVE', 'False Positive'),
        ('IGNORED', 'Ignored'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    audit_event = models.OneToOneField(
        AuditEvent, 
        on_delete=models.CASCADE, 
        related_name='security_event'
    )
    
    event_type = models.CharField(max_length=50, choices=SECURITY_EVENT_TYPES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='OPEN')
    
    # Risk assessment
    risk_score = models.IntegerField(default=0)  # 0-100 risk score
    impact_level = models.CharField(max_length=20, choices=AuditEvent.SEVERITY_LEVELS)
    
    # Response information
    response_required = models.BooleanField(default=True)
    response_deadline = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='assigned_security_events'
    )
    
    # Additional context
    attack_vector = models.CharField(max_length=100, null=True, blank=True)
    affected_resources = models.JSONField(default=list, blank=True)
    remediation_notes = models.TextField(null=True, blank=True)
    
    # Resolution
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolution_notes = models.TextField(null=True, blank=True)
    
    class Meta:
        db_table = 'audit_security_events'
        ordering = ['-audit_event__timestamp']
        indexes = [
            models.Index(fields=['event_type', 'status']),
            models.Index(fields=['risk_score']),
            models.Index(fields=['response_required', 'status']),
        ]
    
    def __str__(self):
        return f"Security Event: {self.event_type} - {self.status}"
    
    def mark_resolved(self, user=None, notes=None):
        """
        Mark security event as resolved
        """
        self.status = 'RESOLVED'
        self.resolved_at = timezone.now()
        if notes:
            self.resolution_notes = notes
        self.save()


class DataAccessLog(models.Model):
    """
    Specialized logging for data access events (GDPR compliance)
    """
    
    ACCESS_TYPES = [
        ('READ', 'Read'),
        ('SEARCH', 'Search'),
        ('EXPORT', 'Export'),
        ('REPORT', 'Report Generation'),
        ('BACKUP', 'Backup Access'),
        ('ADMIN', 'Administrative Access'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    
    # Who accessed the data
    accessor = models.ForeignKey(User, on_delete=models.CASCADE, related_name='data_accesses')
    
    # What data was accessed
    data_subject = models.ForeignKey(
        User, 
        on_delete=models.CASCADE, 
        related_name='data_access_logs',
        help_text="The user whose data was accessed"
    )
    
    # Access details
    access_type = models.CharField(max_length=20, choices=ACCESS_TYPES)
    data_category = models.CharField(max_length=100)  # e.g., 'profile', 'conversations', 'billing'
    fields_accessed = models.JSONField(default=list, blank=True)
    
    # Legal basis for processing (GDPR Article 6)
    legal_basis = models.CharField(
        max_length=100, 
        help_text="Legal basis for data processing under GDPR"
    )
    
    # Context
    purpose = models.CharField(max_length=200, help_text="Purpose of data access")
    request_source = models.CharField(max_length=100)  # API, web interface, etc.
    
    # Technical details
    ip_address = models.GenericIPAddressField()
    user_agent = models.TextField(null=True, blank=True)
    
    # Data volume
    records_accessed = models.IntegerField(default=1)
    
    class Meta:
        db_table = 'data_access_logs'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['data_subject', 'timestamp']),
            models.Index(fields=['accessor', 'timestamp']),
            models.Index(fields=['access_type', 'timestamp']),
        ]
    
    def __str__(self):
        return f"{self.accessor} accessed {self.data_subject}'s {self.data_category} data"


class SystemChangeLog(models.Model):
    """
    Track system configuration and infrastructure changes
    """
    
    CHANGE_TYPES = [
        ('CONFIG', 'Configuration Change'),
        ('DEPLOYMENT', 'Code Deployment'),
        ('SECURITY_UPDATE', 'Security Update'),
        ('FEATURE_FLAG', 'Feature Flag Change'),
        ('INFRASTRUCTURE', 'Infrastructure Change'),
        ('DATABASE', 'Database Schema Change'),
        ('PERMISSION', 'Permission/Role Change'),
        ('INTEGRATION', 'Integration Configuration'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    
    change_type = models.CharField(max_length=30, choices=CHANGE_TYPES)
    component = models.CharField(max_length=100)  # What system component was changed
    version_before = models.CharField(max_length=50, null=True, blank=True)
    version_after = models.CharField(max_length=50, null=True, blank=True)
    
    # Change details
    description = models.TextField()
    changed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    change_reason = models.TextField()
    
    # Technical details
    configuration_diff = models.JSONField(null=True, blank=True)  # Before/after configuration
    affected_services = models.JSONField(default=list, blank=True)
    
    # Approval and tracking
    approved_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='approved_changes'
    )
    change_ticket = models.CharField(max_length=50, null=True, blank=True)
    
    # Impact assessment
    downtime_minutes = models.IntegerField(default=0)
    users_affected = models.IntegerField(default=0)
    rollback_plan = models.TextField(null=True, blank=True)
    
    class Meta:
        db_table = 'system_change_logs'
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['change_type', 'timestamp']),
            models.Index(fields=['component', 'timestamp']),
        ]
    
    def __str__(self):
        return f"{self.change_type}: {self.component} at {self.timestamp}"