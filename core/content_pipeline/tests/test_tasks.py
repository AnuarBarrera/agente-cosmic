import time
import pytest
from unittest.mock import patch, MagicMock
from rq.job import Job, Dependency
from django.test import override_settings
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost

pytestmark = pytest.mark.django_db

_MOCK_POSTS = [
    {'caption': f'Post {i}', 'hashtags': ['#test'], 'suggested_time': '19:00'}
    for i in range(1, 8)
]

_MOCK_POSTS_WITH_CAROUSEL = [
    {'caption': f'Post {i}', 'hashtags': ['#test'], 'suggested_time': '19:00',
     'format': 'carousel' if i == 3 else 'single'}
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


@pytest.fixture
def job_with_dna_and_tenant(job_with_dna):
    from django.contrib.auth import get_user_model
    from core.tenant_management.models import TenantModel, Subscription, Plan
    UserModel = get_user_model()
    plan, _ = Plan.objects.get_or_create(name='User', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    user = UserModel.objects.create_user(
        username='trial@test.com', email='trial@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=user.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=plan)
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    job_with_dna.user = user
    job_with_dna.save(update_fields=['user'])
    return job_with_dna


@pytest.fixture
def trialing_job_with_tenant(job_with_dna_and_tenant):
    from core.tenant_management.models import Subscription
    Subscription.objects.filter(tenant=job_with_dna_and_tenant.user.tenant).update(
        status='trialing', trial_ends_at=timezone.now() - timedelta(hours=1),
    )
    return job_with_dna_and_tenant



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
         patch('core.content_pipeline.tasks._enqueue_trial_images'):
        MockText.return_value.generate.return_value = _MOCK_POSTS

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    assert ContentCalendar.objects.filter(brand_dna__job=job_with_dna).exists()
    assert ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna).count() == 7


def test_content_generation_starts_trial_for_tenant(job_with_dna_and_tenant):
    from core.tenant_management.models import Subscription
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks._enqueue_trial_images'):
        MockText.return_value.generate.return_value = _MOCK_POSTS

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna_and_tenant.id))

    sub = Subscription.objects.get(tenant=job_with_dna_and_tenant.user.tenant)
    assert sub.status == 'trialing'
    assert sub.trial_ends_at is not None
    assert sub.trial_ends_at > timezone.now() + timedelta(days=6)
    assert sub.trial_ends_at < timezone.now() + timedelta(days=8)


