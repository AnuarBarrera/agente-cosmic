
import uuid
from enum import Enum
from django.db import models

class SagaStatus(Enum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    COMPENSATING = "COMPENSATING"
    COMPENSATED = "COMPENSATED"

class Saga(models.Model):
    """
    Modelo para persistir el estado de una saga.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    status = models.CharField(
        max_length=20,
        choices=[(tag.name, tag.value) for tag in SagaStatus],
        default=SagaStatus.PENDING.name
    )
    context = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'shared'

    def __str__(self):
        return f"{self.name} ({self.id}) - {self.status}"

