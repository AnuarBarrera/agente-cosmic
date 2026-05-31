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
        sender.send_initial(job=job, brand_dna=dna, calendar=calendar)
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert job.email in call_kwargs[1]['recipient_list']


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_initial_email_subject_contains_business_name(full_setup):
    from core.content_pipeline.email_sender import EmailSender
    job, dna, calendar, posts = full_setup
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        sender = EmailSender()
        sender.send_initial(job=job, brand_dna=dna, calendar=calendar)
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
