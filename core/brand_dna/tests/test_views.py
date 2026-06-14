import pytest
import json
from unittest.mock import patch
from django.test import Client
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost, WeeklyFeedback

pytestmark = pytest.mark.django_db


def test_landing_page_returns_200():
    c = Client()
    response = c.get('/')
    assert response.status_code == 200


def test_analyze_submit_creates_job():
    c = Client()
    with patch('core.brand_dna.views.django_rq'):
        response = c.post('/analizar/', {
            'email': 'test@example.com',
            'business_url': 'https://tuwebmx.com',
        })
    assert response.status_code == 302
    assert AnalysisJob.objects.filter(email='test@example.com').exists()


def test_analyze_submit_enqueues_task():
    c = Client()
    with patch('core.brand_dna.views.django_rq') as mock_rq:
        c.post('/analizar/', {
            'email': 'test@example.com',
            'business_url': 'https://tuwebmx.com',
        })
    mock_rq.enqueue.assert_called_once()


def test_status_api_returns_progress():
    c = Client()
    job = AnalysisJob.objects.create(
        email='t@t.com', business_url='https://tuwebmx.com',
        status='processing', stage='logo', progress=50,
    )
    response = c.get(f'/api/brand-dna/status/{job.id}/')
    assert response.status_code == 200
    data = json.loads(response.content)
    assert data['progress'] == 50
    assert data['stage'] == 'logo'
    assert data['status'] == 'processing'


def test_status_api_returns_brand_dna_when_done():
    c = Client()
    job = AnalysisJob.objects.create(
        email='t@t.com', business_url='https://tuwebmx.com',
        status='done', stage='complete', progress=100,
    )
    BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    response = c.get(f'/api/brand-dna/status/{job.id}/')
    data = json.loads(response.content)
    assert data['brand_dna'] is not None
    assert data['brand_dna']['business_name'] == 'Tu Web MX'


def test_results_page_returns_200():
    c = Client()
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://tuwebmx.com')
    response = c.get(f'/resultados/{job.id}/')
    assert response.status_code == 200


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(
        username='feedback@test.com', email='feedback@test.com', password='pass1234'
    )


@pytest.fixture
def job_with_calendar(user):
    job = AnalysisJob.objects.create(
        email=user.email, business_url='https://tuwebmx.com', user=user,
        status=AnalysisJob.STATUS_DONE, stage=AnalysisJob.STAGE_COMPLETE, progress=100,
    )
    dna = BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    calendar = ContentCalendar.objects.create(brand_dna=dna)
    for i in range(1, 8):
        ContentPost.objects.create(
            calendar=calendar, day_number=i, caption=f'Post {i}',
            image_url='https://example.com/img.jpg', suggested_time='19:00',
            hashtags=[], scheduled_at=timezone.now() + timedelta(days=i),
        )
    WeeklyFeedback.objects.create(calendar=calendar, week_number=1)
    return job


def test_calendar_review_exposes_pending_feedback(client, user, job_with_calendar):
    client.force_login(user)
    response = client.get(f'/calendar/{job_with_calendar.id}/')
    assert response.status_code == 200
    assert response.context['pending_feedback'] is not None
    assert response.context['pending_feedback'].week_number == 1


def test_calendar_review_no_pending_feedback_when_none_exists(client, user, job_with_calendar):
    calendar = job_with_calendar.brand_dna.calendar
    calendar.feedback_entries.update(continue_decision=WeeklyFeedback.CONTINUE_NO)
    client.force_login(user)
    response = client.get(f'/calendar/{job_with_calendar.id}/')
    assert response.context['pending_feedback'] is None


def test_calendar_review_shows_feedback_banner_when_pending(client, user, job_with_calendar):
    client.force_login(user)
    response = client.get(f'/calendar/{job_with_calendar.id}/')
    assert b'feedback-banner' in response.content
