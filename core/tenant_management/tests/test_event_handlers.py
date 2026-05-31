import pytest
import uuid
from django.test import TestCase
from core.shared.event_bus import EventBus, _EventBus, register_handler
from core.shared.events import MessageSentToChannel, UsageLimitExceeded
from core.tenant_management.models import TenantModel, Plan, Subscription, UsageRecord, TenantConfigurationModel
from core.channel_integration.infrastructure.models import ChannelModel
from core.tenant_management.domain.entities import PlanName
from core.tenant_management.application.event_handlers import register_tenant_event_handlers, TenantEventHandlers
from core.tenant_management.infrastructure.repositories import DjangoTenantRepository, DjangoUsageRecordRepository
from unittest.mock import MagicMock, patch
from core.tenant_management.application.services import handle_usage_limit_exceeded


@pytest.mark.django_db
class TestTenantEventHandlers(TestCase):
    def setUp(self):
        from core.shared.event_bus import _handlers, _EventBus, EventBus
        _handlers.clear() # Clear global handlers

        # Ensure EventBus uses the correct internal instance
        EventBus.set_instance(_EventBus()) # Reset the singleton instance

        # Obtener o crear un Plan
        self.plan, _ = Plan.objects.get_or_create(
            name=PlanName.FREE.value,
            defaults={
                "max_daily_interactions": 10,
                "max_monthly_interactions": 100,
                "price": 0.0
            }
        )
        # Crear un Tenant
        self.tenant = TenantModel.objects.create(
            name="Test Tenant for Events",
            status="active"
        )
        self.tenant.save() # Asegurarse de que el tenant esté guardado en la base de datos de prueba
        # Crear una Configuración de Tenant
        TenantConfigurationModel.objects.create(tenant=self.tenant)
        # Crear una Suscripción
        self.subscription = Subscription.objects.create(
            tenant=self.tenant,
            plan=self.plan,
            status="active"
        )
        # Crear un Canal asociado al Tenant
        self.channel = ChannelModel.objects.create(
            id=uuid.uuid4(),
            tenant_id=self.tenant.id,
            type="EMAIL",
            is_active=True,
            credentials={"key": "value"},
            configuration={}
        )
        # Mockear DjangoChannelRepository
        self.mock_channel_repository = MagicMock()
        self.mock_channel_repository.find_by_id.return_value = self.channel

        # Crear una instancia de TenantEventHandlers con los repositorios correctos
        handler_instance = TenantEventHandlers()
        # Registrar los manejadores de esta instancia
        register_handler(MessageSentToChannel, handler_instance.handle_message_sent)

        

    

    def test_handle_message_sent_increments_usage(self):
        """
        Verifica que al publicar un evento MessageSentToChannel,
        se crea un UsageRecord para el tenant correcto.
        """
        # Pre-verificación: no debe haber registros de uso
        self.assertEqual(UsageRecord.objects.count(), 0)

        # Crear y publicar el evento
        event = MessageSentToChannel(
            conversation_id=uuid.uuid4(),
            message_id=uuid.uuid4(),
            channel_id=self.channel.id,
            tenant_id=self.tenant.id,
            delivery_status="success"
        )
        
        EventBus.publish(event)

        # Verificación: debe haberse creado un registro de uso
        self.assertEqual(UsageRecord.objects.count(), 1)
        usage_record = UsageRecord.objects.first()
        self.assertEqual(usage_record.tenant.id, self.tenant.id)
        self.assertEqual(usage_record.interaction_count, 1)

    @patch('core.tenant_management.application.services.notification_service.send_notification')
    @patch('django.conf.settings.DEFAULT_TENANT_NOTIFICATION_EMAIL', 'test@example.com')
    def test_handle_usage_limit_exceeded_sends_notification(self, mock_send_notification):
        """
        Verifica que al publicar un evento UsageLimitExceeded,
        se llama al NotificationServiceAdapter para enviar una notificación.
        """
        # Arrange
        tenant_id = self.tenant.id
        event = UsageLimitExceeded(
            tenant_id=tenant_id,
            limit_type="ai_analysis",
            current_usage=100,
            limit=50
        )

        # Act
        # Directly call the handler function, as it's registered globally
        from core.tenant_management.application.services import handle_usage_limit_exceeded
        handle_usage_limit_exceeded(event)

        # Assert
        mock_send_notification.assert_called_once()
        call_args, call_kwargs = mock_send_notification.call_args
        
        self.assertEqual(call_kwargs['tenant_id'], str(tenant_id))
        self.assertEqual(call_kwargs['recipient_email'], 'test@example.com')
        self.assertIn("límite de uso excedido", call_kwargs['subject'].lower())
        self.assertIn("ai_analysis", call_kwargs['message'].lower())
        self.assertIn("100", call_kwargs['message'])
        self.assertIn("50", call_kwargs['message'])

    @patch('core.tenant_management.application.services.notification_service.send_notification')
    @patch('django.conf.settings.DEFAULT_TENANT_NOTIFICATION_EMAIL', 'premium@example.com')
    def test_handle_plan_changed_sends_notification_on_premium_upgrade(self, mock_send_notification):
        """
        Verifica que se envía una notificación cuando un tenant se actualiza al plan Premium.
        """
        # Arrange
        from core.shared.events import PlanChanged
        from core.tenant_management.application.services import handle_plan_changed

        tenant_id = self.tenant.id
        event = PlanChanged(
            tenant_id=tenant_id,
            old_plan_name=PlanName.FREE.value,
            new_plan_name=PlanName.PREMIUM.value
        )

        # Act
        handle_plan_changed(event)

        # Assert
        mock_send_notification.assert_called_once()
        call_args, call_kwargs = mock_send_notification.call_args
        
        self.assertEqual(call_kwargs['tenant_id'], str(tenant_id))
        self.assertEqual(call_kwargs['recipient_email'], 'premium@example.com')
        self.assertIn("bienvenido al plan premium", call_kwargs['subject'].lower())
        self.assertIn("congratulations", call_kwargs['message'].lower())

    @patch('core.tenant_management.application.services.notification_service.send_notification')
    def test_handle_plan_changed_does_not_send_notification_on_other_upgrades(self, mock_send_notification):
        """
        Verifica que no se envía una notificación para cambios de plan que no son a Premium.
        """
        # Arrange
        from core.shared.events import PlanChanged
        from core.tenant_management.application.services import handle_plan_changed

        event = PlanChanged(
            tenant_id=self.tenant.id,
            old_plan_name=PlanName.PREMIUM.value,
            new_plan_name=PlanName.FREE.value  # Downgrade or lateral change
        )

        # Act
        handle_plan_changed(event)

        # Assert
        mock_send_notification.assert_not_called()
