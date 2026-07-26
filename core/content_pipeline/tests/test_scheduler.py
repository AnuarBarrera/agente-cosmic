import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta, time as dt_time
from django.utils import timezone
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


def test_schedule_daily_emails_enqueues_jobs_within_range(calendar_with_7_posts):
    with patch('core.content_pipeline.scheduler.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue

        from core.content_pipeline.scheduler import schedule_daily_emails
        schedule_daily_emails(calendar_with_7_posts, day_start=1, day_end=7)

    assert mock_queue.enqueue_in.call_count == 7


def test_schedule_daily_emails_includes_day_1(calendar_with_7_posts):
    with patch('core.content_pipeline.scheduler.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue

        from core.content_pipeline.scheduler import schedule_daily_emails
        schedule_daily_emails(calendar_with_7_posts, day_start=1, day_end=7)

    calls = mock_queue.enqueue_in.call_args_list
    scheduled_days = [ContentPost.objects.get(id=str(call[0][2])).day_number for call in calls]
    assert 1 in scheduled_days


def test_schedule_daily_emails_excludes_days_outside_range(calendar_with_7_posts):
    with patch('core.content_pipeline.scheduler.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue

        from core.content_pipeline.scheduler import schedule_daily_emails
        schedule_daily_emails(calendar_with_7_posts, day_start=1, day_end=3)

    calls = mock_queue.enqueue_in.call_args_list
    scheduled_days = [ContentPost.objects.get(id=str(call[0][2])).day_number for call in calls]
    assert sorted(scheduled_days) == [1, 2, 3]


def test_schedule_daily_emails_two_calls_with_different_ranges_do_not_overlap(calendar_with_7_posts):
    # Reproduce HALLAZGO 81: _trial_closing_task programa dias 1-7, luego
    # generate_next_month programa dias 8-14 sobre el MISMO calendario — dias 1-7
    # deben seguir sin re-encolarse aunque sigan PENDING.
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
        schedule_daily_emails(calendar_with_7_posts, day_start=1, day_end=7)
        schedule_daily_emails(calendar_with_7_posts, day_start=8, day_end=14)

    scheduled_post_ids = [str(call[0][2]) for call in mock_queue.enqueue_in.call_args_list]
    assert len(scheduled_post_ids) == 14
    assert len(set(scheduled_post_ids)) == 14


def test_schedule_daily_emails_targets_7am_mexico_time_for_future_post(calendar_with_7_posts):
    from core.content_pipeline.scheduler import MEXICO_TZ, _REMINDER_HOUR_MEXICO
    post1 = calendar_with_7_posts.posts.get(day_number=1)

    with patch('core.content_pipeline.scheduler.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue

        from core.content_pipeline.scheduler import schedule_daily_emails
        schedule_daily_emails(calendar_with_7_posts, day_start=1, day_end=7)

    calls = mock_queue.enqueue_in.call_args_list
    day1_call = next(c for c in calls if str(c[0][2]) == str(post1.id))
    delta = day1_call[0][0]

    expected_target = datetime.combine(
        post1.scheduled_at.astimezone(MEXICO_TZ).date(),
        dt_time(_REMINDER_HOUR_MEXICO, 0),
        tzinfo=MEXICO_TZ,
    )
    actual_target = timezone.now() + delta
    # Tolerancia de 5s por el tiempo real transcurrido entre el now() interno de
    # schedule_daily_emails y el now() de esta aserción.
    assert abs((actual_target - expected_target).total_seconds()) < 5


def test_schedule_daily_emails_falls_back_to_2_hours_when_7am_already_passed(calendar_with_7_posts):
    post1 = calendar_with_7_posts.posts.get(day_number=1)
    post1.scheduled_at = timezone.now() - timedelta(hours=10)
    post1.save(update_fields=['scheduled_at'])

    with patch('core.content_pipeline.scheduler.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue

        from core.content_pipeline.scheduler import schedule_daily_emails
        schedule_daily_emails(calendar_with_7_posts, day_start=1, day_end=7)

    calls = mock_queue.enqueue_in.call_args_list
    day1_call = next(c for c in calls if str(c[0][2]) == str(post1.id))
    assert day1_call[0][0] == timedelta(hours=2)


def test_schedule_daily_emails_does_not_reschedule_sent_posts(calendar_with_7_posts):
    for post in calendar_with_7_posts.posts.all():
        post.status = ContentPost.STATUS_SENT
        post.save(update_fields=['status'])

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
        schedule_daily_emails(calendar_with_7_posts, day_start=1, day_end=14)

    scheduled_days = []
    for call in mock_queue.enqueue_in.call_args_list:
        post_id = str(call[0][2])
        post = ContentPost.objects.get(id=post_id)
        scheduled_days.append(post.day_number)

    assert sorted(scheduled_days) == list(range(8, 15))
