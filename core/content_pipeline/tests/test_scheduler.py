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


def test_schedule_daily_emails_enqueues_6_jobs(calendar_with_7_posts):
    with patch('core.content_pipeline.scheduler.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue

        from core.content_pipeline.scheduler import schedule_daily_emails
        schedule_daily_emails(calendar_with_7_posts)

    assert mock_queue.enqueue_in.call_count == 6


def test_schedule_daily_emails_skips_day_1(calendar_with_7_posts):
    with patch('core.content_pipeline.scheduler.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue

        from core.content_pipeline.scheduler import schedule_daily_emails
        schedule_daily_emails(calendar_with_7_posts)

    calls = mock_queue.enqueue_in.call_args_list
    for call in calls:
        post_id = str(call[0][2])
        post = ContentPost.objects.get(id=post_id)
        assert post.day_number != 1
