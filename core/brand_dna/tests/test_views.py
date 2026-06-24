import pytest
import json
import os
from unittest.mock import patch
from django.test import Client
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost, WeeklyFeedback

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(
        username='feedback@test.com', email='feedback@test.com', password='pass1234'
    )


def test_landing_page_redirects_to_login():
    c = Client()
    response = c.get('/')
    assert response.status_code == 302


def test_analyze_submit_creates_job(user):
    c = Client()
    c.force_login(user)
    with patch('core.brand_dna.views.django_rq'):
        response = c.post('/analizar/', {
            'business_url': 'https://tuwebmx.com',
        })
    assert response.status_code == 302
    assert AnalysisJob.objects.filter(user=user).exists()


def test_analyze_submit_enqueues_task(user):
    c = Client()
    c.force_login(user)
    with patch('core.brand_dna.views.django_rq') as mock_rq:
        c.post('/analizar/', {
            'business_url': 'https://tuwebmx.com',
        })
    mock_rq.enqueue.assert_called_once()


def test_status_api_returns_progress(user):
    c = Client()
    c.force_login(user)
    job = AnalysisJob.objects.create(
        email=user.email, business_url='https://tuwebmx.com',
        status='processing', stage='logo', progress=50, user=user,
    )
    response = c.get(f'/api/brand-dna/status/{job.id}/')
    assert response.status_code == 200
    data = json.loads(response.content)
    assert data['progress'] == 50
    assert data['stage'] == 'logo'
    assert data['status'] == 'processing'


