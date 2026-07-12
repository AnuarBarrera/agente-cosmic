import pytest
from django.core.management import call_command, CommandError
from core.brand_dna.models import AnalysisJob

pytestmark = pytest.mark.django_db


def test_requires_credentials(monkeypatch):
    monkeypatch.delenv('DEMO_ACCOUNT_EMAIL', raising=False)
    monkeypatch.delenv('DEMO_ACCOUNT_PASSWORD', raising=False)
    with pytest.raises(CommandError, match='credenciales'):
        call_command('capture_landing_screenshots')


def test_requires_existing_business_job():
    with pytest.raises(CommandError, match='No hay un AnalysisJob completado'):
        call_command(
            'capture_landing_screenshots',
            '--email', 'demo@example.com',
            '--password', 'whatever',
            '--business-name', 'Negocio que no existe',
        )


def test_ignores_soft_deleted_jobs(django_user_model):
    from django.utils import timezone
    from core.brand_dna.models import BrandDNA

    user = django_user_model.objects.create_user(email='demo@example.com', password='pw')
    job = AnalysisJob.objects.create(
        email=user.email, business_url='https://tuwebmx.com', user=user,
        status=AnalysisJob.STATUS_DONE, deleted_at=timezone.now(),
    )
    BrandDNA.objects.create(job=job, business_name='Tu Web MX')

    with pytest.raises(CommandError, match='No hay un AnalysisJob completado'):
        call_command(
            'capture_landing_screenshots',
            '--email', 'demo@example.com',
            '--password', 'pw',
            '--business-name', 'Tu Web MX',
        )
