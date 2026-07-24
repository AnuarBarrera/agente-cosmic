import pytest
from unittest.mock import patch
from django.test import override_settings
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost

pytestmark = pytest.mark.django_db


@pytest.fixture
def full_setup():
    job = AnalysisJob.objects.create(email='cliente@ejemplo.com', business_url='https://tuwebmx.com')
    dna = BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    calendar = ContentCalendar.objects.create(brand_dna=dna)
    posts = []
    for i in range(1, 8):
        posts.append(ContentPost.objects.create(
            calendar=calendar, day_number=i,
            caption=f'Post del dia {i}',
            image_url=f'https://storage.googleapis.com/bucket/img{i}.jpg',
            suggested_time='19:00',
            hashtags=['#disenoweb'],
            scheduled_at=timezone.now() + timedelta(days=i),
        ))
    return job, dna, calendar, posts


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_initial_email_calls_django_send(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_initial(job=job, brand_dna=dna)
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert job.email in call_kwargs[1]['recipient_list']


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_initial_email_subject_contains_business_name(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_initial(job=job, brand_dna=dna)
    subject = mock_send.call_args[0][0]
    assert 'Tu Web MX' in subject


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_daily_email_marks_post_sent(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    post = posts[0]
    with patch('core.content_pipeline.email_sender.send_mail'):
        sender = EmailSender()
        sender.send_daily(post=post)
    post.refresh_from_db()
    assert post.status == ContentPost.STATUS_SENT
    assert post.sent_at is not None


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_daily_email_weekend_cta_on_day_7(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    post = posts[6]  # day_number == 7
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_daily(post=post)
    html = mock_send.call_args[1]['html_message']
    assert 'Dar feedback y ver mi próxima semana' in html
    assert 'https://cosmic.anuarbarrera.dev' in html


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_daily_email_no_weekend_cta_on_other_days(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    post = posts[1]  # day_number == 2
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_daily(post=post)
    html = mock_send.call_args[1]['html_message']
    assert 'Dar feedback y ver mi próxima semana' not in html


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/test123')
def test_send_trial_expired_email_calls_django_send(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    from core.tenant_management.models import TenantModel, Subscription, Plan
    job, dna, calendar, posts = full_setup
    plan, _ = Plan.objects.get_or_create(name='User', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    user = UserModel.objects.create_user(username=job.email, email=job.email, password='pass1234')
    tenant = TenantModel.objects.create(name=user.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=plan, status='trial_expired')
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    job.user = user
    job.save(update_fields=['user'])

    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_trial_expired(job=job, brand_dna=dna)

    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert job.email in call_kwargs[1]['recipient_list']
    html = call_kwargs[1]['html_message']
    assert f'https://buy.stripe.com/test123?client_reference_id={tenant.id}' in html

