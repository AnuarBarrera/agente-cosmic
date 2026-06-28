import pytest
from unittest.mock import patch, MagicMock
from datetime import timedelta
from django.utils import timezone
from django.core.management import call_command
from core.tenant_management.models import TenantModel, Plan, Subscription
from core.brand_dna.models import AnalysisJob

pytestmark = pytest.mark.django_db


@pytest.fixture
def free_plan():
    plan, _ = Plan.objects.get_or_create(name='User', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    return plan


@pytest.fixture
def deactivated_user_old(django_user_model, free_plan):
    u = django_user_model.objects.create_user(
        email='old@test.com', username='old@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=u.email, status='deactivated')
    Subscription.objects.create(tenant=tenant, plan=free_plan, status='canceled')
    u.tenant = tenant
    u.is_active = False
    u.deactivated_at = timezone.now() - timedelta(days=31)
    u.save(update_fields=['tenant', 'is_active', 'deactivated_at'])
    return u


@pytest.fixture
def deactivated_user_recent(django_user_model, free_plan):
    u = django_user_model.objects.create_user(
        email='recent@test.com', username='recent@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=u.email, status='deactivated')
    Subscription.objects.create(tenant=tenant, plan=free_plan, status='canceled')
    u.tenant = tenant
    u.is_active = False
    u.deactivated_at = timezone.now() - timedelta(days=10)
    u.save(update_fields=['tenant', 'is_active', 'deactivated_at'])
    return u


def test_cleanup_deletes_old_user_images(deactivated_user_old):
    job = AnalysisJob.objects.create(
        email=deactivated_user_old.email, business_url='https://test.com',
        user=deactivated_user_old, status='done',
        logo_file_path='uploads/logo_test.jpg',
        product_image_paths=['uploads/p1.jpg', 'uploads/p2.jpg'],
    )

    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_bucket.list_blobs.return_value = [mock_blob]

    with patch('core.tenant_management.management.commands.cleanup_deactivated_images.storage.Client') as mock_client:
        mock_client.return_value.bucket.return_value = mock_bucket
        call_command('cleanup_deactivated_images')

    job.refresh_from_db()
    assert job.logo_file_path == ''
    assert job.product_image_paths == []


def test_cleanup_skips_recent_deactivation(deactivated_user_recent):
    job = AnalysisJob.objects.create(
        email=deactivated_user_recent.email, business_url='https://test.com',
        user=deactivated_user_recent, status='done',
        logo_file_path='uploads/logo_test.jpg',
        product_image_paths=['uploads/p1.jpg'],
    )

    with patch('core.tenant_management.management.commands.cleanup_deactivated_images.storage.Client'):
        call_command('cleanup_deactivated_images')

    job.refresh_from_db()
    assert job.logo_file_path == 'uploads/logo_test.jpg'
    assert job.product_image_paths == ['uploads/p1.jpg']
