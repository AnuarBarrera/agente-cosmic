import logging
import time
from datetime import datetime as dt_datetime, timedelta, timezone as dt_timezone
from django.conf import settings
from django.utils import timezone

MEXICO_TZ = dt_timezone(timedelta(hours=-6))  # UTC-6 sin DST (desde 2023)
from core.brand_dna.models import AnalysisJob
from core.content_pipeline.models import ContentCalendar, ContentPost, WeeklyFeedback
from core.tenant_management.models import Subscription
from core.content_pipeline.generators.text_generator import TextGenerator
from core.content_pipeline.generators.image_generator import ImageGenerator
from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
from core.content_pipeline.generators.reel_generator import ReelGenerator
from core.content_pipeline.email_sender import EmailSender
from core.content_pipeline.scheduler import schedule_daily_emails
from core.content_pipeline.smart_scheduler import smart_schedule_dates
from core.shared.metrics import CONTENT_GENERATION_DURATION, CALENDARS_CREATED

logger = logging.getLogger(__name__)


def _generate_post_media(image_gen: ImageGenerator, reel_script_gen: ReelScriptGenerator, reel_gen: ReelGenerator,
                          fmt: str, filename: str, brand_dna=None, post_data: dict = None,
                          max_qc_retries: int = 2, **kwargs) -> tuple[str, list[str], str]:
    """Genera el/los medio(s) de un post segun su formato. Retorna
    (image_url, image_urls, video_url) — image_url es siempre la portada
    (slide 1 del carrusel, poster frame del reel) para retrocompatibilidad."""
    if fmt == ContentPost.FORMAT_REEL:
        script = reel_script_gen.generate(post_data, brand_dna)
        video_url, poster_url = reel_gen.generate(
            script=script, colors=kwargs.get('colors', []), filename_prefix=filename,
        )
        if not video_url:
            url = image_gen.generate(filename=filename, max_qc_retries=max_qc_retries, **kwargs)
            return url, [], ''
        return poster_url, [], video_url
    if fmt == ContentPost.FORMAT_CAROUSEL:
        urls = image_gen.generate_carousel(filename_prefix=filename, max_qc_retries=max_qc_retries, **kwargs)
        return (urls[0] if urls else ''), urls, ''
    url = image_gen.generate(filename=filename, max_qc_retries=max_qc_retries, **kwargs)
    return url, [], ''


