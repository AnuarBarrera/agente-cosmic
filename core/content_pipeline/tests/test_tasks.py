import pytest
from unittest.mock import patch
from django.test import override_settings
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost

pytestmark = pytest.mark.django_db

_MOCK_POSTS = [
    {'caption': f'Post {i}', 'hashtags': ['#test'], 'suggested_time': '19:00'}
    for i in range(1, 8)
]


@pytest.fixture
def job_with_dna():
    job = AnalysisJob.objects.create(
        email='t@t.com', business_url='https://tuwebmx.com',
        status=AnalysisJob.STATUS_PROCESSING, stage=AnalysisJob.STAGE_CONTENT, progress=78,
    )
    BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    return job


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    DEFAULT_FROM_EMAIL='noreply@test.com',
)
def test_content_generation_creates_calendar(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender') as MockEmail, \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    assert ContentCalendar.objects.filter(brand_dna__job=job_with_dna).exists()
    assert ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna).count() == 7


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    DEFAULT_FROM_EMAIL='noreply@test.com',
)
def test_content_generation_marks_job_done(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    job_with_dna.refresh_from_db()
    assert job_with_dna.status == AnalysisJob.STATUS_DONE
    assert job_with_dna.progress == 100
