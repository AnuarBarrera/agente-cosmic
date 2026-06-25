import pytest
from django.test import Client
from django.utils import timezone
from core.tenant_management.models import TenantModel, Plan, Subscription

pytestmark = pytest.mark.django_db


@pytest.fixture
def free_plan():
    plan, _ = Plan.objects.get_or_create(name='Free', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    return plan


@pytest.fixture
def user_with_tenant(django_user_model, free_plan):
    u = django_user_model.objects.create_user(
        email='delete@test.com', username='delete@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=u.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=free_plan)
    u.tenant = tenant
    u.save(update_fields=['tenant'])
    return u


def test_deactivate_account_sets_inactive(user_with_tenant):
    c = Client()
    c.force_login(user_with_tenant)
    response = c.post('/dashboard/delete-account/', {'confirmation': 'ELIMINAR'})
    assert response.status_code == 302
    assert '/auth/login/' in response.url

    user_with_tenant.refresh_from_db()
    assert user_with_tenant.is_active is False
    assert user_with_tenant.deactivated_at is not None
    assert user_with_tenant.tenant.status == 'deactivated'
    assert user_with_tenant.tenant.subscription.status == 'canceled'


def test_deactivate_without_confirmation_rejected(user_with_tenant):
    c = Client()
    c.force_login(user_with_tenant)
    response = c.post('/dashboard/delete-account/', {'confirmation': 'wrong'})
    assert response.status_code == 302
    assert '/dashboard/' in response.url

    user_with_tenant.refresh_from_db()
    assert user_with_tenant.is_active is True


def test_deactivate_requires_post(user_with_tenant):
    c = Client()
    c.force_login(user_with_tenant)
    response = c.get('/dashboard/delete-account/')
    assert response.status_code == 302


def test_login_blocked_after_deactivation(user_with_tenant):
    c = Client()
    c.force_login(user_with_tenant)
    c.post('/dashboard/delete-account/', {'confirmation': 'ELIMINAR'})

    c2 = Client()
    response = c2.post('/auth/login/', {
        'email': 'delete@test.com',
        'password': 'pass1234',
    })
    assert response.status_code == 200
