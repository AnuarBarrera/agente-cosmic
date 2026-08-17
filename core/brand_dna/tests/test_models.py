import pytest
from core.brand_dna.models import AnalysisJob, BrandDNA

pytestmark = pytest.mark.django_db


def test_analysis_job_creation():
    job = AnalysisJob.objects.create(
        email='test@example.com',
        business_url='https://tuwebmx.com',
    )
    assert job.status == 'pending'
    assert job.progress == 0
    assert job.stage == 'web'
    assert str(job.id) != ''


def test_analysis_job_product_reference_image_path_defaults_to_empty():
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://tuwebmx.com')
    assert job.product_reference_image_path == ''



def test_brand_dna_creation(analysis_job):
    dna = BrandDNA.objects.create(
        job=analysis_job,
        business_name='Tu Web MX',
        business_url='https://tuwebmx.com',
        description='Agencia de diseño web',
        keywords=['diseño', 'web', 'digital'],
        audience='Empresas medianas en México',
        tone='profesional',
        primary_colors=['#1A1A2E', '#E94560'],
        logo_elements='Tipografía moderna, colores contrastantes',
        posting_style='Posts cortos con call to action',
        avg_caption_length=120,
        common_hashtags=['#diseñoweb', '#agenciadigital'],
    )
    assert dna.business_name == 'Tu Web MX'
    assert '#1A1A2E' in dna.primary_colors


def test_analysis_job_progress_update(analysis_job):
    analysis_job.update_progress(stage='logo', progress=50)
    refreshed = AnalysisJob.objects.get(id=analysis_job.id)
    assert refreshed.progress == 50
    assert refreshed.stage == 'logo'


def test_product_photo_precheck_attempt_creation():
    from django.contrib.auth import get_user_model
    from core.brand_dna.models import ProductPhotoPrecheckAttempt
    User = get_user_model()
    user = User.objects.create_user(
        username='precheck@test.com', email='precheck@test.com', password='pass1234',
    )
    attempt = ProductPhotoPrecheckAttempt.objects.create(user=user)
    assert attempt.user == user
    assert attempt.created_at is not None


@pytest.fixture
def analysis_job():
    return AnalysisJob.objects.create(
        email='test@example.com',
        business_url='https://tuwebmx.com',
    )
