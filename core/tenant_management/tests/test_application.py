import pytest
import uuid
from datetime import datetime, timedelta
from django.utils import timezone
from ..application.services import TenantApplicationService
from ..infrastructure.repositories import DjangoTenantRepository
from core.conversation_management.infrastructure.models import ConversationModel, MessageModel
from core.routing_escalation.infrastructure.models import EscalationCaseModel
from core.channel_integration.infrastructure.models import ChannelModel

pytestmark = pytest.mark.django_db

@pytest.fixture
def tenant_with_data(db):
    """
    Creates a tenant and populates it with data for dashboard calculations.
    """
    from ..models import TenantModel, Plan, Subscription, User

    # Create a user and a tenant
    user = User.objects.create_user(username="testuser", email="test@example.com", password="password")
    tenant = TenantModel.objects.create(name="Dashboard Tenant", status="active")
    user.tenant = tenant
    user.save()
    
    # Create a default plan and subscription
    plan = Plan.objects.create(name="Test Plan", price=10, max_daily_interactions=100, max_monthly_interactions=1000)
    Subscription.objects.create(tenant=tenant, plan=plan, status='active')

    # Create a channel
    channel = ChannelModel.objects.create(
        tenant_id=tenant.id, 
        type='EMAIL', 
        configuration={'name': 'Test Channel'},
        is_active=True,
        credentials={'user': 'test@gmail.com'}
    )

    # --- Data for Calculations ---
    now = timezone.now()

    # Conversation 1: Automated, 5 min response time
    conv1 = ConversationModel.objects.create(tenant_id=tenant.id, channel_id=channel.id, created_at=now - timedelta(minutes=30))
    MessageModel.objects.create(conversation=conv1, sender='customer', timestamp=now - timedelta(minutes=10))
    MessageModel.objects.create(conversation=conv1, sender='ai', timestamp=now - timedelta(minutes=5))

    # Conversation 2: Escalated, 10 min response time
    conv2 = ConversationModel.objects.create(tenant_id=tenant.id, channel_id=channel.id, created_at=now - timedelta(minutes=40))
    MessageModel.objects.create(conversation=conv2, sender='customer', timestamp=now - timedelta(minutes=20))
    MessageModel.objects.create(conversation=conv2, sender='ai', timestamp=now - timedelta(minutes=10))
    EscalationCaseModel.objects.create(tenant_id=tenant.id, conversation_id=conv2.id, status='OPEN')

    # Conversation 3: Automated, no response yet (should be ignored in avg time)
    conv3 = ConversationModel.objects.create(tenant_id=tenant.id, channel_id=channel.id, created_at=now - timedelta(minutes=5))
    MessageModel.objects.create(conversation=conv3, sender='customer', timestamp=now - timedelta(minutes=2))

    # Conversation 4: Automated, 15 min response time
    conv4 = ConversationModel.objects.create(tenant_id=tenant.id, channel_id=channel.id, created_at=now - timedelta(minutes=50))
    MessageModel.objects.create(conversation=conv4, sender='customer', timestamp=now - timedelta(minutes=30))
    MessageModel.objects.create(conversation=conv4, sender='ai', timestamp=now - timedelta(minutes=15))

    return tenant

def test_get_dashboard_data_calculations(tenant_with_data):
    """
    Tests the dynamic calculations for automation_rate and avg_response_time.
    """
    service = TenantApplicationService(DjangoTenantRepository())
    dashboard_data = service.get_dashboard_data(tenant_with_data.id)

    # --- Assertions ---
    # Total conversations should be 4
    assert dashboard_data.total_conversations == 4

    # Automation Rate: 3 out of 4 conversations are automated (1 is escalated)
    # (3 / 4) * 100 = 75.0%
    assert dashboard_data.automation_rate == 75.0

    # Avg Response Time:
    # Conv1: 5 mins (300s)
    # Conv2: 10 mins (600s)
    # Conv3: Ignored
    # Conv4: 15 mins (900s)
    # Average = (300 + 600 + 900) / 3 = 1800 / 3 = 600 seconds = 10 minutes
    assert dashboard_data.avg_response_time == "10m 0s"
