import logging
import os
from datetime import datetime as dt_datetime, timedelta, timezone as dt_timezone
from django.conf import settings
from django.utils import timezone

MEXICO_TZ = dt_timezone(timedelta(hours=-6))  # UTC-6 sin DST (desde 2023)
from core.brand_dna.models import AnalysisJob
from core.content_pipeline.models import ContentCalendar, ContentPost, WeeklyFeedback
from core.content_pipeline.generators.text_generator import TextGenerator
from core.content_pipeline.generators.image_generator import ImageGenerator
from core.content_pipeline.email_sender import EmailSender
from core.content_pipeline.scheduler import schedule_daily_emails
from core.content_pipeline.smart_scheduler import smart_schedule_dates
from core.content_pipeline.image_utils import normalize_image

logger = logging.getLogger(__name__)


def _load_product_images(paths: list[str]) -> list[bytes]:
    """Carga hasta 7 imágenes de producto normalizadas a WebP."""
    result = []
    for path in (paths or [])[:7]:
        full = os.path.join(settings.MEDIA_ROOT, path)
        if os.path.exists(full):
            with open(full, 'rb') as f:
                result.append(normalize_image(f.read()))
    return result


def _product_image_for_day(day_in_week: int, images: list[bytes]) -> bytes | None:
    """Asigna imagen de producto por día dentro de la semana (1-7).
    - Si hay imagen para ese día exacto: úsala.
    - Si solo hay 1 imagen: se repite el día 2 (máx 2 usos).
    - Después del día 3 sin imagen directa: sin producto.
    """
    n = len(images)
    if n == 0:
        return None
    if day_in_week <= n:
        return images[day_in_week - 1]
    if n == 1 and day_in_week == 2:
        return images[0]
    return None


def content_generation_task(job_id: str) -> None:
    job = AnalysisJob.objects.get(id=job_id)
    brand_dna = job.brand_dna

    try:
        job.update_progress(AnalysisJob.STAGE_CONTENT, 80)

        text_gen = TextGenerator()
        posts_data = text_gen.generate(brand_dna)
        job.update_progress(AnalysisJob.STAGE_CONTENT, 87)

        calendar = ContentCalendar.objects.create(
            brand_dna=brand_dna,
            active_product_images=job.product_image_paths[:7],
        )
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        now = timezone.now()
        mexico_today = now.astimezone(MEXICO_TZ).date()

        scheduled_dates = smart_schedule_dates(brand_dna, base_date=mexico_today, count=len(posts_data))

        # Cargar imágenes de producto (hasta 7, una por día)
        product_images_bytes = _load_product_images(calendar.active_product_images)

        for i, post_data in enumerate(posts_data, start=1):
            hour, minute = map(int, post_data.get('suggested_time', '19:00').split(':'))
            scheduled = scheduled_dates[i - 1]

            day_product = _product_image_for_day(i, product_images_bytes)
            if i == 1:
                image_url = image_gen.generate(
                    caption=post_data['caption'],
                    colors=brand_dna.primary_colors,
                    tone=brand_dna.tone,
                    filename=f"{job_id}-day{i}",
                    brand_name=brand_dna.business_name,
                    keywords=brand_dna.keywords,
                    description=brand_dna.description,
                    product_image_bytes=day_product,
                )
            else:
                image_url = ''

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
    if post.calendar.brand_dna.job.deleted_at is not None:
        logger.info(f"Post {post_id} omitido — calendario eliminado por el usuario")
        return
    # Genera la imagen justo antes de enviar (solo si no fue generada antes)
    if not post.image_url:
        brand_dna = post.calendar.brand_dna
        job_id = str(brand_dna.job.id)
        day_in_week = ((post.day_number - 1) % 7) + 1
        try:
            image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
            product_images = _load_product_images(post.calendar.active_product_images)
            product_image_bytes = _product_image_for_day(day_in_week, product_images)
            post.image_url = image_gen.generate(
                caption=post.caption,
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                filename=f"{job_id}-day{post.day_number}",
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                product_image_bytes=product_image_bytes,
            )
            post.save(update_fields=['image_url'])
        except Exception as img_err:
            logger.warning(f"Imagen día {post.day_number} falló (no fatal): {img_err}")
    EmailSender().send_daily(post=post)

    if post.day_number % 7 == 0:
        week_number = post.day_number // 7
        WeeklyFeedback.objects.get_or_create(calendar=post.calendar, week_number=week_number)


def generate_next_week(calendar: ContentCalendar, week_number: int) -> None:
    brand_dna = calendar.brand_dna
    text_gen = TextGenerator()
    posts_data = text_gen.generate(brand_dna)

    now = timezone.now()
    mexico_today = now.astimezone(MEXICO_TZ).date()
    scheduled_dates = smart_schedule_dates(brand_dna, base_date=mexico_today, count=len(posts_data))

    base_day = (week_number - 1) * 7

    for i, post_data in enumerate(posts_data, start=1):
        hour, minute = map(int, post_data.get('suggested_time', '19:00').split(':'))
        ContentPost.objects.create(
            calendar=calendar,
            day_number=base_day + i,
            caption=post_data['caption'],
            image_url='',
            suggested_time=f"{hour:02d}:{minute:02d}",
            hashtags=post_data.get('hashtags', []),
            scheduled_at=scheduled_dates[i - 1],
        )

    schedule_daily_emails(calendar)
