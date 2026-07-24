import pytest
from django.utils import timezone
from core.tenant_management.models import TenantModel, Plan, Subscription

pytestmark = pytest.mark.django_db


def test_subscription_trial_ends_at_defaults_to_none():
    plan = Plan.objects.create(name='Plan Test Trial')
    tenant = TenantModel.objects.create(name='Tenant Test', status='active')
    sub = Subscription.objects.create(tenant=tenant, plan=plan)
    assert sub.trial_ends_at is None


def test_subscription_trial_ends_at_accepts_datetime():
    plan = Plan.objects.create(name='Plan Test Trial 2')
    tenant = TenantModel.objects.create(name='Tenant Test 2', status='active')
    ends_at = timezone.now() + timezone.timedelta(days=7)
    sub = Subscription.objects.create(
        tenant=tenant, plan=plan, status='trialing', trial_ends_at=ends_at,
    )
    sub.refresh_from_db()
    assert sub.status == 'trialing'
    assert sub.trial_ends_at == ends_at
