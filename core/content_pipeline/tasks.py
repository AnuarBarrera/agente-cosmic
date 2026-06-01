import logging
import time
from datetime import timedelta
from django.conf import settings
from django.utils import timezone
from core.brand_dna.models import AnalysisJob
from core.content_pipeline.models import ContentCalendar, ContentPost
from core.content_pipeline.generators.text_generator import TextGenerator
from core.content_pipeline.generators.image_generator import ImageGenerator
from core.content_pipeline.email_sender import EmailSender
from core.content_pipeline.scheduler import schedule_daily_emails

logger = logging.getLogger(__name__)


def content_generation_task(job_id: str) -> None:
    job = AnalysisJob.objects.get(id=job_id)
    brand_dna = job.brand_dna

    try:
        job.update_progress(AnalysisJob.STAGE_CONTENT, 80)

        text_gen = TextGenerator()
        posts_data = text_gen.generate(brand_dna)
        job.update_progress(AnalysisJob.STAGE_CONTENT, 87)

        calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        now = timezone.now()

        for i, post_data in enumerate(posts_data, start=1):
            hour, minute = map(int, post_data.get('suggested_time', '19:00').split(':'))
            scheduled = (now + timedelta(days=i)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            if i > 1:
                time.sleep(3)
            image_url = image_gen.generate(
                caption=post_data['caption'],
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                filename=f"{job_id}-day{i}",
            )
            ContentPost.objects.create(
                calendar=calendar,
                day_number=i,
                caption=post_data['caption'],
                image_url=image_url,
                suggested_time=f"{hour:02d}:{minute:02d}",
                hashtags=post_data.get('hashtags', []),
                scheduled_at=scheduled,
            )

        job.update_progress(AnalysisJob.STAGE_CONTENT, 95)

        try:
            EmailSender().send_initial(job=job, brand_dna=brand_dna, calendar=calendar)
            schedule_daily_emails(calendar)
        except Exception as email_err:
            logger.error(f"Email falló para job {job_id} (no fatal): {email_err}")

        job.stage = AnalysisJob.STAGE_COMPLETE
        job.progress = 100
        job.status = AnalysisJob.STATUS_DONE
        job.save(update_fields=['stage', 'progress', 'status'])
        logger.info(f"Job {job_id} completado exitosamente")

    except Exception as e:
        logger.error(f"content_generation_task error para job {job_id}: {e}")
        job.mark_failed(str(e))


def send_daily_email_task(post_id: str) -> None:
    post = ContentPost.objects.select_related('calendar__brand_dna__job').get(id=post_id)
    EmailSender().send_daily(post=post)
