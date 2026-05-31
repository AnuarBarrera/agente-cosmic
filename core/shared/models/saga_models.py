"""
Saga Pattern Models for DIALOGIX
Models to support distributed transaction patterns and workflow orchestration
"""

from django.db import models
from django.utils import timezone
import uuid
from enum import Enum

class SagaStatus(Enum):
    """
    Enum for Saga execution status
    """
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    COMPENSATING = "compensating"


class Saga(models.Model):
    """
    Model to track distributed transaction sagas
    """
    
    STATUS_CHOICES = [
        (SagaStatus.PENDING.name, 'Pending'),
        (SagaStatus.PROCESSING.name, 'Processing'),
        (SagaStatus.COMPLETED.name, 'Completed'),
        (SagaStatus.FAILED.name, 'Failed'),
        (SagaStatus.CANCELLED.name, 'Cancelled'),
        (SagaStatus.COMPENSATING.name, 'Compensating'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, help_text="Name of the saga type")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=SagaStatus.PENDING.name)
    
    # Context and state information
    context = models.JSONField(default=dict, help_text="Saga execution context and data")
    
    # Timing information
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    # Timeout and retry configuration
    timeout_seconds = models.IntegerField(default=300, help_text="Saga timeout in seconds")
    retry_count = models.IntegerField(default=0, help_text="Number of retries attempted")
    max_retries = models.IntegerField(default=3, help_text="Maximum retry attempts")
    
    # Error information
    error_message = models.TextField(null=True, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    
    # Tenant isolation
    tenant = models.ForeignKey(
        'tenant_management.TenantModel',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='sagas'
    )
    
    class Meta:
        db_table = 'sagas'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['name', 'status']),
            models.Index(fields=['tenant', 'status']),
        ]
    
    def __str__(self):
        return f"Saga {self.name} ({self.status}) - {self.id}"
    
    def mark_started(self):
        """Mark saga as started"""
        if self.status == SagaStatus.PENDING.name:
            self.status = SagaStatus.PROCESSING.name
            self.started_at = timezone.now()
            self.save()
    
    def mark_completed(self):
        """Mark saga as completed successfully"""
        self.status = SagaStatus.COMPLETED.name
        self.completed_at = timezone.now()
        self.save()
    
    def mark_failed(self, error_message: str = None):
        """Mark saga as failed"""
        self.status = SagaStatus.FAILED.name
        self.error_message = error_message
        self.last_error_at = timezone.now()
        self.completed_at = timezone.now()
        self.save()
    
    def mark_cancelled(self):
        """Mark saga as cancelled"""
        self.status = SagaStatus.CANCELLED.name
        self.completed_at = timezone.now()
        self.save()
    
    def increment_retry(self):
        """Increment retry counter"""
        self.retry_count += 1
        self.save()
    
    def can_retry(self) -> bool:
        """Check if saga can be retried"""
        return self.retry_count < self.max_retries
    
    def is_expired(self) -> bool:
        """Check if saga has exceeded timeout"""
        if not self.started_at:
            return False
        
        elapsed = (timezone.now() - self.started_at).total_seconds()
        return elapsed > self.timeout_seconds


class SagaStep(models.Model):
    """
    Model to track individual saga steps
    """
    
    STEP_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('processing', 'Processing'),
        ('completed', 'Completed'),
        ('failed', 'Failed'),
        ('skipped', 'Skipped'),
        ('compensated', 'Compensated'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    saga = models.ForeignKey(Saga, on_delete=models.CASCADE, related_name='steps')
    
    # Step information
    step_name = models.CharField(max_length=100)
    step_order = models.IntegerField(help_text="Execution order of the step")
    status = models.CharField(max_length=20, choices=STEP_STATUS_CHOICES, default='pending')
    
    # Step data
    input_data = models.JSONField(default=dict, help_text="Input data for the step")
    output_data = models.JSONField(null=True, blank=True, help_text="Output data from the step")
    
    # Timing
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    # Error handling
    error_message = models.TextField(null=True, blank=True)
    retry_count = models.IntegerField(default=0)
    max_retries = models.IntegerField(default=3)
    
    # Compensation
    compensation_step_name = models.CharField(
        max_length=100, 
        null=True, 
        blank=True,
        help_text="Name of the compensation step to run if this step needs to be undone"
    )
    compensation_data = models.JSONField(
        null=True, 
        blank=True, 
        help_text="Data needed for compensation"
    )
    
    class Meta:
        db_table = 'saga_steps'
        ordering = ['saga', 'step_order']
        unique_together = [['saga', 'step_name']]
        indexes = [
            models.Index(fields=['saga', 'step_order']),
            models.Index(fields=['saga', 'status']),
        ]
    
    def __str__(self):
        return f"Step {self.step_name} ({self.status}) - Saga {self.saga.id}"
    
    def mark_started(self):
        """Mark step as started"""
        self.status = 'processing'
        self.started_at = timezone.now()
        self.save()
    
    def mark_completed(self, output_data: dict = None):
        """Mark step as completed"""
        self.status = 'completed'
        self.completed_at = timezone.now()
        if output_data:
            self.output_data = output_data
        self.save()
    
    def mark_failed(self, error_message: str = None):
        """Mark step as failed"""
        self.status = 'failed'
        self.completed_at = timezone.now()
        if error_message:
            self.error_message = error_message
        self.save()
    
    def mark_compensated(self):
        """Mark step as compensated"""
        self.status = 'compensated'
        self.save()
    
    def increment_retry(self):
        """Increment retry counter"""
        self.retry_count += 1
        self.save()
    
    def can_retry(self) -> bool:
        """Check if step can be retried"""
        return self.retry_count < self.max_retries