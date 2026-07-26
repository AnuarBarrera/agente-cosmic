import pytest
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost

pytestmark = pytest.mark.django_db


@pytest.fixture
def brand_dna():
    job = AnalysisJob.objects.create(email='test@example.com', business_url='https://tuwebmx.com')
    return BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseño'], audience='PYMEs',
        tone='profesional', primary_colors=['#1A1A2E'],
    )


def test_content_calendar_creation(brand_dna):
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    assert calendar.brand_dna == brand_dna
    assert calendar.id is not None


def test_content_post_creation(brand_dna):
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    scheduled = timezone.now() + timedelta(days=1)
    post = ContentPost.objects.create(
        calendar=calendar,
        day_number=1,
        caption='Post de prueba para redes sociales.',
        image_url='https://storage.googleapis.com/agente-cosmic/img1.jpg',
        suggested_time='19:00',
        hashtags=['#diseñoweb', '#mexico'],
        scheduled_at=scheduled,
    )
    assert post.status == 'pending'
    assert post.day_number == 1
    assert post.sent_at is None


def test_calendar_has_7_posts(brand_dna):
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    for i in range(1, 8):
        ContentPost.objects.create(
            calendar=calendar, day_number=i,
            caption=f'Post día {i}', image_url='https://example.com/img.jpg',
            suggested_time='19:00', hashtags=[],
            scheduled_at=timezone.now() + timedelta(days=i),
        )
    assert calendar.posts.count() == 7


def test_content_post_reel_format(brand_dna):
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    scheduled = timezone.now() + timedelta(days=1)
    post = ContentPost.objects.create(
        calendar=calendar,
        day_number=1,
        caption='Reel de prueba.',
        image_url='https://storage.googleapis.com/agente-cosmic/poster_frame.jpg',
        video_url='https://storage.googleapis.com/agente-cosmic/video.mp4',
        format=ContentPost.FORMAT_REEL,
        suggested_time='19:00',
        hashtags=['#reel', '#test'],
        scheduled_at=scheduled,
    )
    assert post.format == 'reel'
    assert post.video_url == 'https://storage.googleapis.com/agente-cosmic/video.mp4'
    assert post.image_url == 'https://storage.googleapis.com/agente-cosmic/poster_frame.jpg'


def test_content_calendar_last_reactivation_email_at_defaults_to_none(brand_dna):
    calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
    assert calendar.last_reactivation_email_at is None

