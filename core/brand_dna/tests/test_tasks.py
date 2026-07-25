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


@pytest.fixture
def pending_job():
    return AnalysisJob.objects.create(
        email='test@example.com',
        business_url='https://tuwebmx.com',
        business_description='Tu Web MX\nAgencia digital que hace sitios web.',
    )


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
         patch('core.brand_dna.tasks.ManualBrandExtractor') as MockExtractor, \
         patch('core.brand_dna.tasks.LogoAnalyzer') as MockLogo, \
         patch('core.brand_dna.tasks.django_rq') as mock_rq:
        MockScraper.return_value.fetch_context.return_value = ('texto del sitio', ['#123456'])
        MockExtractor.return_value.extract.return_value = _WEB_RESULT
        MockLogo.return_value.analyze.return_value = _LOGO_RESULT

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
         patch('core.brand_dna.tasks.ManualBrandExtractor') as MockExtractor, \
         patch('core.brand_dna.tasks.LogoAnalyzer') as MockLogo, \
         patch('core.brand_dna.tasks.django_rq') as mock_rq:
        MockScraper.return_value.fetch_context.return_value = ('texto del sitio', ['#123456'])
        MockExtractor.return_value.extract.return_value = _WEB_RESULT
        MockLogo.return_value.analyze.return_value = _LOGO_RESULT

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
def test_task_enqueues_content_generation_for_full_mode(pending_job):
    pending_job.generation_mode = AnalysisJob.MODE_FULL
    pending_job.save(update_fields=['generation_mode'])
    with patch('core.brand_dna.tasks.WebScraper') as MockScraper, \
         patch('core.brand_dna.tasks.ManualBrandExtractor') as MockExtractor, \
         patch('core.brand_dna.tasks.LogoAnalyzer') as MockLogo, \
         patch('core.brand_dna.tasks.django_rq') as mock_rq:
        MockScraper.return_value.fetch_context.return_value = ('texto del sitio', ['#123456'])
        MockExtractor.return_value.extract.return_value = _WEB_RESULT
        MockLogo.return_value.analyze.return_value = _LOGO_RESULT

        from core.brand_dna.tasks import analyze_brand_task
        analyze_brand_task(str(pending_job.id))

    from core.content_pipeline.tasks import content_generation_task
    assert mock_rq.enqueue.call_args.args[0] is content_generation_task
    assert mock_rq.enqueue.call_args.kwargs['job_timeout'] == 300


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_task_enqueues_sample_generation_for_sample_mode(pending_job):
    pending_job.generation_mode = AnalysisJob.MODE_SAMPLE_REEL
    pending_job.save(update_fields=['generation_mode'])
    with patch('core.brand_dna.tasks.WebScraper') as MockScraper, \
         patch('core.brand_dna.tasks.ManualBrandExtractor') as MockExtractor, \
         patch('core.brand_dna.tasks.LogoAnalyzer') as MockLogo, \
         patch('core.brand_dna.tasks.django_rq') as mock_rq:
        MockScraper.return_value.fetch_context.return_value = ('texto del sitio', ['#123456'])
        MockExtractor.return_value.extract.return_value = _WEB_RESULT
        MockLogo.return_value.analyze.return_value = _LOGO_RESULT

        from core.brand_dna.tasks import analyze_brand_task
        analyze_brand_task(str(pending_job.id))

    from core.content_pipeline.tasks import generate_sample_task
    assert mock_rq.enqueue.call_args.args[0] is generate_sample_task
    assert mock_rq.enqueue.call_args.kwargs['job_timeout'] == 2400


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
         patch('core.brand_dna.tasks.ManualBrandExtractor') as MockExtractor, \
         patch('core.brand_dna.tasks.LogoAnalyzer'), \
         patch('core.brand_dna.tasks.django_rq'):
        MockScraper.return_value.fetch_context.return_value = ('texto del sitio', [])
        MockExtractor.return_value.extract.side_effect = Exception('Fatal error')

        from core.brand_dna.tasks import analyze_brand_task
        analyze_brand_task(str(pending_job.id))

    pending_job.refresh_from_db()
    assert pending_job.status == AnalysisJob.STATUS_FAILED
    assert 'Fatal error' in pending_job.error_message


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_task_continues_when_scraping_fails(pending_job):
    with patch('core.brand_dna.tasks.WebScraper') as MockScraper, \
         patch('core.brand_dna.tasks.ManualBrandExtractor') as MockExtractor, \
         patch('core.brand_dna.tasks.LogoAnalyzer') as MockLogo, \
         patch('core.brand_dna.tasks.django_rq'):
        MockScraper.return_value.fetch_context.side_effect = Exception('Sitio caido')
        MockExtractor.return_value.extract.return_value = _WEB_RESULT
        MockLogo.return_value.analyze.return_value = _LOGO_RESULT

        from core.brand_dna.tasks import analyze_brand_task
        analyze_brand_task(str(pending_job.id))

    pending_job.refresh_from_db()
    assert pending_job.status == AnalysisJob.STATUS_PROCESSING
    assert BrandDNA.objects.filter(job=pending_job).exists()
    MockExtractor.return_value.extract.assert_called_once_with(
        business_name='Tu Web MX',
        description=pending_job.business_description,
        scraped_context='',
        scraped_colors=[],
    )
