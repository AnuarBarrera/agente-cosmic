import pytest
from unittest.mock import patch
from django.test import override_settings
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost, WeeklyFeedback

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
def test_content_generation_generates_image_for_every_day(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    assert MockImage.return_value.generate.call_count == 7
    posts = ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna)
    assert all(p.image_url == 'https://storage.googleapis.com/test/img.jpg' for p in posts)


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


def test_load_product_images_takes_paths_list(tmp_path, settings):
    settings.MEDIA_ROOT = str(tmp_path)
    uploads_dir = tmp_path / 'uploads'
    uploads_dir.mkdir()
    (uploads_dir / 'product.webp').write_bytes(b'fake-image-bytes')

    from core.content_pipeline.tasks import _load_product_images
    result = _load_product_images(['uploads/product.webp'])
    assert result == [b'fake-image-bytes']


def test_product_image_for_day_maps_day_in_week():
    from core.content_pipeline.tasks import _product_image_for_day
    images = [b'img1', b'img2', b'img3']

    # Semana 1: day_in_week == day_number
    assert _product_image_for_day(1, images) == b'img1'
    assert _product_image_for_day(3, images) == b'img3'
    assert _product_image_for_day(4, images) is None

    # Semana 2, día 8 -> day_in_week 1 (mismo resultado que día 1 de semana 1)
    day_in_week = ((8 - 1) % 7) + 1
    assert day_in_week == 1
    assert _product_image_for_day(day_in_week, images) == _product_image_for_day(1, images)


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    DEFAULT_FROM_EMAIL='noreply@test.com',
)
def test_content_generation_sets_active_product_images(job_with_dna):
    job_with_dna.product_image_paths = ['uploads/p1.jpg', 'uploads/p2.jpg']
    job_with_dna.save(update_fields=['product_image_paths'])

    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    calendar = ContentCalendar.objects.get(brand_dna__job=job_with_dna)
    assert calendar.active_product_images == ['uploads/p1.jpg', 'uploads/p2.jpg']


@pytest.fixture
def calendar_with_dna():
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://tuwebmx.com')
    dna = BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    return ContentCalendar.objects.create(brand_dna=dna)


def _make_post(calendar, day_number, **kwargs):
    defaults = dict(
        caption=f'Post {day_number}',
        image_url='https://example.com/img.jpg',
        suggested_time='19:00',
        hashtags=[],
        scheduled_at=timezone.now() + timedelta(days=day_number),
    )
    defaults.update(kwargs)
    return ContentPost.objects.create(calendar=calendar, day_number=day_number, **defaults)


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_daily_email_task_creates_weekly_feedback_on_day_7(calendar_with_dna):
    post = _make_post(calendar_with_dna, 7)
    with patch('core.content_pipeline.tasks.EmailSender'):
        from core.content_pipeline.tasks import send_daily_email_task
        send_daily_email_task(str(post.id))

    assert WeeklyFeedback.objects.filter(calendar=calendar_with_dna, week_number=1).exists()


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_daily_email_task_no_feedback_on_other_days(calendar_with_dna):
    post = _make_post(calendar_with_dna, 5)
    with patch('core.content_pipeline.tasks.EmailSender'):
        from core.content_pipeline.tasks import send_daily_email_task
        send_daily_email_task(str(post.id))

    assert not WeeklyFeedback.objects.filter(calendar=calendar_with_dna).exists()


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_daily_email_task_weekly_feedback_idempotent(calendar_with_dna):
    post = _make_post(calendar_with_dna, 14)
    with patch('core.content_pipeline.tasks.EmailSender'):
        from core.content_pipeline.tasks import send_daily_email_task
        send_daily_email_task(str(post.id))
        send_daily_email_task(str(post.id))

    assert WeeklyFeedback.objects.filter(calendar=calendar_with_dna, week_number=2).count() == 1


def test_backfill_image_task_generates_missing_image(calendar_with_dna):
    post = _make_post(calendar_with_dna, 3, image_url='')
    with patch('core.content_pipeline.tasks.ImageGenerator') as MockImage:
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.png?v=123'
        from core.content_pipeline.tasks import backfill_image_task
        backfill_image_task(str(post.id))

    post.refresh_from_db()
    assert post.image_url == 'https://storage.googleapis.com/test/img.png?v=123'


def test_backfill_image_task_skips_post_with_existing_image(calendar_with_dna):
    post = _make_post(calendar_with_dna, 3, image_url='https://example.com/already-there.jpg')
    with patch('core.content_pipeline.tasks.ImageGenerator') as MockImage:
        from core.content_pipeline.tasks import backfill_image_task
        backfill_image_task(str(post.id))

    MockImage.assert_not_called()
    post.refresh_from_db()
    assert post.image_url == 'https://example.com/already-there.jpg'


def test_backfill_image_task_skips_deleted_calendar(calendar_with_dna):
    from django.utils import timezone as tz
    job = calendar_with_dna.brand_dna.job
    job.deleted_at = tz.now()
    job.save(update_fields=['deleted_at'])
    post = _make_post(calendar_with_dna, 3, image_url='')
    with patch('core.content_pipeline.tasks.ImageGenerator') as MockImage:
        from core.content_pipeline.tasks import backfill_image_task
        backfill_image_task(str(post.id))

    MockImage.assert_not_called()


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
)
def test_generate_next_week_creates_posts_for_week_2(job_with_dna):
    calendar = ContentCalendar.objects.create(
        brand_dna=job_with_dna.brand_dna, active_product_images=[], next_week_generating=True,
    )

    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.schedule_daily_emails') as mock_schedule, \
         patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'

        from core.content_pipeline.tasks import generate_next_week
        generate_next_week(str(calendar.id), week_number=2)

    days = sorted(p.day_number for p in calendar.posts.all())
    assert days == list(range(8, 15))
    assert all(p.image_url for p in calendar.posts.all())
    MockEmail.return_value.send_week_ready.assert_called_once()
    calendar.refresh_from_db()
    assert calendar.next_week_generating is False


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_next_week_resets_flag_even_on_failure(job_with_dna):
    calendar = ContentCalendar.objects.create(
        brand_dna=job_with_dna.brand_dna, active_product_images=[], next_week_generating=True,
    )

    with patch('core.content_pipeline.tasks.TextGenerator') as MockText:
        MockText.return_value.generate.side_effect = Exception('Gemini caido')

        from core.content_pipeline.tasks import generate_next_week
        generate_next_week(str(calendar.id), week_number=2)

    calendar.refresh_from_db()
    assert calendar.next_week_generating is False
    assert calendar.posts.count() == 0
