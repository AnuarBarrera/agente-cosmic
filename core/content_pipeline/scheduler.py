import logging
from datetime import timedelta
import django_rq
from django.utils import timezone
from core.content_pipeline.models import ContentCalendar, ContentPost

logger = logging.getLogger(__name__)


def schedule_daily_emails(calendar: ContentCalendar) -> None:
    from core.content_pipeline.tasks import send_daily_email_task
    queue = django_rq.get_queue('default')
    now = timezone.now()
    posts = list(calendar.posts.filter(
        status=ContentPost.STATUS_PENDING
    ).order_by('day_number'))
    for post in posts:
        delta = post.scheduled_at - now
        if delta.total_seconds() < 300:
            delta = timedelta(minutes=5)
        queue.enqueue_in(delta, send_daily_email_task, str(post.id))
        logger.info(f"Dia {post.day_number} programado en {delta}")
