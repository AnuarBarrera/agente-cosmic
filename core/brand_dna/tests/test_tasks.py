import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings
from core.brand_dna.models import AnalysisJob, BrandDNA

pytestmark = pytest.mark.django_db

_WEB_RESULT = {
    'business_name': 'Tu Web MX', 'description': 'Agencia digital',
    'keywords': ['diseno'], 'audience': 'PYMEs', 'tone': 'profesional',
}
_LOGO_RESULT = {'primary_colors': ['#1a1a2e'], 'logo_elements': 'Tipografia moderna'}
_POSTS_RESULT = {'posting_style': 'Directo', 'avg_caption_length': 120, 'common_hashtags': []}


@pytest.fixture
def pending_job():
    return AnalysisJob.objects.create(email='test@example.com', business_url='https://tuwebmx.com')


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_task_creates_brand_dna(pending_job):
    with patch('core.brand_dna.tasks.WebScraper') as MockScraper, \
         patch('core.brand_dna.tasks.LogoAnalyzer') as MockLogo, \
         patch('core.brand_dna.tasks.PostsAnalyzer') as MockPosts, \
         patch('core.brand_dna.tasks.django_rq') as mock_rq:
        MockScraper.return_value.extract.return_value = _WEB_RESULT
        MockLogo.return_value.analyze.return_value = _LOGO_RESULT
        MockPosts.return_value.analyze.return_value = _POSTS_RESULT

        from core.brand_dna.tasks import analyze_brand_task
        analyze_brand_task(str(pending_job.id))

    pending_job.refresh_from_db()
    assert pending_job.status == AnalysisJob.STATUS_PROCESSING
    assert BrandDNA.objects.filter(job=pending_job).exists()
    dna = BrandDNA.objects.get(job=pending_job)
    assert dna.business_name == 'Tu Web MX'
    assert dna.tone == 'profesional'


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_task_enqueues_content_generation(pending_job):
    with patch('core.brand_dna.tasks.WebScraper') as MockScraper, \
         patch('core.brand_dna.tasks.LogoAnalyzer') as MockLogo, \
         patch('core.brand_dna.tasks.PostsAnalyzer') as MockPosts, \
         patch('core.brand_dna.tasks.django_rq') as mock_rq:
        MockScraper.return_value.extract.return_value = _WEB_RESULT
        MockLogo.return_value.analyze.return_value = _LOGO_RESULT
        MockPosts.return_value.analyze.return_value = _POSTS_RESULT

        from core.brand_dna.tasks import analyze_brand_task
        analyze_brand_task(str(pending_job.id))

    mock_rq.enqueue.assert_called_once()


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_task_marks_failed_on_error(pending_job):
    with patch('core.brand_dna.tasks.WebScraper') as MockScraper, \
         patch('core.brand_dna.tasks.LogoAnalyzer'), \
         patch('core.brand_dna.tasks.PostsAnalyzer'), \
         patch('core.brand_dna.tasks.django_rq'):
        MockScraper.return_value.extract.side_effect = Exception('Fatal error')

        from core.brand_dna.tasks import analyze_brand_task
        analyze_brand_task(str(pending_job.id))

    pending_job.refresh_from_db()
    assert pending_job.status == AnalysisJob.STATUS_FAILED
    assert 'Fatal error' in pending_job.error_message
