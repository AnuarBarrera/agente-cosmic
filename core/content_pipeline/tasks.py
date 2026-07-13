import logging
import time
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
from core.shared.gcs_uploads import read_upload, upload_exists
from core.shared.metrics import CONTENT_GENERATION_DURATION, CALENDARS_CREATED

logger = logging.getLogger(__name__)


def _load_product_images(paths: list[str]) -> list[bytes]:
    """Carga hasta 7 imágenes de producto desde GCS, normalizadas a WebP."""
    result = []
    for path in (paths or [])[:7]:
        try:
            if upload_exists(path):
                result.append(normalize_image(read_upload(path)))
            else:
                logger.warning(f"Producto no encontrado en GCS: {path}")
        except Exception as e:
            logger.warning(f"Error cargando imagen de producto {path}: {e}")
    return result


def _generate_post_media(image_gen: ImageGenerator, fmt: str, filename: str, max_qc_retries: int = 2, **kwargs) -> tuple[str, list[str]]:
    """Genera la imagen (o slides del carrusel, H20 + roadmap #5) de un post.
    Retorna (image_url, image_urls) — image_url es siempre la portada/slide 1
    para retrocompatibilidad (email, thumbnail, descarga por default)."""
    if fmt == ContentPost.FORMAT_CAROUSEL:
        urls = image_gen.generate_carousel(filename_prefix=filename, max_qc_retries=max_qc_retries, **kwargs)
        return (urls[0] if urls else ''), urls
    url = image_gen.generate(filename=filename, max_qc_retries=max_qc_retries, **kwargs)
    return url, []


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


def _disable_carousel_if_full_product_week(posts_data: list[dict], product_images_bytes: list[bytes]) -> None:
    """Si el usuario subio una foto de producto por cada dia de la semana (7),
    el carrusel (que usa un fondo generado por IA) le restaria protagonismo a
    esas fotos — el usuario quiere mostrar SUS productos ese dia, no
    contenido generico. En ese caso, todos los posts se generan como 'single'."""
    if len(product_images_bytes) == 7:
        for post in posts_data:
            post['format'] = ContentPost.FORMAT_SINGLE


def content_generation_task(job_id: str) -> None:
    job = AnalysisJob.objects.get(id=job_id)
    brand_dna = job.brand_dna

    start = time.monotonic()
    try:
        job.update_progress(AnalysisJob.STAGE_CONTENT, 80)

        text_gen = TextGenerator()
        posts_data = text_gen.generate(brand_dna)
        job.update_progress(AnalysisJob.STAGE_CONTENT, 87)

        calendar = ContentCalendar.objects.create(
            brand_dna=brand_dna,
            active_product_images=job.product_image_paths[:7],
        )
        CALENDARS_CREATED.inc()
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        now = timezone.now()
        mexico_today = now.astimezone(MEXICO_TZ).date()

        scheduled_dates = smart_schedule_dates(brand_dna, base_date=mexico_today, count=len(posts_data))

        # Cargar imágenes de producto (hasta 7, una por día)
        product_images_bytes = _load_product_images(calendar.active_product_images)
        _disable_carousel_if_full_product_week(posts_data, product_images_bytes)

        # Generamos las 7 imágenes por adelantado — el usuario no espera en vivo
        # (flujo async: se le avisa por correo/dashboard cuando todo está listo),
        # así que el calendario completo queda disponible desde el primer momento.
        total = len(posts_data)
        for i, post_data in enumerate(posts_data, start=1):
            scheduled = scheduled_dates[i - 1]
            day_product = _product_image_for_day(i, product_images_bytes)
            image_url, image_urls = _generate_post_media(
                image_gen,
                fmt=post_data.get('format', ContentPost.FORMAT_SINGLE),
                filename=f"{job_id}-day{i}",
                caption=post_data['caption'],
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                audience=brand_dna.audience,
                product_image_bytes=day_product,
            )

            ContentPost.objects.create(
                calendar=calendar,
                day_number=i,
                caption=post_data['caption'],
                image_url=image_url,
                image_urls=image_urls,
                format=post_data.get('format', ContentPost.FORMAT_SINGLE),
                suggested_time=scheduled.astimezone(MEXICO_TZ).time(),
                hashtags=post_data.get('hashtags', []),
                scheduled_at=scheduled,
            )
            job.update_progress(AnalysisJob.STAGE_CONTENT, 87 + int(8 * i / total))

        try:
            EmailSender().send_initial(job=job, brand_dna=brand_dna)
            schedule_daily_emails(calendar)
        except Exception as email_err:
            logger.error(f"Email falló para job {job_id} (no fatal): {email_err}")

        job.stage = AnalysisJob.STAGE_COMPLETE
        job.progress = 100
        job.status = AnalysisJob.STATUS_DONE
        job.save(update_fields=['stage', 'progress', 'status'])
        CONTENT_GENERATION_DURATION.observe(time.monotonic() - start)
        logger.info(f"Job {job_id} completado exitosamente")

    except Exception as e:
        CONTENT_GENERATION_DURATION.observe(time.monotonic() - start)
        logger.error(f"content_generation_task error para job {job_id}: {e}")
        job.mark_failed(str(e))