def test_content_generation_does_not_start_trial_for_tester_plan(job_with_dna):
    from django.contrib.auth import get_user_model
    from core.tenant_management.models import TenantModel, Subscription, Plan
    UserModel = get_user_model()
    tester_plan, _ = Plan.objects.get_or_create(name='Tester', defaults={
        'max_calendars_per_week': 999, 'max_post_regenerations': 999,
        'max_post_edits': 999, 'price': 0,
    })
    user = UserModel.objects.create_user(
        username='tester@test.com', email='tester@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=user.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=tester_plan, status='active')
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    job_with_dna.user = user
    job_with_dna.save(update_fields=['user'])

    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks._enqueue_trial_images'):
        MockText.return_value.generate.return_value = _MOCK_POSTS

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    sub = Subscription.objects.get(tenant=tenant)
    assert sub.status == 'active'
    assert sub.trial_ends_at is None


def test_content_generation_without_user_does_not_crash(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks._enqueue_trial_images') as mock_enqueue:
        MockText.return_value.generate.return_value = _MOCK_POSTS

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    job_with_dna.refresh_from_db()
    assert job_with_dna.status == AnalysisJob.STATUS_PROCESSING
    mock_enqueue.assert_called_once()


def test_content_generation_creates_posts_without_images_and_enqueues_trial(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks._enqueue_trial_images') as mock_enqueue:
        MockText.return_value.generate.return_value = _MOCK_POSTS

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    posts = ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna)
    assert posts.count() == 7
    assert all(p.image_url == '' and p.image_urls == [] and p.video_url == '' for p in posts)

    job_with_dna.refresh_from_db()
    assert job_with_dna.stage == AnalysisJob.STAGE_CONTENT
    assert job_with_dna.progress == 87

    calendar = ContentCalendar.objects.get(brand_dna__job=job_with_dna)
    mock_enqueue.assert_called_once()
    call_args = mock_enqueue.call_args.args
    assert call_args[0] == str(job_with_dna.id)
    assert call_args[1] == str(calendar.id)
    assert isinstance(call_args[2], float)


def test_content_generation_observes_duration_metric_on_text_failure(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.CONTENT_GENERATION_DURATION') as mock_duration:
        MockText.return_value.generate.side_effect = Exception('Gemini error')

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    mock_duration.observe.assert_called_once()
    job_with_dna.refresh_from_db()
    assert job_with_dna.status == AnalysisJob.STATUS_FAILED


def test_content_generation_uses_carousel_for_carousel_day(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks._enqueue_trial_images'):
        MockText.return_value.generate.return_value = _MOCK_POSTS_WITH_CAROUSEL

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    posts = ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna).order_by('day_number')
    carousel_post = posts.get(day_number=3)
    assert carousel_post.format == 'carousel'
    assert carousel_post.image_url == ''
    assert carousel_post.image_urls == []
    non_carousel_posts = [p for p in posts if p.day_number != 3]
    assert all(p.format == 'single' and p.image_urls == [] for p in non_carousel_posts)


_MOCK_POSTS_FOR_SAMPLE = [
    {'caption': 'Post reel', 'hashtags': ['#test'], 'suggested_time': '19:00', 'format': 'reel'},
    {'caption': 'Post imagen', 'hashtags': ['#test'], 'suggested_time': '19:00', 'format': 'single'},
    {'caption': 'Post carrusel', 'hashtags': ['#test'], 'suggested_time': '19:00', 'format': 'carousel'},
] + [
    {'caption': f'Post {i}', 'hashtags': ['#test'], 'suggested_time': '19:00', 'format': 'single'}
    for i in range(4, 8)
]


@pytest.fixture
def job_with_dna_sample_image():
    job = AnalysisJob.objects.create(
        email='t@t.com', business_url='https://tuwebmx.com',
        status=AnalysisJob.STATUS_PROCESSING, stage=AnalysisJob.STAGE_CONTENT, progress=78,
        generation_mode=AnalysisJob.MODE_SAMPLE_IMAGE,
    )
    BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    return job


@pytest.fixture
def job_with_dna_sample_reel():
    job = AnalysisJob.objects.create(
        email='t@t.com', business_url='https://tuwebmx.com',
        status=AnalysisJob.STATUS_PROCESSING, stage=AnalysisJob.STAGE_CONTENT, progress=78,
        generation_mode=AnalysisJob.MODE_SAMPLE_REEL,
    )
    BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    return job


@pytest.fixture
def job_with_dna_sample_product_reel():
    job = AnalysisJob.objects.create(
        email='t@t.com', business_url='https://tuwebmx.com',
        status=AnalysisJob.STATUS_PROCESSING, stage=AnalysisJob.STAGE_CONTENT, progress=78,
        generation_mode=AnalysisJob.MODE_SAMPLE_PRODUCT_REEL,
        product_reference_image_path='uploads/product_ref_test.jpg',
    )
    BrandDNA.objects.create(
        job=job, business_name='Gelatinas Marba', business_url='https://tuwebmx.com',
        description='Gelatinas artesanales', keywords=['gelatinas'], audience='Familias',
        tone='alegre', primary_colors=['#e94560'],
    )
    return job



@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
)
def test_generate_sample_task_creates_single_post_calendar_for_image(job_with_dna_sample_image):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender') as MockEmail, \
         patch('core.content_pipeline.tasks.schedule_daily_emails') as mock_schedule:
        MockText.return_value.generate.return_value = _MOCK_POSTS_FOR_SAMPLE
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/sample.jpg'

        from core.content_pipeline.tasks import generate_sample_task
        generate_sample_task(str(job_with_dna_sample_image.id))

    assert ContentCalendar.objects.filter(brand_dna__job=job_with_dna_sample_image).exists()
    posts = ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna_sample_image)
    assert posts.count() == 1
    post = posts.first()
    assert post.format == ContentPost.FORMAT_SINGLE
    assert post.caption == 'Post imagen'
    assert post.image_url == 'https://storage.googleapis.com/test/sample.jpg'
    MockEmail.return_value.send_initial.assert_not_called()
    mock_schedule.assert_not_called()
    job_with_dna_sample_image.refresh_from_db()
    assert job_with_dna_sample_image.status == AnalysisJob.STATUS_DONE


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
)
def test_generate_sample_task_creates_single_post_calendar_for_reel(job_with_dna_sample_reel):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ReelScriptGenerator') as MockScript, \
         patch('core.content_pipeline.tasks.ReelGenerator') as MockReel, \
         patch('core.content_pipeline.tasks.EmailSender') as MockEmail, \
         patch('core.content_pipeline.tasks.schedule_daily_emails') as mock_schedule:
        MockText.return_value.generate.return_value = _MOCK_POSTS_FOR_SAMPLE
        MockScript.return_value.generate.return_value = {'hook_text': 'H', 'scene_prompts': ['a', 'b', 'c']}
        MockReel.return_value.generate.return_value = ('https://storage.test/reel.mp4', 'https://storage.test/poster.png')

        from core.content_pipeline.tasks import generate_sample_task
        generate_sample_task(str(job_with_dna_sample_reel.id))

    posts = ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna_sample_reel)
    assert posts.count() == 1
    post = posts.first()
    assert post.format == ContentPost.FORMAT_REEL
    assert post.caption == 'Post reel'
    assert post.video_url == 'https://storage.test/reel.mp4'
    MockEmail.return_value.send_initial.assert_not_called()
    mock_schedule.assert_not_called()


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
)
def test_generate_sample_task_marks_failed_on_error(job_with_dna_sample_image):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText:
        MockText.return_value.generate.side_effect = Exception('Gemini error')

        from core.content_pipeline.tasks import generate_sample_task
        generate_sample_task(str(job_with_dna_sample_image.id))

    job_with_dna_sample_image.refresh_from_db()
    assert job_with_dna_sample_image.status == AnalysisJob.STATUS_FAILED
    assert 'Gemini error' in job_with_dna_sample_image.error_message


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
)
def test_generate_sample_task_product_reel_mode_creates_post(job_with_dna_sample_product_reel):
    with patch('core.content_pipeline.tasks.read_upload', return_value=b'fake-photo-bytes'), \
         patch('core.content_pipeline.tasks.ProductShowcaseGenerator') as MockGen:
        MockGen.return_value.generate_reel.return_value = ('https://storage.test/video.mp4', 'https://storage.test/poster.png', '')

        from core.content_pipeline.tasks import generate_sample_task
        generate_sample_task(str(job_with_dna_sample_product_reel.id))

    posts = ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna_sample_product_reel)
    assert posts.count() == 1
    post = posts.first()
    assert post.format == ContentPost.FORMAT_REEL
    assert post.video_url == 'https://storage.test/video.mp4'
    assert post.image_url == 'https://storage.test/poster.png'


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
)
def test_generate_sample_task_product_reel_mode_fails_when_qc_rejects(job_with_dna_sample_product_reel):
    with patch('core.content_pipeline.tasks.read_upload', return_value=b'fake-photo-bytes'), \
         patch('core.content_pipeline.tasks.ProductShowcaseGenerator') as MockGen:
        MockGen.return_value.generate_reel.return_value = ('', '', 'El control de calidad rechazó el resultado. Reintenta.')

        from core.content_pipeline.tasks import generate_sample_task
        generate_sample_task(str(job_with_dna_sample_product_reel.id))

    posts = ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna_sample_product_reel)
    assert posts.count() == 0
    job_with_dna_sample_product_reel.refresh_from_db()
    assert job_with_dna_sample_product_reel.status == AnalysisJob.STATUS_FAILED
    assert 'calidad' in job_with_dna_sample_product_reel.error_message.lower()


