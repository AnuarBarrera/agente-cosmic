import pytest
from unittest.mock import patch
from django.test import Client
from core.tenant_management.models import TenantModel, Subscription, Plan

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


def test_provision_tenant_creates_tenant_and_subscription(free_plan, django_user_model):
    from core.brand_dna.auth_views import provision_tenant

    user = django_user_model.objects.create_user(
        email='new@test.com', username='new@test.com', password='pass1234'
    )
    tenant = provision_tenant(user)

    assert tenant is not None
    assert tenant.name == 'new@test.com'
    assert tenant.status == 'active'
    user.refresh_from_db()
    assert user.tenant == tenant
    assert Subscription.objects.filter(tenant=tenant, plan=free_plan).exists()


def test_provision_tenant_is_idempotent(free_plan, django_user_model):
    from core.brand_dna.auth_views import provision_tenant

    user = django_user_model.objects.create_user(
        email='existing@test.com', username='existing@test.com', password='pass1234'
    )
    tenant1 = provision_tenant(user)
    tenant2 = provision_tenant(user)

    assert tenant1.pk == tenant2.pk
    assert TenantModel.objects.filter(name='existing@test.com').count() == 1


def test_verify_email_creates_tenant(free_plan, django_user_model):
    from core.tenant_management.models import EmailVerificationToken
    from django.contrib.auth.hashers import make_password

    token = EmailVerificationToken.objects.create(
        email='verify@test.com',
        tenant_name='',
        user_data={'password': make_password('TestPass123!'), 'invitation_code': ''},
    )
    c = Client()
    response = c.get(f'/auth/verify/{token.token}/')

    user = django_user_model.objects.get(email='verify@test.com')
    assert user.tenant is not None
    assert Subscription.objects.filter(tenant=user.tenant).exists()


def test_analysis_job_allows_empty_url_with_description(free_plan, django_user_model):
    from core.brand_dna.models import AnalysisJob
    from core.brand_dna.auth_views import provision_tenant
    user = django_user_model.objects.create_user(
        email='nourl@test.com', username='nourl@test.com', password='pass1234'
    )
    provision_tenant(user)
    job = AnalysisJob.objects.create(
        email=user.email,
        business_url='',
        business_description='Vendo tamales oaxaqueños en el mercado de Coyoacán',
        user=user,
    )
    assert job.business_description == 'Vendo tamales oaxaqueños en el mercado de Coyoacán'
    assert job.business_url == ''


def test_user_has_deactivated_at_field(free_plan, django_user_model):
    from core.brand_dna.auth_views import provision_tenant
    user = django_user_model.objects.create_user(
        email='deact@test.com', username='deact@test.com', password='pass1234'
    )
    provision_tenant(user)
    assert user.deactivated_at is None
    from django.utils import timezone
    user.deactivated_at = timezone.now()
    user.save(update_fields=['deactivated_at'])
    user.refresh_from_db()
    assert user.deactivated_at is not None