def _generate_missing_image(post: ContentPost) -> None:
    """Genera y guarda la imagen de un post que quedo sin image_url. No lanza — loggea y sigue."""
    brand_dna = post.calendar.brand_dna
    job_id = str(brand_dna.job.id)
    day_in_week = ((post.day_number - 1) % 7) + 1
    try:
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        product_images = _load_product_images(post.calendar.active_product_images)
        product_image_bytes = _product_image_for_day(day_in_week, product_images)
        post.image_url, post.image_urls = _generate_post_media(
            image_gen,
            fmt=post.format,
            filename=f"{job_id}-day{post.day_number}",
            caption=post.caption,
            colors=brand_dna.primary_colors,
            tone=brand_dna.tone,
            brand_name=brand_dna.business_name,
            keywords=brand_dna.keywords,
            description=brand_dna.description,
            audience=brand_dna.audience,
            product_image_bytes=product_image_bytes,
        )
        post.save(update_fields=['image_url', 'image_urls'])
    except Exception as img_err:
        logger.warning(f"Imagen día {post.day_number} falló (no fatal): {img_err}")


def backfill_image_task(post_id: str) -> None:
    """Genera la imagen de un post existente que quedo pendiente (arquitectura previa a H2/H3)."""
    post = ContentPost.objects.select_related('calendar__brand_dna__job').get(id=post_id)
    if post.calendar.brand_dna.job.deleted_at is not None:
        logger.info(f"Post {post_id} omitido — calendario eliminado por el usuario")
        return
    if post.image_url:
        logger.info(f"Post {post_id} ya tiene imagen — nada que hacer")
        return
    _generate_missing_image(post)


def send_daily_email_task(post_id: str) -> None:
    post = ContentPost.objects.select_related('calendar__brand_dna__job').get(id=post_id)
    if post.calendar.brand_dna.job.deleted_at is not None:
        logger.info(f"Post {post_id} omitido — calendario eliminado por el usuario")
        return
    # Fallback defensivo: las imágenes ya se generan todas en content_generation_task,
    # esto solo cubre el caso raro de que una generación individual haya fallado.
    if not post.image_url:
        _generate_missing_image(post)
    EmailSender().send_daily(post=post)

    if post.day_number % 7 == 0:
        week_number = post.day_number // 7
        WeeklyFeedback.objects.get_or_create(calendar=post.calendar, week_number=week_number)


def generate_next_week(calendar_id: str, week_number: int) -> None:
    calendar = ContentCalendar.objects.get(id=calendar_id)
    brand_dna = calendar.brand_dna
    job_id = str(brand_dna.job.id)
    try:
        text_gen = TextGenerator()
        posts_data = text_gen.generate(brand_dna)

        now = timezone.now()
        mexico_today = now.astimezone(MEXICO_TZ).date()
        last_post = calendar.posts.order_by('-day_number').first()
        if last_post:
            day_after_last = last_post.scheduled_at.astimezone(MEXICO_TZ).date() + timedelta(days=1)
            base_date = max(mexico_today, day_after_last)
        else:
            base_date = mexico_today
        scheduled_dates = smart_schedule_dates(brand_dna, base_date=base_date, count=len(posts_data))

        base_day = (week_number - 1) * 7
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        product_images_bytes = _load_product_images(calendar.active_product_images)
        _disable_carousel_if_full_product_week(posts_data, product_images_bytes)

        for i, post_data in enumerate(posts_data, start=1):
            scheduled = scheduled_dates[i - 1]
            day_product = _product_image_for_day(i, product_images_bytes)
            image_url, image_urls = _generate_post_media(
                image_gen,
                fmt=post_data.get('format', ContentPost.FORMAT_SINGLE),
                filename=f"{job_id}-day{base_day + i}",
                caption=post_data['caption'],
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                audience=brand_dna.audience,
                product_image_bytes=day_product,
            )
            ContentPost.objects.create(
                calendar=calendar,
                day_number=base_day + i,
                caption=post_data['caption'],
                image_url=image_url,
                image_urls=image_urls,
                format=post_data.get('format', ContentPost.FORMAT_SINGLE),
                suggested_time=scheduled.astimezone(MEXICO_TZ).time(),
                hashtags=post_data.get('hashtags', []),
                scheduled_at=scheduled,
            )

        schedule_daily_emails(calendar)

        try:
            EmailSender().send_week_ready(job=brand_dna.job, brand_dna=brand_dna, week_number=week_number)
        except Exception as email_err:
            logger.error(f"Email de semana lista falló para calendar {calendar_id} (no fatal): {email_err}")
    except Exception as e:
        logger.error(f"generate_next_week error para calendar {calendar_id}, semana {week_number}: {e}")
    finally:
        calendar.next_week_generating = False
        calendar.save(update_fields=['next_week_generating'])