def content_generation_task(job_id: str) -> None:
    job = AnalysisJob.objects.get(id=job_id)
    brand_dna = job.brand_dna

    start = time.monotonic()
    try:
        job.update_progress(AnalysisJob.STAGE_CONTENT, 80)

        text_gen = TextGenerator()
        posts_data = text_gen.generate(brand_dna)
        job.update_progress(AnalysisJob.STAGE_CONTENT, 87)

        calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
        CALENDARS_CREATED.inc()
        if job.user and job.user.tenant:
            Subscription.objects.filter(tenant=job.user.tenant).update(
                status='trialing',
                trial_ends_at=timezone.now() + timedelta(days=7),
            )
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        now = timezone.now()
        mexico_today = now.astimezone(MEXICO_TZ).date()

        scheduled_dates = smart_schedule_dates(brand_dna, base_date=mexico_today, count=len(posts_data))

        # Generamos las 7 imágenes por adelantado — el usuario no espera en vivo
        # (flujo async: se le avisa por correo/dashboard cuando todo está listo),
        # así que el calendario completo queda disponible desde el primer momento.
        total = len(posts_data)
        reel_script_gen = ReelScriptGenerator()
        reel_gen = ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)

        for i, post_data in enumerate(posts_data, start=1):
            scheduled = scheduled_dates[i - 1]
            image_url, image_urls, video_url = _generate_post_media(
                image_gen, reel_script_gen, reel_gen,
                fmt=post_data.get('format', ContentPost.FORMAT_SINGLE),
                filename=f"{job_id}-day{i}",
                caption=post_data['caption'],
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                audience=brand_dna.audience,
                business_url=brand_dna.business_url,
                brand_dna=brand_dna,
                post_data=post_data,
            )

            ContentPost.objects.create(
                calendar=calendar,
                day_number=i,
                caption=post_data['caption'],
                image_url=image_url,
                image_urls=image_urls,
                video_url=video_url,
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


def generate_sample_task(job_id: str) -> None:
    """Genera 1 sola pieza (imagen o reel) en vez del calendario completo —
    usado para prospeccion (ver AnalysisJob.generation_mode). Reutiliza
    TextGenerator/_generate_post_media tal cual: TextGenerator ya fija el
    formato por posicion (dia 1/indice 0 = reel via REEL_DAY, el resto =
    single salvo dia 3/indice 2 = carousel via CAROUSEL_DAY), asi que solo
    se toma el primer post que coincida con el formato pedido."""
    job = AnalysisJob.objects.get(id=job_id)
    brand_dna = job.brand_dna

    try:
        job.update_progress(AnalysisJob.STAGE_CONTENT, 80)

        text_gen = TextGenerator()
        posts_data = text_gen.generate(brand_dna)
        job.update_progress(AnalysisJob.STAGE_CONTENT, 87)

        wanted_format = (
            ContentPost.FORMAT_REEL if job.generation_mode == AnalysisJob.MODE_SAMPLE_REEL
            else ContentPost.FORMAT_SINGLE
        )
        post_data = next(p for p in posts_data if p.get('format') == wanted_format)

        calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        reel_script_gen = ReelScriptGenerator()
        reel_gen = ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)

        image_url, image_urls, video_url = _generate_post_media(
            image_gen, reel_script_gen, reel_gen,
            fmt=wanted_format,
            filename=f"{job_id}-sample",
            caption=post_data['caption'],
            colors=brand_dna.primary_colors,
            tone=brand_dna.tone,
            brand_name=brand_dna.business_name,
            keywords=brand_dna.keywords,
            description=brand_dna.description,
            audience=brand_dna.audience,
            business_url=brand_dna.business_url,
            brand_dna=brand_dna,
            post_data=post_data,
        )

        ContentPost.objects.create(
            calendar=calendar,
            day_number=1,
            caption=post_data['caption'],
            image_url=image_url,
            image_urls=image_urls,
            video_url=video_url,
            format=wanted_format,
            suggested_time='09:00',
            hashtags=post_data.get('hashtags', []),
            scheduled_at=timezone.now(),
        )

        job.stage = AnalysisJob.STAGE_COMPLETE
        job.progress = 100
        job.status = AnalysisJob.STATUS_DONE
        job.save(update_fields=['stage', 'progress', 'status'])
        logger.info(f"Muestra generada para job {job_id} ({wanted_format})")

    except Exception as e:
        logger.error(f"generate_sample_task error para job {job_id}: {e}")
        job.mark_failed(str(e))


def _generate_missing_image(post: ContentPost) -> None:
    """Genera y guarda la imagen de un post que quedo sin image_url. No lanza — loggea y sigue."""
    brand_dna = post.calendar.brand_dna
    job_id = str(brand_dna.job.id)
    try:
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        post.image_url, post.image_urls, post.video_url = _generate_post_media(
            image_gen, ReelScriptGenerator(), ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET),
            fmt=post.format,
            filename=f"{job_id}-day{post.day_number}",
            caption=post.caption,
            colors=brand_dna.primary_colors,
            tone=brand_dna.tone,
            brand_name=brand_dna.business_name,
            keywords=brand_dna.keywords,
            description=brand_dna.description,
            audience=brand_dna.audience,
            business_url=brand_dna.business_url,
            brand_dna=brand_dna,
            post_data={'caption': post.caption},
        )
        post.save(update_fields=['image_url', 'image_urls', 'video_url'])
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
        reel_script_gen = ReelScriptGenerator()
        reel_gen = ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)

        for i, post_data in enumerate(posts_data, start=1):
            scheduled = scheduled_dates[i - 1]
            image_url, image_urls, video_url = _generate_post_media(
                image_gen, reel_script_gen, reel_gen,
                fmt=post_data.get('format', ContentPost.FORMAT_SINGLE),
                filename=f"{job_id}-day{base_day + i}",
                caption=post_data['caption'],
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                audience=brand_dna.audience,
                business_url=brand_dna.business_url,
                brand_dna=brand_dna,
                post_data=post_data,
            )
            ContentPost.objects.create(
                calendar=calendar,
                day_number=base_day + i,
                caption=post_data['caption'],
                image_url=image_url,
                image_urls=image_urls,
                video_url=video_url,
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
