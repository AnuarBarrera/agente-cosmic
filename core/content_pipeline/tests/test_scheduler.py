import pytest
from unittest.mock import patch, MagicMock
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost

pytestmark = pytest.mark.django_db


@pytest.fixture
def calendar_with_7_posts():
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://tuwebmx.com')
    dna = BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Test', keywords=[], audience='Test', tone='profesional', primary_colors=[],
    )
    cal = ContentCalendar.objects.create(brand_dna=dna)
    for i in range(1, 8):
        ContentPost.objects.create(
            calendar=cal, day_number=i, caption=f'Post {i}',
            image_url='https://example.com/img.jpg', suggested_time='19:00',
            hashtags=[], scheduled_at=timezone.now() + timedelta(days=i),
        )
    return cal


def test_schedule_daily_emails_enqueues_7_jobs(calendar_with_7_posts):
    with patch('core.content_pipeline.scheduler.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue

        from core.content_pipeline.scheduler import schedule_daily_emails
        schedule_daily_emails(calendar_with_7_posts)

    assert mock_queue.enqueue_in.call_count == 7


def test_schedule_daily_emails_includes_day_1(calendar_with_7_posts):
    with patch('core.content_pipeline.scheduler.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue

        from core.content_pipeline.scheduler import schedule_daily_emails
        schedule_daily_emails(calendar_with_7_posts)

    calls = mock_queue.enqueue_in.call_args_list
    scheduled_days = [ContentPost.objects.get(id=str(call[0][2])).day_number for call in calls]
    assert 1 in scheduled_days


def test_schedule_daily_emails_clamps_past_due_to_5_minutes(calendar_with_7_posts):
    post1 = calendar_with_7_posts.posts.get(day_number=1)
    post1.scheduled_at = timezone.now() - timedelta(hours=10)
    post1.save(update_fields=['scheduled_at'])

    with patch('core.content_pipeline.scheduler.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue

        from core.content_pipeline.scheduler import schedule_daily_emails
        schedule_daily_emails(calendar_with_7_posts)

    calls = mock_queue.enqueue_in.call_args_list
    day1_call = next(c for c in calls if str(c[0][2]) == str(post1.id))
    assert day1_call[0][0] == timedelta(minutes=5)


def test_schedule_daily_emails_does_not_reschedule_sent_posts(calendar_with_7_posts):
    # Marcar días 1-7 como ya enviados (semana 1 completada)
    for post in calendar_with_7_posts.posts.all():
        post.status = ContentPost.STATUS_SENT
        post.save(update_fields=['status'])

    # Agregar semana 2 (días 8-14), pendientes
    for i in range(8, 15):
        ContentPost.objects.create(
            calendar=calendar_with_7_posts, day_number=i, caption=f'Post {i}',
            image_url='', suggested_time='19:00', hashtags=[],
            scheduled_at=timezone.now() + timedelta(days=i),
        )

    with patch('core.content_pipeline.scheduler.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue

        from core.content_pipeline.scheduler import schedule_daily_emails
        schedule_daily_emails(calendar_with_7_posts)

    scheduled_days = []
    for call in mock_queue.enqueue_in.call_args_list:
        post_id = str(call[0][2])
        post = ContentPost.objects.get(id=post_id)
        scheduled_days.append(post.day_number)

    assert sorted(scheduled_days) == list(range(8, 15))
