import uuid
from django.db import models

# Importar los modelos existentes de tenant_management
from core.tenant_management.models import Plan, TenantModel, Subscription, TenantConfigurationModel, User

# Modelo para registrar errores de interacción (movido desde routing_escalation)
class ErrorInteractionModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(TenantModel, on_delete=models.CASCADE, related_name='error_interactions')
    error_type = models.CharField(max_length=100)  # 'limit_exceeded', 'api_error', etc.
    details = models.JSONField()  # Detalles del error
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'error_interaction'
        verbose_name = 'Error Interaction'
        verbose_name_plural = 'Error Interactions'
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.tenant.name} - {self.error_type} - {self.timestamp.strftime('%Y-%m-%d %H:%M')}"