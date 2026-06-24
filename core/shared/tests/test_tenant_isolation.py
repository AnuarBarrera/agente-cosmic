import pytest
from django.test import Client
from core.tenant_management.models import TenantModel, Plan, Subscription

pytestmark = pytest.mark.django_db


@pytest.fixture
def free_plan():
    plan, _ = Plan.objects.get_or_create(
        name='Free',
        defaults={
            'max_calendars_per_week': 2,
            'max_post_regenerations': 2,
            'max_post_edits': 2,
            'price': 0,
        },
    )
    return plan


@pytest.fixture
def user_with_tenant(django_user_model, free_plan):
    user = django_user_model.objects.create_user(
        email='tenant@test.com', username='tenant@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=user.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=free_plan)
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    return user


@pytest.fixture
def user_without_tenant(django_user_model):
    return django_user_model.objects.create_user(
        email='notenant@test.com', username='notenant@test.com', password='pass1234'
    )


def test_public_path_passes_through():
    c = Client()
    response = c.get('/')
    assert response.status_code != 403


def test_auth_path_passes_through():
    c = Client()
    response = c.get('/auth/login/')
    assert response.status_code == 200


def test_authenticated_without_tenant_gets_403(user_without_tenant):
    c = Client()
    c.force_login(user_without_tenant)
    response = c.get('/dashboard/')
    assert response.status_code == 403


def test_authenticated_with_tenant_passes(user_with_tenant):
    c = Client()
    c.force_login(user_with_tenant)
    response = c.get('/dashboard/')
    assert response.status_code == 200


def test_superuser_admin_passes_without_tenant(django_user_model):
    admin = django_user_model.objects.create_superuser(
        email='admin@test.com', password='pass1234'
    )
    c = Client()
    c.force_login(admin)
    response = c.get('/admin/')
    assert response.status_code in (200, 301, 302)
