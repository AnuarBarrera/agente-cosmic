import logging
from datetime import datetime as dt_datetime, timedelta, timezone as dt_timezone, time as dt_time
import django_rq
from django.utils import timezone
from core.content_pipeline.models import ContentCalendar, ContentPost

logger = logging.getLogger(__name__)

MEXICO_TZ = dt_timezone(timedelta(hours=-6))  # UTC-6 sin DST (desde 2023) — mismo offset que tasks.py/smart_scheduler.py
_REMINDER_HOUR_MEXICO = 7
_REMINDER_FALLBACK_DELAY = timedelta(hours=2)


def schedule_daily_emails(calendar: ContentCalendar, day_start: int, day_end: int) -> None:
    from core.content_pipeline.tasks import send_daily_email_task
    queue = django_rq.get_queue('default')
    now = timezone.now()
    posts = list(calendar.posts.filter(
        status=ContentPost.STATUS_PENDING,
        day_number__gte=day_start,
        day_number__lte=day_end,
    ).order_by('day_number'))
    for post in posts:
        post_date_mx = post.scheduled_at.astimezone(MEXICO_TZ).date()
        target = dt_datetime.combine(post_date_mx, dt_time(_REMINDER_HOUR_MEXICO, 0), tzinfo=MEXICO_TZ)
        delta = target - now
        if delta < _REMINDER_FALLBACK_DELAY:
            delta = _REMINDER_FALLBACK_DELAY
        queue.enqueue_in(delta, send_daily_email_task, str(post.id))
        logger.info(f"Dia {post.day_number} programado en {delta} (objetivo 7am CDMX {post_date_mx})")