def test_status_api_returns_brand_dna_when_done(user):
    c = Client()
    c.force_login(user)
    job = AnalysisJob.objects.create(
        email=user.email, business_url='https://tuwebmx.com',
        status='done', stage='complete', progress=100, user=user,
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


def test_results_page_returns_200(user):
    c = Client()
    c.force_login(user)
    job = AnalysisJob.objects.create(email=user.email, business_url='https://tuwebmx.com', user=user)
    response = c.get(f'/resultados/{job.id}/')
    assert response.status_code == 200


def test_status_api_requires_login():
    c = Client()
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://tuwebmx.com')
    response = c.get(f'/api/brand-dna/status/{job.id}/')
    assert response.status_code == 302


def test_results_requires_login():
    c = Client()
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://tuwebmx.com')
    response = c.get(f'/resultados/{job.id}/')
    assert response.status_code == 302


def test_status_api_blocks_other_user(user, django_user_model):
    other = django_user_model.objects.create_user(
        username='other@test.com', email='other@test.com', password='pass1234'
    )
    c = Client()
    c.force_login(other)
    job = AnalysisJob.objects.create(email=user.email, business_url='https://tuwebmx.com', user=user)
    response = c.get(f'/api/brand-dna/status/{job.id}/')
    assert response.status_code == 404


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


def test_calendar_feedback_api_no_decision_does_not_generate(client, user, job_with_calendar):
    calendar = job_with_calendar.brand_dna.calendar
    client.force_login(user)
    with patch('core.content_pipeline.tasks.generate_next_week') as mock_gen:
        response = client.post(f'/api/calendar/{job_with_calendar.id}/feedback/', {
            'rating': '3',
            'comment': 'Estuvo bien',
            'continue_decision': 'no',
        })
    assert response.status_code == 200
    data = response.json()
    assert data['continue_decision'] == 'no'

    feedback = calendar.feedback_entries.get(week_number=1)
    assert feedback.rating == 3
    assert feedback.comment == 'Estuvo bien'
    assert feedback.continue_decision == WeeklyFeedback.CONTINUE_NO
    assert feedback.responded_at is not None
    mock_gen.assert_not_called()


def test_calendar_feedback_api_yes_triggers_generate_next_week(client, user, job_with_calendar):
    calendar = job_with_calendar.brand_dna.calendar
    client.force_login(user)
    with patch('core.content_pipeline.tasks.generate_next_week') as mock_gen, \
         patch('core.brand_dna.views._update_active_product_images') as mock_update:
        response = client.post(f'/api/calendar/{job_with_calendar.id}/feedback/', {
            'rating': '5',
            'comment': '',
            'continue_decision': 'yes',
            'image_choice': 'reuse',
        })
    assert response.status_code == 200
    data = response.json()
    assert data['continue_decision'] == 'yes'

    feedback = calendar.feedback_entries.get(week_number=1)
    assert feedback.continue_decision == WeeklyFeedback.CONTINUE_YES
    mock_update.assert_called_once()
    mock_gen.assert_called_once()


def test_calendar_feedback_api_requires_ownership(client, django_user_model, job_with_calendar):
    other_user = django_user_model.objects.create_user(
        username='other@test.com', email='other@test.com', password='pass1234'
    )
    client.force_login(other_user)
    response = client.post(f'/api/calendar/{job_with_calendar.id}/feedback/', {
        'rating': '4',
        'continue_decision': 'no',
    })
    assert response.status_code == 404


def test_calendar_feedback_api_invalid_rating_returns_400(client, user, job_with_calendar):
    calendar = job_with_calendar.brand_dna.calendar
    client.force_login(user)
    with patch('core.content_pipeline.tasks.generate_next_week') as mock_gen:
        response = client.post(f'/api/calendar/{job_with_calendar.id}/feedback/', {
            'rating': 'not-a-number',
            'comment': 'Estuvo bien',
            'continue_decision': 'no',
        })
    assert response.status_code == 400
    data = response.json()
    assert 'error' in data

    feedback = calendar.feedback_entries.get(week_number=1)
    assert feedback.continue_decision == WeeklyFeedback.CONTINUE_PENDING
    mock_gen.assert_not_called()


def test_calendar_feedback_api_invalid_continue_decision_returns_400(client, user, job_with_calendar):
    calendar = job_with_calendar.brand_dna.calendar
    client.force_login(user)
    with patch('core.content_pipeline.tasks.generate_next_week') as mock_gen:
        response = client.post(f'/api/calendar/{job_with_calendar.id}/feedback/', {
            'rating': '5',
            'comment': 'Estuvo bien',
            'continue_decision': 'maybe',
        })
    assert response.status_code == 400
    data = response.json()
    assert 'error' in data

    feedback = calendar.feedback_entries.get(week_number=1)
    assert feedback.continue_decision == WeeklyFeedback.CONTINUE_PENDING
    mock_gen.assert_not_called()


def test_update_active_product_images_reuse_pool_le_7(job_with_calendar):
    from core.brand_dna.views import _update_active_product_images
    from django.test import RequestFactory

    job = job_with_calendar
    job.product_image_paths = ['uploads/p1.jpg', 'uploads/p2.jpg']
    job.save(update_fields=['product_image_paths'])
    calendar = job.brand_dna.calendar
    calendar.active_product_images = ['uploads/p1.jpg', 'uploads/p2.jpg']
    calendar.save(update_fields=['active_product_images'])

    request = RequestFactory().post('/', {'image_choice': 'reuse'})
    _update_active_product_images(calendar, job, request, next_week=2)

    calendar.refresh_from_db()
    assert calendar.active_product_images == ['uploads/p1.jpg', 'uploads/p2.jpg']


def test_update_active_product_images_reuse_pool_gt_7_with_selection(job_with_calendar):
    from core.brand_dna.views import _update_active_product_images
    from django.test import RequestFactory

    job = job_with_calendar
    pool = [f'uploads/p{i}.jpg' for i in range(1, 9)]  # 8 imágenes
    job.product_image_paths = pool
    job.save(update_fields=['product_image_paths'])
    calendar = job.brand_dna.calendar

    selected = pool[:5]
    request = RequestFactory().post('/', {
        'image_choice': 'reuse',
        'selected_images': selected,
    })
    _update_active_product_images(calendar, job, request, next_week=2)

    calendar.refresh_from_db()
    assert calendar.active_product_images == selected


def test_update_active_product_images_new_uploads(job_with_calendar, tmp_path, settings):
    from core.brand_dna.views import _update_active_product_images
    from django.test import RequestFactory
    from django.core.files.uploadedfile import SimpleUploadedFile

    settings.MEDIA_ROOT = str(tmp_path)
    job = job_with_calendar
    calendar = job.brand_dna.calendar

    image1 = SimpleUploadedFile('product1.jpg', b'fake-bytes-1', content_type='image/jpeg')
    image2 = SimpleUploadedFile('product2.png', b'fake-bytes-2', content_type='image/png')

    request = RequestFactory().post('/', {
        'image_choice': 'new',
        'product_images': [image1, image2],
    })
    _update_active_product_images(calendar, job, request, next_week=2)

    job.refresh_from_db()
    calendar.refresh_from_db()

    assert len(job.product_image_paths) == 2
    assert calendar.active_product_images == job.product_image_paths
    for path in calendar.active_product_images:
        full = os.path.join(settings.MEDIA_ROOT, path)
        assert os.path.exists(full)
