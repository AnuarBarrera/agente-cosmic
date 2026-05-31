import pytest
from django.test import TestCase
from unittest.mock import MagicMock, patch

from core.tenant_management.admin import SubscriptionAdmin
from core.tenant_management.models import TenantModel, Plan, Subscription
from core.channel_integration.models import Channel

@pytest.mark.django_db
class TestSubscriptionAdmin(TestCase):
    def setUp(self):
        # Instancia del ModelAdmin
        self.admin = SubscriptionAdmin(Subscription, None)

        # Crear datos de prueba
        self.plan = Plan.objects.create(name="Test Plan", max_daily_interactions=10, max_monthly_interactions=100)
        self.tenant = TenantModel.objects.create(name="Test Tenant", status="active")
        self.subscription = Subscription.objects.create(tenant=self.tenant, plan=self.plan)

    def test_google_email_found(self):
        """
        Verifica que el método google_email devuelve el email correcto
        cuando existe un canal de tipo EMAIL.
        """
        # Crear un canal de email para el tenant
        Channel.objects.create(
            tenant_id=self.tenant.id,
            channel_type='EMAIL',
            configuration={'email': 'test@example.com'}
        )

        # Llamar al método del admin
        email = self.admin.google_email(self.subscription)
        
        self.assertEqual(email, 'test@example.com')

    def test_google_email_not_found_if_no_email_channel(self):
        """
        Verifica que el método devuelve 'N/A' si no hay un canal de tipo EMAIL.
        """
        # Crear un canal de otro tipo
        Channel.objects.create(
            tenant_id=self.tenant.id,
            channel_type='WEB',
            configuration={}
        )
        
        email = self.admin.google_email(self.subscription)
        
        self.assertEqual(email, 'N/A')

    def test_google_email_not_found_if_no_channels(self):
        """
        Verifica que el método devuelve 'N/A' si el tenant no tiene canales.
        """
        email = self.admin.google_email(self.subscription)
        
        self.assertEqual(email, 'N/A')

    def test_google_email_not_found_if_config_is_missing_email(self):
        """
        Verifica que el método devuelve 'N/A' si la configuración del canal
        no contiene la clave 'email'.
        """
        Channel.objects.create(
            tenant_id=self.tenant.id,
            channel_type='EMAIL',
            configuration={'other_key': 'some_value'} # Sin 'email'
        )
        
        email = self.admin.google_email(self.subscription)
        
        self.assertEqual(email, 'N/A')
