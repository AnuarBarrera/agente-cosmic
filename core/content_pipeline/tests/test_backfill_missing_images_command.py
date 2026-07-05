import pytest
from io import StringIO
from unittest.mock import patch
from django.core.management import call_command
from django.utils import timezone
from datetime import timedelta
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost

pytestmark = pytest.mark.django_db


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
        caption=f'Post {day_number}', image_url='', suggested_time='19:00',
        hashtags=[], scheduled_at=timezone.now() + timedelta(days=day_number),
    )
    defaults.update(kwargs)
    return ContentPost.objects.create(calendar=calendar, day_number=day_number, **defaults)


def test_dry_run_lists_posts_without_enqueueing(calendar_with_dna):
    _make_post(calendar_with_dna, 2, image_url='')
    _make_post(calendar_with_dna, 3, image_url='https://example.com/already.jpg')

    out = StringIO()
    with patch('django_rq.get_queue') as mock_queue:
        call_command('backfill_missing_images', '--dry-run', stdout=out)
    mock_queue.assert_not_called()
    assert 'dia 2' in out.getvalue()
    assert 'dia 3' not in out.getvalue()


def test_enqueues_only_posts_missing_image(calendar_with_dna):
    post_missing = _make_post(calendar_with_dna, 2, image_url='')
    _make_post(calendar_with_dna, 3, image_url='https://example.com/already.jpg')

    out = StringIO()
    with patch('core.content_pipeline.management.commands.backfill_missing_images.django_rq') as mock_rq:
        call_command('backfill_missing_images', stdout=out)

    mock_rq.get_queue.return_value.enqueue.assert_called_once()
    args = mock_rq.get_queue.return_value.enqueue.call_args[0]
    assert args[1] == str(post_missing.id)
    assert 'Encolados 1' in out.getvalue()


def test_skips_deleted_calendars(calendar_with_dna):
    job = calendar_with_dna.brand_dna.job
    job.deleted_at = timezone.now()
    job.save(update_fields=['deleted_at'])
    _make_post(calendar_with_dna, 2, image_url='')

    out = StringIO()
    with patch('core.content_pipeline.management.commands.backfill_missing_images.django_rq') as mock_rq:
        call_command('backfill_missing_images', stdout=out)

    mock_rq.get_queue.return_value.enqueue.assert_not_called()
    assert 'No hay posts pendientes' in out.getvalue()


def test_no_pending_posts_reports_nothing_to_do(calendar_with_dna):
    _make_post(calendar_with_dna, 2, image_url='https://example.com/already.jpg')

    out = StringIO()
    with patch('core.content_pipeline.management.commands.backfill_missing_images.django_rq') as mock_rq:
        call_command('backfill_missing_images', stdout=out)

    mock_rq.get_queue.return_value.enqueue.assert_not_called()
    assert 'No hay posts pendientes' in out.getvalue()