def test_generate_sample_task_product_reel_mode_fails_without_photo():
    job = AnalysisJob.objects.create(
        email='t@t.com', business_url='https://tuwebmx.com',
        generation_mode=AnalysisJob.MODE_SAMPLE_PRODUCT_REEL,
        product_reference_image_path='',
    )
    BrandDNA.objects.create(
        job=job, business_name='Gelatinas Marba', business_url='https://tuwebmx.com',
        description='Gelatinas artesanales', keywords=[], audience='Familias',
        tone='alegre', primary_colors=[],
    )

    from core.content_pipeline.tasks import generate_sample_task
    generate_sample_task(str(job.id))

    job.refresh_from_db()
    assert job.status == AnalysisJob.STATUS_FAILED
    assert 'foto' in job.error_message.lower()



@pytest.fixture
def calendar_with_dna():
    from django.contrib.auth import get_user_model
    from core.tenant_management.models import TenantModel, Subscription, Plan
    UserModel = get_user_model()
    plan, _ = Plan.objects.get_or_create(name='User', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    user = UserModel.objects.create_user(
        username='calendariovencido@test.com', email='calendariovencido@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=user.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=plan, status='active')
    user.tenant = tenant
    user.save(update_fields=['tenant'])

    job = AnalysisJob.objects.create(
        email='t@t.com', business_url='https://tuwebmx.com', user=user,
    )
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




def test_backfill_image_task_generates_missing_image(calendar_with_dna):
    post = _make_post(calendar_with_dna, 3, image_url='')
    with patch('core.content_pipeline.tasks.ImageGenerator') as MockImage:
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.png?v=123'
        from core.content_pipeline.tasks import backfill_image_task
        backfill_image_task(str(post.id))

    post.refresh_from_db()
    assert post.image_url == 'https://storage.googleapis.com/test/img.png?v=123'


def test_backfill_image_task_uses_carousel_when_post_format_is_carousel(calendar_with_dna):
    post = _make_post(calendar_with_dna, 3, image_url='', format='carousel')
    with patch('core.content_pipeline.tasks.ImageGenerator') as MockImage:
        MockImage.return_value.generate_carousel.return_value = [
            'https://storage.googleapis.com/test/slide1.png',
            'https://storage.googleapis.com/test/slide2.png',
        ]
        from core.content_pipeline.tasks import backfill_image_task
        backfill_image_task(str(post.id))

    MockImage.return_value.generate.assert_not_called()
    MockImage.return_value.generate_carousel.assert_called_once()
    post.refresh_from_db()
    assert post.image_url == 'https://storage.googleapis.com/test/slide1.png'
    assert post.image_urls == [
        'https://storage.googleapis.com/test/slide1.png',
        'https://storage.googleapis.com/test/slide2.png',
    ]


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


def test_backfill_image_task_uses_reel_for_reel_format(calendar_with_dna):
    post = _make_post(calendar_with_dna, 1, image_url='', format='reel')
    with patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.ReelScriptGenerator') as MockScript, \
         patch('core.content_pipeline.tasks.ReelGenerator') as MockReel:
        MockScript.return_value.generate.return_value = {'hook_text': 'H', 'scene_prompts': ['a', 'b', 'c']}
        MockReel.return_value.generate.return_value = ('https://storage.test/reel.mp4', 'https://storage.test/poster.png')
        from core.content_pipeline.tasks import backfill_image_task
        backfill_image_task(str(post.id))

    MockImage.return_value.generate.assert_not_called()
    post.refresh_from_db()
    assert post.video_url == 'https://storage.test/reel.mp4'
    assert post.image_url == 'https://storage.test/poster.png'


def test_backfill_image_task_falls_back_to_image_when_reel_generation_fails(calendar_with_dna):
    post = _make_post(calendar_with_dna, 1, image_url='', format='reel')
    with patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.ReelScriptGenerator') as MockScript, \
         patch('core.content_pipeline.tasks.ReelGenerator') as MockReel:
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/fallback.jpg'
        MockScript.return_value.generate.return_value = {'hook_text': 'H', 'scene_prompts': ['a', 'b', 'c']}
        MockReel.return_value.generate.return_value = ('', '')
        from core.content_pipeline.tasks import backfill_image_task
        backfill_image_task(str(post.id))

    post.refresh_from_db()
    assert post.video_url == ''
    assert post.image_url == 'https://storage.googleapis.com/test/fallback.jpg'


def test_backfill_image_task_passes_business_url_to_image_gen(calendar_with_dna):
    post = _make_post(calendar_with_dna, 3, image_url='')
    with patch('core.content_pipeline.tasks.ImageGenerator') as MockImage:
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'
        from core.content_pipeline.tasks import backfill_image_task
        backfill_image_task(str(post.id))

    call_kwargs = MockImage.return_value.generate.call_args_list[0].kwargs
    assert call_kwargs['business_url'] == 'https://tuwebmx.com'


def test_generate_next_month_creates_28_posts_without_images(job_with_dna):
    from core.content_pipeline.tasks import content_generation_task, generate_next_month
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'), \
         patch('core.content_pipeline.tasks._enqueue_trial_images'), \
         patch('core.content_pipeline.tasks._enqueue_week_images') as mock_enqueue_week:
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'
        content_generation_task(str(job_with_dna.id))
        job_with_dna.status = AnalysisJob.STATUS_DONE
        job_with_dna.save(update_fields=['status'])

        calendar = ContentCalendar.objects.get(brand_dna__job=job_with_dna)
        generate_next_month(str(calendar.id))

    posts = ContentPost.objects.filter(calendar=calendar).order_by('day_number')
    assert posts.count() == 35  # 7 del trial + 28 del mes
    day_numbers = list(posts.values_list('day_number', flat=True))
    assert day_numbers == list(range(1, 36))
    assert MockText.return_value.generate.call_count == 5  # 1 del trial + 4 del mes
    new_posts = posts.filter(day_number__gte=8)
    assert all(p.image_url == '' for p in new_posts)
    assert all(p.image_urls == [] for p in new_posts)
    assert all(p.video_url == '' for p in new_posts)
    mock_enqueue_week.assert_called_once_with(str(calendar.id), week_index=0)


def test_generate_next_month_resets_flag_on_text_failure(job_with_dna):
    from core.content_pipeline.tasks import content_generation_task, generate_next_month
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'), \
         patch('core.content_pipeline.tasks._enqueue_trial_images'):
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'
        content_generation_task(str(job_with_dna.id))
        job_with_dna.status = AnalysisJob.STATUS_DONE
        job_with_dna.save(update_fields=['status'])
        calendar = ContentCalendar.objects.get(brand_dna__job=job_with_dna)
        calendar.next_week_generating = True
        calendar.save(update_fields=['next_week_generating'])

        MockText.return_value.generate.side_effect = Exception('Gemini error')
        generate_next_month(str(calendar.id))

    calendar.refresh_from_db()
    assert calendar.next_week_generating is False


def test_generate_next_month_keeps_flag_true_on_success(job_with_dna):
    from core.content_pipeline.tasks import content_generation_task, generate_next_month
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'), \
         patch('core.content_pipeline.tasks._enqueue_trial_images'), \
         patch('core.content_pipeline.tasks._enqueue_week_images'):
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'
        content_generation_task(str(job_with_dna.id))
        job_with_dna.status = AnalysisJob.STATUS_DONE
        job_with_dna.save(update_fields=['status'])
        calendar = ContentCalendar.objects.get(brand_dna__job=job_with_dna)
        calendar.next_week_generating = True
        calendar.save(update_fields=['next_week_generating'])

        generate_next_month(str(calendar.id))

    calendar.refresh_from_db()
    assert calendar.next_week_generating is True


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/test123')
def test_expire_stale_trials_sends_email_and_expires_subscription(trialing_job_with_tenant):
    from core.tenant_management.models import Subscription
    from core.content_pipeline.models import ContentCalendar
    ContentCalendar.objects.create(brand_dna=trialing_job_with_tenant.brand_dna)

    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import expire_stale_trials_task
        expire_stale_trials_task()

    MockEmail.return_value.send_trial_expired.assert_called_once()
    call_kwargs = MockEmail.return_value.send_trial_expired.call_args[1]
    assert call_kwargs['job'] == trialing_job_with_tenant

    sub = Subscription.objects.get(tenant=trialing_job_with_tenant.user.tenant)
    assert sub.status == 'trial_expired'


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/test123')
def test_expire_stale_trials_ignores_active_subscriptions(job_with_dna_and_tenant):
    from core.tenant_management.models import Subscription
    Subscription.objects.filter(tenant=job_with_dna_and_tenant.user.tenant).update(
        status='active', trial_ends_at=None,
    )
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import expire_stale_trials_task
        expire_stale_trials_task()

    MockEmail.return_value.send_trial_expired.assert_not_called()
    sub = Subscription.objects.get(tenant=job_with_dna_and_tenant.user.tenant)
    assert sub.status == 'active'


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/test123')
def test_expire_stale_trials_is_idempotent(trialing_job_with_tenant):
    from core.tenant_management.models import Subscription
    from core.content_pipeline.models import ContentCalendar
    ContentCalendar.objects.create(brand_dna=trialing_job_with_tenant.brand_dna)

    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import expire_stale_trials_task
        expire_stale_trials_task()
        expire_stale_trials_task()

    assert MockEmail.return_value.send_trial_expired.call_count == 1


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', STRIPE_PAYMENT_LINK_URL='https://buy.stripe.com/test123')
def test_expire_stale_trials_expires_lapsed_paid_month(job_with_dna_and_tenant):
    from core.tenant_management.models import Subscription
    from core.content_pipeline.models import ContentCalendar
    Subscription.objects.filter(tenant=job_with_dna_and_tenant.user.tenant).update(
        status='active', paid_until=timezone.now() - timedelta(hours=1),
    )
    ContentCalendar.objects.create(brand_dna=job_with_dna_and_tenant.brand_dna)

    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import expire_stale_trials_task
        expire_stale_trials_task()

    MockEmail.return_value.send_month_expired.assert_called_once()
    MockEmail.return_value.send_trial_expired.assert_not_called()
    sub = Subscription.objects.get(tenant=job_with_dna_and_tenant.user.tenant)
    assert sub.status == 'trial_expired'


def test_expire_stale_trials_ignores_active_with_future_paid_until(job_with_dna_and_tenant):
    from core.tenant_management.models import Subscription
    Subscription.objects.filter(tenant=job_with_dna_and_tenant.user.tenant).update(
        status='active', paid_until=timezone.now() + timedelta(days=10),
    )
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import expire_stale_trials_task
        expire_stale_trials_task()

    MockEmail.return_value.send_month_expired.assert_not_called()
    sub = Subscription.objects.get(tenant=job_with_dna_and_tenant.user.tenant)
    assert sub.status == 'active'


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_daily_email_task_skips_when_already_downloaded(calendar_with_dna):
    post = _make_post(calendar_with_dna, 3, downloaded_at=timezone.now())
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        from core.content_pipeline.tasks import send_daily_email_task
        send_daily_email_task(str(post.id))
    mock_send.assert_not_called()
    post.refresh_from_db()
    assert post.status == ContentPost.STATUS_PENDING


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx', COSMIC_BASE_URL='https://cosmic.anuarbarrera.dev')
def test_send_daily_email_task_sends_when_not_downloaded(calendar_with_dna):
    post = _make_post(calendar_with_dna, 3)
    with patch('core.content_pipeline.email_sender.send_mail') as mock_send:
        from core.content_pipeline.tasks import send_daily_email_task
        send_daily_email_task(str(post.id))
    mock_send.assert_called_once()
    post.refresh_from_db()
    assert post.status == ContentPost.STATUS_SENT


def _make_calendar_with_month(job_with_dna, reel_day_number=None):
    """Crea un calendar con 7 posts de trial + 28 del mes, todos sin imagen (image_url='')."""
    from core.content_pipeline.models import ContentCalendar, ContentPost
    calendar = ContentCalendar.objects.create(brand_dna=job_with_dna.brand_dna)
    for day in range(1, 36):
        fmt = ContentPost.FORMAT_REEL if day == reel_day_number else ContentPost.FORMAT_SINGLE
        ContentPost.objects.create(
            calendar=calendar, day_number=day, caption=f'Post {day}',
            image_url='', image_urls=[], video_url='', format=fmt,
            suggested_time='19:00', hashtags=[],
            scheduled_at=timezone.now() + timedelta(days=day),
        )
    return calendar


def test_enqueue_week_images_enqueues_7_jobs_plus_closing(job_with_dna):
    from core.content_pipeline.tasks import _enqueue_week_images
    calendar = _make_calendar_with_month(job_with_dna)
    with patch('core.content_pipeline.tasks.django_rq') as mock_rq:
        mock_rq.enqueue.side_effect = lambda *a, **kw: MagicMock(spec=Job)
        _enqueue_week_images(str(calendar.id), week_index=0)
    assert mock_rq.enqueue.call_count == 8  # 7 backfill_image_task + 1 _week_closing_task
    closing_call = mock_rq.enqueue.call_args_list[-1]
    assert closing_call.kwargs['job_timeout'] == 120
    dependency = closing_call.kwargs['depends_on']
    assert isinstance(dependency, Dependency)
    assert len(dependency.dependencies) == 7
    assert dependency.allow_failure is True


def test_enqueue_week_images_uses_longer_timeout_for_reel(job_with_dna):
    from core.content_pipeline.tasks import _enqueue_week_images
    calendar = _make_calendar_with_month(job_with_dna, reel_day_number=8)
    with patch('core.content_pipeline.tasks.django_rq') as mock_rq:
        mock_rq.enqueue.side_effect = lambda *a, **kw: MagicMock(spec=Job)
        _enqueue_week_images(str(calendar.id), week_index=0)
    backfill_calls = mock_rq.enqueue.call_args_list[:7]
    timeouts_by_post_id = {call.args[1]: call.kwargs['job_timeout'] for call in backfill_calls}
    reel_post = calendar.posts.get(day_number=8)
    single_post = calendar.posts.get(day_number=9)
    assert timeouts_by_post_id[str(reel_post.id)] == 2700
    assert timeouts_by_post_id[str(single_post.id)] == 300


def test_enqueue_week_images_selects_correct_day_range_for_week_index(job_with_dna):
    from core.content_pipeline.tasks import _enqueue_week_images
    calendar = _make_calendar_with_month(job_with_dna)
    with patch('core.content_pipeline.tasks.django_rq') as mock_rq:
        mock_rq.enqueue.side_effect = lambda *a, **kw: MagicMock(spec=Job)
        _enqueue_week_images(str(calendar.id), week_index=2)  # dias 22-28
    backfill_post_ids = {call.args[1] for call in mock_rq.enqueue.call_args_list[:7]}
    expected_ids = {str(p.id) for p in calendar.posts.filter(day_number__gte=22, day_number__lte=28)}
    assert backfill_post_ids == expected_ids


def test_week_closing_task_week_0_sends_week_ready_and_advances(job_with_dna):
    from core.content_pipeline.tasks import _week_closing_task
    calendar = _make_calendar_with_month(job_with_dna)
    calendar.next_week_generating = True
    calendar.save(update_fields=['next_week_generating'])
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail, \
         patch('core.content_pipeline.tasks._enqueue_week_images') as mock_enqueue:
        _week_closing_task(str(calendar.id), week_index=0)
    MockEmail.return_value.send_week_ready.assert_called_once()
    MockEmail.return_value.send_month_ready.assert_not_called()
    mock_enqueue.assert_called_once_with(str(calendar.id), 1)
    calendar.refresh_from_db()
    assert calendar.next_week_generating is True


def test_week_closing_task_middle_weeks_silent(job_with_dna):
    from core.content_pipeline.tasks import _week_closing_task
    calendar = _make_calendar_with_month(job_with_dna)
    calendar.next_week_generating = True
    calendar.save(update_fields=['next_week_generating'])
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail, \
         patch('core.content_pipeline.tasks._enqueue_week_images') as mock_enqueue:
        _week_closing_task(str(calendar.id), week_index=1)
    MockEmail.return_value.send_week_ready.assert_not_called()
    MockEmail.return_value.send_month_ready.assert_not_called()
    mock_enqueue.assert_called_once_with(str(calendar.id), 2)
    calendar.refresh_from_db()
    assert calendar.next_week_generating is True


def test_week_closing_task_week_3_sends_month_ready_and_resets_flag(job_with_dna):
    from core.content_pipeline.tasks import _week_closing_task
    calendar = _make_calendar_with_month(job_with_dna)
    calendar.next_week_generating = True
    calendar.save(update_fields=['next_week_generating'])
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail, \
         patch('core.content_pipeline.tasks._enqueue_week_images') as mock_enqueue:
        _week_closing_task(str(calendar.id), week_index=3)
    MockEmail.return_value.send_month_ready.assert_called_once()
    MockEmail.return_value.send_week_ready.assert_not_called()
    mock_enqueue.assert_not_called()
    calendar.refresh_from_db()
    assert calendar.next_week_generating is False


def test_week_closing_task_advances_despite_partial_failure_is_implicit_in_dependency(job_with_dna):
    """No hay logica propia de _week_closing_task para fallos parciales — RQ ya
    dispara el job aunque algun dependiente haya fallado (allow_failure=True, probado
    en test_enqueue_week_images_enqueues_7_jobs_plus_closing). Este test solo confirma
    que _week_closing_task no revisa el estado de los 7 posts antes de avanzar."""
    from core.content_pipeline.tasks import _week_closing_task
    calendar = _make_calendar_with_month(job_with_dna)
    calendar.next_week_generating = True
    calendar.save(update_fields=['next_week_generating'])
    # Ningun post de esta semana tiene imagen (todos image_url='') — si _week_closing_task
    # revisara el estado, se bloquearia. Debe avanzar de todos modos.
    with patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks._enqueue_week_images') as mock_enqueue:
        _week_closing_task(str(calendar.id), week_index=0)
    mock_enqueue.assert_called_once_with(str(calendar.id), 1)


def test_week_closing_task_resets_flag_on_internal_error(job_with_dna):
    from core.content_pipeline.tasks import _week_closing_task
    calendar = _make_calendar_with_month(job_with_dna)
    calendar.next_week_generating = True
    calendar.save(update_fields=['next_week_generating'])
    with patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks._enqueue_week_images', side_effect=Exception('redis down')):
        _week_closing_task(str(calendar.id), week_index=0)
    calendar.refresh_from_db()
    assert calendar.next_week_generating is False


def test_enqueue_trial_images_enqueues_7_jobs_plus_closing(calendar_with_dna):
    job_id = str(calendar_with_dna.brand_dna.job.id)
    for i in range(1, 8):
        _make_post(calendar_with_dna, i, image_url='')
    with patch('core.content_pipeline.tasks.django_rq') as mock_rq:
        mock_rq.enqueue.side_effect = lambda *a, **kw: MagicMock(spec=Job)
        from core.content_pipeline.tasks import _enqueue_trial_images
        _enqueue_trial_images(job_id, str(calendar_with_dna.id), 1234.5)
    assert mock_rq.enqueue.call_count == 8  # 7 backfill_image_task + 1 _trial_closing_task
    closing_call = mock_rq.enqueue.call_args_list[-1]
    assert closing_call.args[1:] == (job_id, str(calendar_with_dna.id), 1234.5)
    assert closing_call.kwargs['job_timeout'] == 120
    dependency = closing_call.kwargs['depends_on']
    assert isinstance(dependency, Dependency)
    assert len(dependency.dependencies) == 7
    assert dependency.allow_failure is True


def test_trial_closing_task_sends_initial_email_and_marks_job_done(job_with_dna):
    calendar = ContentCalendar.objects.create(brand_dna=job_with_dna.brand_dna)
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail, \
         patch('core.content_pipeline.tasks.schedule_daily_emails') as mock_schedule, \
         patch('core.content_pipeline.tasks.CONTENT_GENERATION_DURATION') as mock_duration:
        from core.content_pipeline.tasks import _trial_closing_task
        _trial_closing_task(str(job_with_dna.id), str(calendar.id), time.time() - 5)

    MockEmail.return_value.send_initial.assert_called_once()
    mock_schedule.assert_called_once_with(calendar, day_start=1, day_end=0)
    mock_duration.observe.assert_called_once()
    job_with_dna.refresh_from_db()
    assert job_with_dna.stage == AnalysisJob.STAGE_COMPLETE
    assert job_with_dna.progress == 100
    assert job_with_dna.status == AnalysisJob.STATUS_DONE


def test_trial_closing_task_marks_done_even_if_email_fails(job_with_dna):
    calendar = ContentCalendar.objects.create(brand_dna=job_with_dna.brand_dna)
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail, \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
        MockEmail.return_value.send_initial.side_effect = Exception('smtp down')
        from core.content_pipeline.tasks import _trial_closing_task
        _trial_closing_task(str(job_with_dna.id), str(calendar.id), time.time())

    job_with_dna.refresh_from_db()
    assert job_with_dna.status == AnalysisJob.STATUS_DONE


def test_trial_closing_task_marks_failed_on_internal_error(calendar_with_dna):
    mock_job = MagicMock()
    mock_job.save.side_effect = Exception('db down')
    with patch('core.content_pipeline.tasks.AnalysisJob.objects.get', return_value=mock_job), \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'), \
         patch('core.content_pipeline.tasks.CONTENT_GENERATION_DURATION') as mock_duration:
        from core.content_pipeline.tasks import _trial_closing_task
        _trial_closing_task('fake-job-id', str(calendar_with_dna.id), time.time())

    mock_job.mark_failed.assert_called_once()
    mock_duration.observe.assert_called_once()


def test_generate_next_month_defers_when_trial_job_not_done(job_with_dna):
    from core.content_pipeline.tasks import generate_next_month
    from core.content_pipeline.models import ContentCalendar
    job_with_dna.status = AnalysisJob.STATUS_PROCESSING
    job_with_dna.save(update_fields=['status'])
    calendar = ContentCalendar.objects.create(brand_dna=job_with_dna.brand_dna)
    with patch('core.content_pipeline.tasks.django_rq') as mock_rq, \
         patch('core.content_pipeline.tasks.TextGenerator') as MockText:
        generate_next_month(str(calendar.id))
    MockText.assert_not_called()
    mock_rq.get_queue.return_value.enqueue_in.assert_called_once_with(
        timedelta(seconds=60), generate_next_month, str(calendar.id), 1
    )
    assert calendar.posts.count() == 0


def test_generate_next_month_gives_up_when_trial_job_failed(job_with_dna):
    from core.content_pipeline.tasks import generate_next_month
    from core.content_pipeline.models import ContentCalendar
    job_with_dna.status = AnalysisJob.STATUS_FAILED
    job_with_dna.save(update_fields=['status'])
    calendar = ContentCalendar.objects.create(brand_dna=job_with_dna.brand_dna, next_week_generating=True)
    with patch('core.content_pipeline.tasks.django_rq') as mock_rq, \
         patch('core.content_pipeline.tasks.TextGenerator') as MockText:
        generate_next_month(str(calendar.id))
    MockText.assert_not_called()
    mock_rq.get_queue.return_value.enqueue_in.assert_not_called()
    calendar.refresh_from_db()
    assert calendar.next_week_generating is False


def test_generate_next_month_gives_up_after_max_attempts(job_with_dna):
    from core.content_pipeline.tasks import generate_next_month, _MAX_TRIAL_WAIT_ATTEMPTS
    from core.content_pipeline.models import ContentCalendar
    job_with_dna.status = AnalysisJob.STATUS_PROCESSING
    job_with_dna.save(update_fields=['status'])
    calendar = ContentCalendar.objects.create(brand_dna=job_with_dna.brand_dna, next_week_generating=True)
    with patch('core.content_pipeline.tasks.django_rq') as mock_rq, \
         patch('core.content_pipeline.tasks.TextGenerator') as MockText:
        generate_next_month(str(calendar.id), attempt=_MAX_TRIAL_WAIT_ATTEMPTS)
    MockText.assert_not_called()
    mock_rq.get_queue.return_value.enqueue_in.assert_not_called()
    calendar.refresh_from_db()
    assert calendar.next_week_generating is False


def test_generate_next_month_proceeds_when_trial_job_done(job_with_dna):
    from core.content_pipeline.tasks import content_generation_task, generate_next_month
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'), \
         patch('core.content_pipeline.tasks._enqueue_trial_images'), \
         patch('core.content_pipeline.tasks._enqueue_week_images') as mock_enqueue_week:
        MockText.return_value.generate.return_value = _MOCK_POSTS
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'
        content_generation_task(str(job_with_dna.id))
        job_with_dna.status = AnalysisJob.STATUS_DONE
        job_with_dna.save(update_fields=['status'])
        calendar = ContentCalendar.objects.get(brand_dna__job=job_with_dna)
        generate_next_month(str(calendar.id))
    mock_enqueue_week.assert_called_once_with(str(calendar.id), week_index=0)


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_reactivation_emails_task_sends_for_stale_calendar_without_downloads(calendar_with_dna):
    for i in range(1, 4):
        _make_post(calendar_with_dna, i)
    ContentCalendar.objects.filter(id=calendar_with_dna.id).update(
        created_at=timezone.now() - timedelta(days=4)
    )
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
    MockEmail.return_value.send_reactivation_calendar.assert_called_once()
    calendar_with_dna.refresh_from_db()
    assert calendar_with_dna.last_reactivation_email_at is not None


def test_send_reactivation_emails_task_skips_recent_calendar(calendar_with_dna):
    for i in range(1, 4):
        _make_post(calendar_with_dna, i)
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
    MockEmail.return_value.send_reactivation_calendar.assert_not_called()


def test_send_reactivation_emails_task_skips_calendar_with_a_download(calendar_with_dna):
    _make_post(calendar_with_dna, 1, downloaded_at=timezone.now())
    ContentCalendar.objects.filter(id=calendar_with_dna.id).update(
        created_at=timezone.now() - timedelta(days=4)
    )
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
    MockEmail.return_value.send_reactivation_calendar.assert_not_called()


def test_send_reactivation_emails_task_does_not_repeat_before_15_days(calendar_with_dna):
    _make_post(calendar_with_dna, 1)
    ContentCalendar.objects.filter(id=calendar_with_dna.id).update(
        created_at=timezone.now() - timedelta(days=20),
        last_reactivation_email_at=timezone.now() - timedelta(days=5),
    )
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
    MockEmail.return_value.send_reactivation_calendar.assert_not_called()


def test_send_reactivation_emails_task_repeats_after_15_days(calendar_with_dna):
    _make_post(calendar_with_dna, 1)
    ContentCalendar.objects.filter(id=calendar_with_dna.id).update(
        created_at=timezone.now() - timedelta(days=30),
        last_reactivation_email_at=timezone.now() - timedelta(days=16),
    )
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
    MockEmail.return_value.send_reactivation_calendar.assert_called_once()


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_reactivation_emails_task_sends_for_user_without_analysis():
    from django.contrib.auth import get_user_model
    from core.tenant_management.models import TenantModel, Subscription, Plan
    UserModel = get_user_model()
    plan, _ = Plan.objects.get_or_create(name='User', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    user = UserModel.objects.create_user(username='sinanalisis@test.com', email='sinanalisis@test.com', password='pass1234')
    tenant = TenantModel.objects.create(name=user.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=plan, status='active')
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    UserModel.objects.filter(id=user.id).update(date_joined=timezone.now() - timedelta(days=3))
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
    MockEmail.return_value.send_reactivation_analysis.assert_called_once()
    user.refresh_from_db()
    assert user.last_reactivation_email_at is not None


def test_send_reactivation_emails_task_skips_recent_user():
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    UserModel.objects.create_user(username='reciente@test.com', email='reciente@test.com', password='pass1234')
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
    MockEmail.return_value.send_reactivation_analysis.assert_not_called()


def test_send_reactivation_emails_task_skips_user_with_analysis(job_with_dna_and_tenant):
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    UserModel.objects.filter(id=job_with_dna_and_tenant.user.id).update(
        date_joined=timezone.now() - timedelta(days=3)
    )
    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
    MockEmail.return_value.send_reactivation_analysis.assert_not_called()


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_reactivation_emails_task_skips_tester_and_admin_plans():
    from django.contrib.auth import get_user_model
    from core.tenant_management.models import TenantModel, Subscription, Plan
    UserModel = get_user_model()
    for plan_name in ('Tester', 'Admin'):
        plan, _ = Plan.objects.get_or_create(name=plan_name, defaults={
            'max_calendars_per_week': 999, 'max_post_regenerations': 999,
            'max_post_edits': 999, 'price': 0,
        })
        user = UserModel.objects.create_user(
            username=f'{plan_name.lower()}@test.com', email=f'{plan_name.lower()}@test.com', password='pass1234'
        )
        tenant = TenantModel.objects.create(name=user.email, status='active')
        Subscription.objects.create(tenant=tenant, plan=plan, status='active')
        user.tenant = tenant
        user.save(update_fields=['tenant'])
        UserModel.objects.filter(id=user.id).update(date_joined=timezone.now() - timedelta(days=3))

    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
    MockEmail.return_value.send_reactivation_analysis.assert_not_called()


@override_settings(DEFAULT_FROM_EMAIL='noreply@cosmic.mx')
def test_send_reactivation_emails_task_skips_calendar_for_tester_and_admin_plans():
    """HALLAZGO: stale_calendars no filtraba por plan — un calendario vencido de
    Tester/Admin recibia el correo de reactivacion igual que uno de plan User."""
    from django.contrib.auth import get_user_model
    from core.tenant_management.models import TenantModel, Subscription, Plan
    UserModel = get_user_model()
    for plan_name in ('Tester', 'Admin'):
        plan, _ = Plan.objects.get_or_create(name=plan_name, defaults={
            'max_calendars_per_week': 999, 'max_post_regenerations': 999,
            'max_post_edits': 999, 'price': 0,
        })
        user = UserModel.objects.create_user(
            username=f'{plan_name.lower()}calendario@test.com',
            email=f'{plan_name.lower()}calendario@test.com', password='pass1234'
        )
        tenant = TenantModel.objects.create(name=user.email, status='active')
        Subscription.objects.create(tenant=tenant, plan=plan, status='active')
        user.tenant = tenant
        user.save(update_fields=['tenant'])

        job = AnalysisJob.objects.create(email=user.email, business_url='https://tuwebmx.com', user=user)
        dna = BrandDNA.objects.create(
            job=job, business_name=f'Negocio {plan_name}', business_url='https://tuwebmx.com',
            description='Agencia digital', keywords=['diseno'], audience='PYMEs',
            tone='profesional', primary_colors=['#1a1a2e'],
        )
        calendar = ContentCalendar.objects.create(brand_dna=dna)
        _make_post(calendar, 1)
        ContentCalendar.objects.filter(id=calendar.id).update(
            created_at=timezone.now() - timedelta(days=4)
        )

    with patch('core.content_pipeline.tasks.EmailSender') as MockEmail:
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
    MockEmail.return_value.send_reactivation_calendar.assert_not_called()

