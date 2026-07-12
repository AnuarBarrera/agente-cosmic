import pytest
import json
import os
from unittest.mock import patch, MagicMock
from django.test import Client
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost, WeeklyFeedback

pytestmark = pytest.mark.django_db


@pytest.fixture
def free_plan():
    from core.tenant_management.models import Plan
    plan, _ = Plan.objects.get_or_create(name='User', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    return plan


@pytest.fixture
def user(django_user_model, free_plan):
    from core.tenant_management.models import TenantModel, Subscription
    u = django_user_model.objects.create_user(
        username='feedback@test.com', email='feedback@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=u.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=free_plan)
    u.tenant = tenant
    u.save(update_fields=['tenant'])
    return u


def test_home_page_shows_marketing_for_anonymous_visitor():
    c = Client()
    response = c.get('/')
    assert response.status_code == 200
    assert b'Reg\xc3\xadstrate gratis' in response.content


def test_home_page_redirects_authenticated_users_to_dashboard(user):
    c = Client()
    c.force_login(user)
    response = c.get('/')
    assert response.status_code == 302
    assert response.url == '/dashboard/'


def test_new_analysis_requires_login():
    c = Client()
    response = c.get('/nuevo-analisis/')
    assert response.status_code == 302
    assert '/auth/login/' in response.url


def test_new_analysis_without_screenshots_hides_gallery(user):
    c = Client()
    c.force_login(user)
    with patch('core.brand_dna.views._screenshots_context', return_value={'has_app_screenshots': False, 'screenshots_version': 0}):
        response = c.get('/nuevo-analisis/')
    assert response.status_code == 200
    assert response.context['has_app_screenshots'] is False
    assert b'screenshots/dashboard.png' not in response.content


def test_analyze_submit_creates_job(user):
    c = Client()
    c.force_login(user)
    with patch('core.brand_dna.views.django_rq'):
        response = c.post('/analizar/', {
            'business_name': 'Tu Web MX',
            'business_description': 'Agencia digital que hace sitios web.',
            'business_url': 'https://tuwebmx.com',
        })
    assert response.status_code == 302
    assert AnalysisJob.objects.filter(user=user).exists()


def test_analyze_submit_without_url_with_description(user):
    c = Client()
    c.force_login(user)
    with patch('core.brand_dna.views.django_rq'):
        response = c.post('/analizar/', {
            'business_name': 'Tamales Doña Lupita',
            'business_description': 'Vendo tamales oaxaqueños en el mercado',
        })
    assert response.status_code == 302
    job = AnalysisJob.objects.filter(user=user).latest('created_at')
    assert job.business_url == ''
    assert 'tamales' in job.business_description


def test_analyze_submit_without_name_or_description_shows_error(user):
    c = Client()
    c.force_login(user)
    response = c.post('/analizar/', {})
    assert response.status_code == 200


def test_analyze_submit_enqueues_task(user):
    c = Client()
    c.force_login(user)
    with patch('core.brand_dna.views.django_rq') as mock_rq:
        c.post('/analizar/', {
            'business_name': 'Tu Web MX',
            'business_description': 'Agencia digital que hace sitios web.',
            'business_url': 'https://tuwebmx.com',
        })
    mock_rq.enqueue.assert_called_once()


def test_analyze_submit_rejects_more_than_7_product_images(user):
    from django.core.files.uploadedfile import SimpleUploadedFile
    c = Client()
    c.force_login(user)
    images = [SimpleUploadedFile(f'p{i}.jpg', b'fake-bytes', content_type='image/jpeg') for i in range(8)]
    with patch('core.brand_dna.views.django_rq') as mock_rq:
        response = c.post('/analizar/', {
            'business_name': 'Tu Web MX',
            'business_description': 'Agencia digital que hace sitios web.',
            'product_images': images,
        })
    assert response.status_code == 200
    assert b'm\xc3\xa1ximo es 7' in response.content
    mock_rq.enqueue.assert_not_called()
    assert not AnalysisJob.objects.filter(user=user).exists()


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


def test_status_api_blocks_other_user(user, django_user_model, free_plan):
    from core.tenant_management.models import TenantModel, Subscription
    other = django_user_model.objects.create_user(
        username='other@test.com', email='other@test.com', password='pass1234'
    )
    t = TenantModel.objects.create(name=other.email, status='active')
    Subscription.objects.create(tenant=t, plan=free_plan)
    other.tenant = t
    other.save(update_fields=['tenant'])
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


def test_mark_published_sets_timestamp(client, user, job_with_calendar):
    post = job_with_calendar.brand_dna.calendar.posts.get(day_number=1)
    client.force_login(user)
    response = client.post(
        f'/api/post/{post.id}/action/',
        data=json.dumps({'action': 'mark_published'}),
        content_type='application/json',
    )
    assert response.status_code == 200
    post.refresh_from_db()
    assert post.published_at is not None


def test_mark_published_is_idempotent(client, user, job_with_calendar):
    post = job_with_calendar.brand_dna.calendar.posts.get(day_number=1)
    client.force_login(user)
    client.post(
        f'/api/post/{post.id}/action/',
        data=json.dumps({'action': 'mark_published'}),
        content_type='application/json',
    )
    post.refresh_from_db()
    first_timestamp = post.published_at

    client.post(
        f'/api/post/{post.id}/action/',
        data=json.dumps({'action': 'mark_published'}),
        content_type='application/json',
    )
    post.refresh_from_db()
    assert post.published_at == first_timestamp


def test_download_post_image_returns_attachment(client, user, job_with_calendar):
    post = job_with_calendar.brand_dna.calendar.posts.get(day_number=1)
    client.force_login(user)
    fake_response = MagicMock()
    fake_response.read.return_value = b'fake-image-bytes'
    fake_response.__enter__.return_value = fake_response
    with patch('urllib.request.urlopen', return_value=fake_response):
        response = client.get(f'/api/post/{post.id}/download/')
    assert response.status_code == 200
    assert response['Content-Type'] == 'image/png'
    assert 'attachment' in response['Content-Disposition']
    assert response.content == b'fake-image-bytes'


def test_download_post_image_blocks_other_user(client, django_user_model, job_with_calendar, free_plan):
    from core.tenant_management.models import TenantModel, Subscription
    post = job_with_calendar.brand_dna.calendar.posts.get(day_number=1)
    other = django_user_model.objects.create_user(
        username='other@test.com', email='other@test.com', password='pass1234'
    )
    t = TenantModel.objects.create(name=other.email, status='active')
    Subscription.objects.create(tenant=t, plan=free_plan)
    other.tenant = t
    other.save(update_fields=['tenant'])
    client.force_login(other)
    response = client.get(f'/api/post/{post.id}/download/')
    assert response.status_code == 404


def test_mark_downloaded_sets_timestamp(client, user, job_with_calendar):
    post = job_with_calendar.brand_dna.calendar.posts.get(day_number=1)
    client.force_login(user)
    response = client.post(
        f'/api/post/{post.id}/action/',
        data=json.dumps({'action': 'mark_downloaded'}),
        content_type='application/json',
    )
    assert response.status_code == 200
    post.refresh_from_db()
    assert post.downloaded_at is not None


def test_calendar_feedback_api_no_rating_is_valid(client, user, job_with_calendar):
    calendar = job_with_calendar.brand_dna.calendar
    client.force_login(user)
    with patch('core.content_pipeline.tasks.generate_next_week') as mock_gen:
        response = client.post(f'/api/calendar/{job_with_calendar.id}/feedback/', {
            'comment': 'Estuvo bien',
            'continue_decision': 'no',
        })
    assert response.status_code == 200
    data = response.json()
    assert data['continue_decision'] == 'no'

    feedback = calendar.feedback_entries.get(week_number=1)
    assert feedback.rating is None
    assert feedback.comment == 'Estuvo bien'


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


def test_calendar_review_groups_posts_by_week(client, user, job_with_calendar):
    calendar = job_with_calendar.brand_dna.calendar
    for i in range(8, 15):
        ContentPost.objects.create(
            calendar=calendar, day_number=i, caption=f'Post {i}',
            image_url='https://example.com/img.jpg', suggested_time='19:00',
            hashtags=[], scheduled_at=timezone.now() + timedelta(days=i),
        )
    client.force_login(user)
    response = client.get(f'/calendar/{job_with_calendar.id}/')

    week_groups = response.context['week_groups']
    assert [w['week_number'] for w in week_groups] == [2, 1]
    assert week_groups[0]['is_current'] is True
    assert week_groups[1]['is_current'] is False
    assert len(week_groups[0]['posts']) == 7
    assert len(week_groups[1]['posts']) == 7


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
    with patch('core.brand_dna.views.django_rq') as mock_rq, \
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
    mock_rq.enqueue.assert_called_once()
    enqueue_args = mock_rq.enqueue.call_args[0]
    assert enqueue_args[1] == str(calendar.id)
    assert enqueue_args[2] == 2

    calendar.refresh_from_db()
    assert calendar.next_week_generating is True


def test_calendar_feedback_api_requires_ownership(client, django_user_model, job_with_calendar, free_plan):
    from core.tenant_management.models import TenantModel, Subscription
    other_user = django_user_model.objects.create_user(
        username='other@test.com', email='other@test.com', password='pass1234'
    )
    t = TenantModel.objects.create(name=other_user.email, status='active')
    Subscription.objects.create(tenant=t, plan=free_plan)
    other_user.tenant = t
    other_user.save(update_fields=['tenant'])
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


def test_calendar_feedback_api_rejects_more_than_7_new_images(client, user, job_with_calendar):
    from django.core.files.uploadedfile import SimpleUploadedFile
    calendar = job_with_calendar.brand_dna.calendar
    client.force_login(user)
    images = [SimpleUploadedFile(f'p{i}.jpg', b'fake-bytes', content_type='image/jpeg') for i in range(8)]
    with patch('core.brand_dna.views.django_rq') as mock_rq:
        response = client.post(f'/api/calendar/{job_with_calendar.id}/feedback/', {
            'rating': '5',
            'comment': '',
            'continue_decision': 'yes',
            'image_choice': 'new',
            'product_images': images,
        })
    assert response.status_code == 400
    data = response.json()
    assert 'máximo es 7' in data['error']
    mock_rq.enqueue.assert_not_called()

    feedback = calendar.feedback_entries.get(week_number=1)
    assert feedback.continue_decision == WeeklyFeedback.CONTINUE_PENDING


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


def test_privacy_policy_accessible_without_login():
    c = Client()
    response = c.get('/privacidad/')
    assert response.status_code == 200


def test_terms_of_service_accessible_without_login():
    c = Client()
    response = c.get('/terminos/')
    assert response.status_code == 200


def test_ga4_tag_renders_when_measurement_id_configured(settings):
    settings.GA4_MEASUREMENT_ID = 'G-TESTID123'
    c = Client()
    response = c.get('/privacidad/')
    assert b'gtag' in response.content
    assert b'G-TESTID123' in response.content


def test_ga4_tag_absent_when_measurement_id_empty(settings):
    settings.GA4_MEASUREMENT_ID = ''
    c = Client()
    response = c.get('/privacidad/')
    assert b'gtag' not in response.content
