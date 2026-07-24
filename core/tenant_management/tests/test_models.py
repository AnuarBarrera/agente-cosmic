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


def test_subscription_stripe_fields_default_empty():
    plan = Plan.objects.create(name='Plan Test Stripe Fields')
    tenant = TenantModel.objects.create(name='Tenant Stripe Fields', status='active')
    sub = Subscription.objects.create(tenant=tenant, plan=plan)
    assert sub.stripe_customer_id == ''
    assert sub.stripe_subscription_id == ''
    assert sub.cancel_at_period_end is False


def test_subscription_stripe_fields_accept_values():
    plan = Plan.objects.create(name='Plan Test Stripe Fields 2')
    tenant = TenantModel.objects.create(name='Tenant Stripe Fields 2', status='active')
    sub = Subscription.objects.create(
        tenant=tenant, plan=plan,
        stripe_customer_id='cus_123', stripe_subscription_id='sub_123',
        cancel_at_period_end=True,
    )
    sub.refresh_from_db()
    assert sub.stripe_customer_id == 'cus_123'
    assert sub.stripe_subscription_id == 'sub_123'
    assert sub.cancel_at_period_end is True


def test_subscription_paid_until_defaults_to_none():
    plan = Plan.objects.create(name='Plan Test Paid Until')
    tenant = TenantModel.objects.create(name='Tenant Paid Until', status='active')
    sub = Subscription.objects.create(tenant=tenant, plan=plan)
    assert sub.paid_until is None


def test_subscription_paid_until_accepts_datetime():
    plan = Plan.objects.create(name='Plan Test Paid Until 2')
    tenant = TenantModel.objects.create(name='Tenant Paid Until 2', status='active')
    paid_until = timezone.now() + timezone.timedelta(days=28)
    sub = Subscription.objects.create(tenant=tenant, plan=plan, status='active', paid_until=paid_until)
    sub.refresh_from_db()
    assert sub.status == 'active'
    assert sub.paid_until == paid_until


