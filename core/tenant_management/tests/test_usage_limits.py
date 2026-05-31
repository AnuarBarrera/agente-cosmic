import pytest
import uuid
from django.test import TestCase
from unittest.mock import patch, MagicMock
from django.utils import timezone
from datetime import timedelta

from core.tenant_management.models import TenantModel, Plan, Subscription, UsageRecord, TenantConfigurationModel
from core.tenant_management.domain.entities import PlanName
from core.tenant_management.domain.services import UsageTrackingService
from core.tenant_management.infrastructure.repositories import DjangoTenantRepository, DjangoUsageRecordRepository
from core.shared.event_bus import EventBus

@pytest.mark.django_db
class TestUsageLimits(TestCase):
    def setUp(self):
        self.tenant_repo = DjangoTenantRepository()
        self.usage_repo = DjangoUsageRecordRepository()
        self.usage_service = UsageTrackingService(self.tenant_repo, self.usage_repo)

        # Obtener o crear Plan
        self.plan, _ = Plan.objects.get_or_create(
            name=PlanName.FREE.value,
            defaults={
                "max_daily_interactions": 3,  # Límite bajo para facilitar la prueba
                "max_monthly_interactions": 10,
                "price": 0.0
            }
        )
        # Asegurarse de que los límites sean los esperados por los tests
        self.plan.max_daily_interactions = 3
        self.plan.max_monthly_interactions = 10
        self.plan.save()
        
        # Crear Tenant
        self.tenant = TenantModel.objects.create(name="Tenant for Limits Test", status="active")
        # Crear Configuración de Tenant
        TenantConfigurationModel.objects.create(tenant=self.tenant)
        # Crear Suscripción
        Subscription.objects.create(tenant=self.tenant, plan=self.plan, status="active")

        # Limpiar registros de uso antes de cada prueba
        UsageRecord.objects.all().delete()

    def test_can_perform_interaction_under_limit(self):
        """Verifica que un tenant puede realizar una interacción si está por debajo del límite."""
        can_interact = self.usage_service.can_perform_interaction(self.tenant.id)
        self.assertTrue(can_interact)

    def test_cannot_perform_interaction_at_daily_limit(self):
        """Verifica que un tenant no puede interactuar al alcanzar el límite diario."""
        from django.utils import timezone
        from datetime import timedelta
        
        # Simular 3 interacciones (igual al límite)
        now = timezone.now()
        for _ in range(3):
            UsageRecord.objects.create(
                tenant=self.tenant, 
                interaction_count=1,
                timestamp=now
            )
        
        # Verificar que no puede interactuar
        can_interact = self.usage_service.can_perform_interaction(self.tenant.id)
        self.assertFalse(can_interact)

    def test_cannot_perform_interaction_at_monthly_limit(self):
        """Verifica que un tenant no puede interactuar al alcanzar el límite mensual."""
        from django.utils import timezone
        from datetime import timedelta
        
        # Simular 10 interacciones (igual al límite)
        now = timezone.now()
        for _ in range(10):
            UsageRecord.objects.create(
                tenant=self.tenant, 
                interaction_count=1,
                timestamp=now
            )
            
        can_interact = self.usage_service.can_perform_interaction(self.tenant.id)
        self.assertFalse(can_interact)

    @patch.object(EventBus, 'publish')
    def test_usage_limit_exceeded_event_published_for_daily_limit(self, mock_publish):
        """Verifica que se publica un evento cuando se excede el límite diario."""
        from django.utils import timezone
        from datetime import timedelta
        
        # Simular 2 interacciones previas
        now = timezone.now()
        for _ in range(2):
            UsageRecord.objects.create(
                tenant=self.tenant, 
                interaction_count=1,
                timestamp=now
            )

        # La tercera interacción debería disparar el evento
        self.usage_service.record_interaction(self.tenant.id)

        # Verificar que se llamó a publish
        self.assertTrue(mock_publish.called)
        event_published = mock_publish.call_args[0][0]
        self.assertEqual(event_published.tenant_id, self.tenant.id)
        self.assertEqual(event_published.limit_type, "daily")
        self.assertEqual(event_published.current_usage, 3)
        self.assertEqual(event_published.limit, 3)

    @patch.object(EventBus, 'publish')
    def test_usage_limit_exceeded_event_published_for_monthly_limit(self, mock_publish):
        """Verifica que se publica un evento cuando se excede el límite mensual."""
        from django.utils import timezone
        from datetime import timedelta
        
        # Simular 9 interacciones previas en días diferentes para no alcanzar el límite diario
        base_time = timezone.now()
        for i in range(9):
            UsageRecord.objects.create(
                tenant=self.tenant,
                interaction_count=1,
                timestamp=base_time - timedelta(days=i + 1)
            )

        # La décima interacción debería disparar el evento de límite mensual
        self.usage_service.record_interaction(self.tenant.id)

        # Verificar que se llamó a publish
        self.assertTrue(mock_publish.called)
        event_published = mock_publish.call_args[0][0]
        self.assertEqual(event_published.tenant_id, self.tenant.id)
        self.assertEqual(event_published.limit_type, "monthly")
        self.assertEqual(event_published.current_usage, 10)
        self.assertEqual(event_published.limit, 10)

    def test_can_interact_if_daily_is_zero_but_monthly_is_full(self):
        """
        Verifica que un tenant no puede interactuar si su uso diario es cero
        pero ya ha alcanzado el límite mensual en días anteriores.
        """
        from django.utils import timezone
        from datetime import timedelta
        
        # Simular 10 interacciones en días anteriores (igual al límite)
        base_time = timezone.now()
        for i in range(10):
            UsageRecord.objects.create(
                tenant=self.tenant,
                interaction_count=1,
                timestamp=base_time - timedelta(days=i + 1) # Días pasados
            )
        
        # El uso de hoy es 0, pero el mensual es 10.
        can_interact = self.usage_service.can_perform_interaction(self.tenant.id)
        self.assertFalse(can_interact, "Debería devolver False si el límite mensual ya se ha alcanzado.")

    @patch('core.tenant_management.domain.services.cancel_jobs_for_tenant')
    def test_job_cancellation_triggered_on_daily_limit(self, mock_cancel_jobs):
        """
        Verifica que la función de cancelación de trabajos de RQ es llamada
        cuando se excede el límite diario.
        """
        # Simular 2 interacciones previas
        for _ in range(2):
            UsageRecord.objects.create(tenant=self.tenant, interaction_count=1)

        # La tercera interacción debería disparar la cancelación
        self.usage_service.record_interaction(self.tenant.id)

        # Verificar que la función de cancelación fue llamada una vez con el tenant_id correcto
        mock_cancel_jobs.assert_called_once_with(self.tenant.id)
