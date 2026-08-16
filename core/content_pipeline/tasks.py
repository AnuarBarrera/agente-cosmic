import logging
import time
from datetime import datetime as dt_datetime, timedelta, timezone as dt_timezone
from django.conf import settings
from django.utils import timezone
from django.db.models import Count
import django_rq
from rq import Retry
from rq.job import Dependency

MEXICO_TZ = dt_timezone(timedelta(hours=-6))  # UTC-6 sin DST (desde 2023)
from core.brand_dna.models import AnalysisJob
from core.content_pipeline.models import ContentCalendar, ContentPost
from core.tenant_management.models import Subscription, User
from core.content_pipeline.generators.text_generator import TextGenerator
from core.content_pipeline.generators.image_generator import ImageGenerator, _detect_mime
from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
from core.content_pipeline.generators.reel_generator import ReelGenerator
from core.shared.gcs_uploads import read_upload, read_upload_from_public_url, upload_exists
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

    started_at = time.time()
    try:
        job.update_progress(AnalysisJob.STAGE_CONTENT, 80)

        text_gen = TextGenerator()
        posts_data = text_gen.generate(brand_dna)
        job.update_progress(AnalysisJob.STAGE_CONTENT, 87)

        calendar = ContentCalendar.objects.create(brand_dna=brand_dna)
        CALENDARS_CREATED.inc()
        if job.user and job.user.tenant:
            Subscription.objects.filter(tenant=job.user.tenant, plan__name='User').update(
                status='trialing',
                trial_ends_at=timezone.now() + timedelta(days=7),
            )

        now = timezone.now()
        mexico_today = now.astimezone(MEXICO_TZ).date()
        scheduled_dates = smart_schedule_dates(brand_dna, base_date=mexico_today, count=len(posts_data))

        for i, post_data in enumerate(posts_data, start=1):
            scheduled = scheduled_dates[i - 1]
            ContentPost.objects.create(
                calendar=calendar,
                day_number=i,
                caption=post_data['caption'],
                image_url='',
                image_urls=[],
                video_url='',
                format=post_data.get('format', ContentPost.FORMAT_SINGLE),
                suggested_time=scheduled.astimezone(MEXICO_TZ).time(),
                hashtags=post_data.get('hashtags', []),
                scheduled_at=scheduled,
            )

        _enqueue_trial_images(job_id, str(calendar.id), started_at)
        logger.info(f"Job {job_id}: texto listo, encadenando generación de imágenes")

    except Exception as e:
        CONTENT_GENERATION_DURATION.observe(time.time() - started_at)
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

        # upload_exists() espeja el guard de analyze_brand_task sobre esta misma
        # foto: si el blob ya no esta en GCS, read_upload lanzaria y el job
        # ENTERO se marcaria failed en vez de degradar al camino normal (imagen
        # diseñada sin foto), que es el comportamiento por defecto del spec.
        if (wanted_format == ContentPost.FORMAT_SINGLE and job.product_reference_image_path
                and upload_exists(job.product_reference_image_path)):
            photo_bytes = read_upload(job.product_reference_image_path)
            background_url, image_url = image_gen.generate_from_product_photo(
                # mime real por magic bytes, no 'image/jpeg' hardcodeado: el
                # frontend recomprime a JPEG casi siempre, pero el fallback de
                # img.onerror (HEIC, imagen corrupta) y el POST sin JS no.
                photo_bytes=photo_bytes, mime_type=_detect_mime(photo_bytes),
                caption=post_data['caption'], colors=brand_dna.primary_colors,
                tone=brand_dna.tone, filename=f"{job_id}-sample",
                vision_context=brand_dna.product_photo_analysis,
                description=brand_dna.description, keywords=brand_dna.keywords,
                business_url=brand_dna.business_url,
            )
            image_urls, video_url = [], ''
        elif (wanted_format == ContentPost.FORMAT_REEL and job.product_reference_image_path
                and upload_exists(job.product_reference_image_path)):
            photo_bytes = read_upload(job.product_reference_image_path)
            script = reel_script_gen.generate(post_data, brand_dna)
            video_url, image_url = reel_gen.generate_from_product_photo(
                image_gen, photo_bytes, _detect_mime(photo_bytes), script,
                brand_dna.primary_colors, f"{job_id}-sample",
            )
            if not video_url:
                # Mismo fallback que ya usa _generate_post_media para el reel SIN
                # foto: si el reel falla completo, degradar a una imagen generada
                # desde cero en vez de dejar el post sin ningun medio.
                image_url = image_gen.generate(
                    caption=post_data['caption'], colors=brand_dna.primary_colors,
                    tone=brand_dna.tone, filename=f"{job_id}-sample",
                    brand_name=brand_dna.business_name, keywords=brand_dna.keywords,
                    description=brand_dna.description, audience=brand_dna.audience,
                    business_url=brand_dna.business_url,
                )
            image_urls, background_url = [], ''
        else:
            background_url = ''
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
            product_photo_background_url=background_url,
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


def _is_paid_content(post: ContentPost) -> bool:
    """True SOLO si el tenant tiene plan='User' Y Stripe ya confirmo el pago
    (status='active', ver stripe_views.py) -- False para el trial gratis
    ('trialing'), para Tester/Admin (sin importar su status), y por defecto
    ante cualquier dato faltante, para nunca facturar por error contra Gemini
    API. Decision de Anuar 2026-08-14: la generacion de imagen del plan
    pagado usa Gemini API (dinero real de usuarios), todo lo demas
    (trial gratis, Tester, Admin) se queda en Vertex (creditos de GCP).

    HALLAZGO (2026-08-15): status='active' por si solo NO alcanza -- Tester y
    Admin tambien terminan con status='active' (provision_tenant() en
    auth_views.py no fija status explicito, cae al default del modelo;
    InvitationCode.redeem() en tenant_management/models.py solo cambia
    `plan`, nunca `status`). Sin el filtro de plan.name=='User' aqui, Tester/
    Admin habrian caido en Gemini API igual que un pago real."""
    try:
        subscription = post.calendar.brand_dna.job.user.tenant.subscription
        return subscription.plan.name == 'User' and subscription.status == 'active'
    except Exception:
        return False


def _generate_missing_image(post: ContentPost) -> None:
    """Genera y guarda la imagen de un post que quedo sin image_url. No lanza — loggea y sigue."""
    brand_dna = post.calendar.brand_dna
    job_id = str(brand_dna.job.id)
    try:
        use_gemini_api = _is_paid_content(post)
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET, use_gemini_api=use_gemini_api)
        post.image_url, post.image_urls, post.video_url = _generate_post_media(
            image_gen, ReelScriptGenerator(), ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET, use_gemini_api=use_gemini_api),
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


def regenerate_post_image_task(post_id: str, feedback: str) -> None:
    """Regeneracion async con foto real -- ver ImageGenerator.regenerate_with_reference.
    Sincrono era inviable: 1 rpm en Vertex + hasta 3 reintentos de QC pueden
    tardar varios minutos, mucho para un request HTTP. Decision de Anuar
    2026-08-16."""
    try:
        # El get() va DENTRO del try a proposito: si falla (blip transitorio de
        # DB), la limpieza del flag de abajo no depende de tener el objeto en
        # memoria -- si no, la fila quedaba con regenerating=True para siempre y
        # el guard de reentrada de views.py bloqueaba ese post permanentemente.
        post = ContentPost.objects.select_related('calendar__brand_dna__job').get(id=post_id)
        brand_dna = post.calendar.brand_dna
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        current_background_bytes = read_upload_from_public_url(
            post.product_photo_background_url or post.image_url
        )
        background_url, new_url = image_gen.regenerate_with_reference(
            current_background_bytes=current_background_bytes,
            feedback=feedback,
            vision_context=brand_dna.product_photo_analysis,
            caption=post.caption,
            colors=brand_dna.primary_colors,
            tone=brand_dna.tone,
            description=brand_dna.description,
            keywords=brand_dna.keywords,
            business_url=brand_dna.business_url,
            filename=f"{brand_dna.job.id}-day{post.day_number}-regen-{int(time.time())}",
        )
        if new_url:
            post.image_url = new_url
            post.image_urls = []
            post.product_photo_background_url = background_url
        post.regenerating = False
        post.save(update_fields=['image_url', 'image_urls', 'product_photo_background_url', 'regenerating'])
    except Exception as e:
        logger.error(f"regenerate_post_image_task error para post {post_id}: {e}")
        ContentPost.objects.filter(id=post_id).update(regenerating=False)


def send_daily_email_task(post_id: str) -> None:
    post = ContentPost.objects.select_related('calendar__brand_dna__job').get(id=post_id)
    if post.calendar.brand_dna.job.deleted_at is not None:
        logger.info(f"Post {post_id} omitido — calendario eliminado por el usuario")
        return
    if post.downloaded_at is not None:
        logger.info(f"Post {post_id} ya descargado — se omite el correo diario")
        return
    # Fallback defensivo: las imágenes ya se generan todas en content_generation_task,
    # esto solo cubre el caso raro de que una generación individual haya fallado.
    if not post.image_url:
        _generate_missing_image(post)
    EmailSender().send_daily(post=post)


def _enqueue_post_images_then(post_ids: list, closing_fn, *closing_args) -> None:
    jobs = []
    for post_id in post_ids:
        post = ContentPost.objects.get(id=post_id)
        # 600s era menor que el peor caso interno de un reel: Veo puede tardar
        # hasta _VEO_POLL_TIMEOUT_SECONDS=1800s antes de rendirse a su propio
        # fallback, y ese fallback llega DESPUES de portada+contraportada de
        # HyperFrames (hasta 480s con 1 reintento cada una). RQ mataba el
        # worker antes de que ese fallback ya existente tuviera oportunidad de
        # correr -- mismo patron que el incidente de job huerfano documentado
        # en project_rq_orphaned_job_2026_07_14. 2700s cubre 1800+480 con
        # margen para TTS/musica/ffmpeg/uploads sin tocar ninguna logica de
        # generacion.
        #
        # 300s para imagen suelta murio en la prueba real del 2026-08-11 tras
        # agregar throttle real de 1/min para gemini-3.1-flash-image
        # (RPM_LIMITS, ver rate_limiter.py): ese 1/min se comparte entre los 3
        # rqworkers, asi que una imagen puede esperar varios minutos solo su
        # turno antes de intentar siquiera la primera llamada, sin contar los
        # reintentos propios de call_with_429_retry. 900s da margen para esa
        # espera + reintentos sin acercarse al caso del reel.
        timeout = 2700 if post.format == ContentPost.FORMAT_REEL else 900
        jobs.append(django_rq.enqueue(
            backfill_image_task, post_id,
            job_timeout=timeout,
            retry=Retry(max=3, interval=[10, 20, 40]),
        ))
    django_rq.enqueue(
        closing_fn, *closing_args,
        job_timeout=120,
        retry=Retry(max=2, interval=[10, 30]),
        depends_on=Dependency(jobs=jobs, allow_failure=True),
    )


def _enqueue_week_images(calendar_id: str, week_index: int) -> None:
    calendar = ContentCalendar.objects.get(id=calendar_id)
    base_day = calendar.posts.count() - 28
    week_start = base_day + (week_index * 7) + 1
    week_end = week_start + 6
    post_ids = [
        str(pid) for pid in calendar.posts.filter(
            day_number__gte=week_start, day_number__lte=week_end
        ).order_by('day_number').values_list('id', flat=True)
    ]
    _enqueue_post_images_then(post_ids, _week_closing_task, calendar_id, week_index)


def _audit_month_closing_task(calendar_id: str) -> None:
    """Cierre del backfill de auditoria -- solo loggea el resultado final, no
    reintenta de nuevo. Si sigue habiendo huecos aqui es una falla
    persistente (no transitoria) y necesita revisión manual, no otra
    ronda automática."""
    calendar = ContentCalendar.objects.get(id=calendar_id)
    still_missing = calendar.posts.filter(image_url='').count()
    if still_missing:
        logger.error(
            f"Auditor de mes: calendar {calendar_id} sigue con {still_missing} "
            f"post(s) sin imagen tras el backfill de auditoría -- revisar manualmente"
        )
    else:
        logger.info(f"Auditor de mes: calendar {calendar_id} completo, todos los posts tienen imagen")


def _audit_and_backfill_missing_images(calendar_id: str) -> None:
    """Auditor final del mes completo (HALLAZGO 2026-08-15, prueba real de pago
    simulado): ImageGenerator.generate() atrapa sus propias excepciones y
    devuelve '' en vez de propagar el error (ver image_generator.py) -- el
    reintento normal de RQ (Retry(max=3,...) en _enqueue_post_images_then)
    NUNCA se dispara para este tipo de falla silenciosa, solo para
    excepciones que sí llegan a RQ. Un 503 transitorio de Google dejó un
    post sin imagen y sin ningún mecanismo que lo detectara. Este auditor
    corre una sola vez al cerrar el mes: revisa qué posts quedaron sin
    imagen y reencola backfill_image_task para esos -- mismo mecanismo que
    ya usa el resto del pipeline (_enqueue_post_images_then), no uno nuevo."""
    calendar = ContentCalendar.objects.get(id=calendar_id)
    missing_post_ids = [
        str(pid) for pid in calendar.posts.filter(image_url='').values_list('id', flat=True)
    ]
    if not missing_post_ids:
        logger.info(f"Auditor de mes: calendar {calendar_id} sin huecos, todos los posts ya tienen imagen")
        return
    logger.warning(
        f"Auditor de mes: calendar {calendar_id} tiene {len(missing_post_ids)} "
        f"post(s) sin imagen ({missing_post_ids}), encolando backfill de auditoría"
    )
    _enqueue_post_images_then(missing_post_ids, _audit_month_closing_task, calendar_id)


def _week_closing_task(calendar_id: str, week_index: int) -> None:
    calendar = ContentCalendar.objects.get(id=calendar_id)
    brand_dna = calendar.brand_dna
    try:
        if week_index == 0:
            try:
                EmailSender().send_week_ready(job=brand_dna.job, brand_dna=brand_dna)
            except Exception as email_err:
                logger.error(f"Email de semana 1 lista falló para calendar {calendar_id} (no fatal): {email_err}")
        if week_index < 3:
            _enqueue_week_images(calendar_id, week_index + 1)
        else:
            try:
                EmailSender().send_month_ready(job=brand_dna.job, brand_dna=brand_dna)
            except Exception as email_err:
                logger.error(f"Email de mes listo falló para calendar {calendar_id} (no fatal): {email_err}")
            _audit_and_backfill_missing_images(calendar_id)
            calendar.next_week_generating = False
            calendar.save(update_fields=['next_week_generating'])
    except Exception as e:
        logger.error(f"_week_closing_task error para calendar {calendar_id}, semana {week_index}: {e}")
        calendar.next_week_generating = False
        calendar.save(update_fields=['next_week_generating'])


def _enqueue_trial_images(job_id: str, calendar_id: str, started_at: float) -> None:
    calendar = ContentCalendar.objects.get(id=calendar_id)
    post_ids = [str(pid) for pid in calendar.posts.order_by('day_number').values_list('id', flat=True)]
    _enqueue_post_images_then(post_ids, _trial_closing_task, job_id, calendar_id, started_at)


def _trial_closing_task(job_id: str, calendar_id: str, started_at: float) -> None:
    job = AnalysisJob.objects.get(id=job_id)
    calendar = ContentCalendar.objects.get(id=calendar_id)
    brand_dna = calendar.brand_dna
    try:
        try:
            EmailSender().send_initial(job=job, brand_dna=brand_dna)
            schedule_daily_emails(calendar, day_start=1, day_end=calendar.posts.count())
        except Exception as email_err:
            logger.error(f"Email inicial falló para job {job_id} (no fatal): {email_err}")
        job.stage = AnalysisJob.STAGE_COMPLETE
        job.progress = 100
        job.status = AnalysisJob.STATUS_DONE
        job.save(update_fields=['stage', 'progress', 'status'])
        CONTENT_GENERATION_DURATION.observe(time.time() - started_at)
        logger.info(f"Job {job_id} completado exitosamente")
    except Exception as e:
        CONTENT_GENERATION_DURATION.observe(time.time() - started_at)
        logger.error(f"_trial_closing_task error para job {job_id}: {e}")
        job.mark_failed(str(e))


_MAX_TRIAL_WAIT_ATTEMPTS = 30  # 30 x 60s = 30 min de margen sobre los ~10-15 min medidos


def generate_next_month(calendar_id: str, attempt: int = 0) -> None:
    calendar = ContentCalendar.objects.get(id=calendar_id)
    brand_dna = calendar.brand_dna
    job = brand_dna.job
    if job.status != AnalysisJob.STATUS_DONE:
        if job.status == AnalysisJob.STATUS_FAILED or attempt >= _MAX_TRIAL_WAIT_ATTEMPTS:
            logger.error(
                f"generate_next_month: job {job.id} no llego a status=done "
                f"(status={job.status}, intento={attempt}) — se cancela la generacion del mes"
            )
            calendar.next_week_generating = False
            calendar.save(update_fields=['next_week_generating'])
            return
        logger.info(
            f"generate_next_month: job {job.id} aun generando el trial "
            f"(status={job.status}, intento={attempt}) — reintentando en 60s"
        )
        queue = django_rq.get_queue('default')
        queue.enqueue_in(timedelta(seconds=60), generate_next_month, calendar_id, attempt + 1)
        return
    try:
        now = timezone.now()
        mexico_today = now.astimezone(MEXICO_TZ).date()
        last_post = calendar.posts.order_by('-day_number').first()
        base_day = last_post.day_number if last_post else 0
        if last_post:
            day_after_last = last_post.scheduled_at.astimezone(MEXICO_TZ).date() + timedelta(days=1)
            base_date = max(mexico_today, day_after_last)
        else:
            base_date = mexico_today

        scheduled_dates = smart_schedule_dates(brand_dna, base_date=base_date, count=28)
        text_gen = TextGenerator()

        for batch in range(4):
            posts_data = text_gen.generate(brand_dna)
            for i, post_data in enumerate(posts_data, start=1):
                day_number = base_day + (batch * 7) + i
                scheduled = scheduled_dates[batch * 7 + i - 1]
                ContentPost.objects.create(
                    calendar=calendar,
                    day_number=day_number,
                    caption=post_data['caption'],
                    image_url='',
                    image_urls=[],
                    video_url='',
                    format=post_data.get('format', ContentPost.FORMAT_SINGLE),
                    suggested_time=scheduled.astimezone(MEXICO_TZ).time(),
                    hashtags=post_data.get('hashtags', []),
                    scheduled_at=scheduled,
                )

        schedule_daily_emails(calendar, day_start=base_day + 1, day_end=base_day + 28)
        _enqueue_week_images(calendar_id, week_index=0)
    except Exception as e:
        logger.error(f"generate_next_month error para calendar {calendar_id}: {e}")
        calendar.next_week_generating = False
        calendar.save(update_fields=['next_week_generating'])



def expire_stale_trials_task() -> None:
    now = timezone.now()
    expired_trials = Subscription.objects.filter(
        status='trialing', trial_ends_at__lte=now
    ).select_related('tenant')
    expired_months = Subscription.objects.filter(
        status='active', paid_until__lte=now
    ).select_related('tenant')

    for sub, email_method in [(s, 'send_trial_expired') for s in expired_trials] + \
                              [(s, 'send_month_expired') for s in expired_months]:
        job = AnalysisJob.objects.filter(
            user__tenant=sub.tenant, generation_mode=AnalysisJob.MODE_FULL,
        ).order_by('-created_at').first()
        if job and hasattr(job, 'brand_dna'):
            try:
                getattr(EmailSender(), email_method)(job=job, brand_dna=job.brand_dna)
            except Exception as email_err:
                logger.error(f"Email de vencimiento falló para tenant {sub.tenant_id} (no fatal): {email_err}")
        else:
            logger.warning(f"No se encontró AnalysisJob completo para tenant {sub.tenant_id} — vence sin correo")
        sub.status = 'trial_expired'
        sub.save(update_fields=['status'])


_REACTIVATION_FIRST_DAYS_CALENDAR = 3
_REACTIVATION_FIRST_DAYS_ANALYSIS = 2
_REACTIVATION_REPEAT_DAYS = 15


def send_reactivation_emails_task() -> None:
    now = timezone.now()

    stale_calendars = ContentCalendar.objects.filter(
        created_at__lte=now - timedelta(days=_REACTIVATION_FIRST_DAYS_CALENDAR),
        brand_dna__job__user__tenant__subscription__plan__name='User',
    ).exclude(
        posts__downloaded_at__isnull=False
    )
    for calendar in stale_calendars:
        due = (
            calendar.last_reactivation_email_at is None
            or calendar.last_reactivation_email_at <= now - timedelta(days=_REACTIVATION_REPEAT_DAYS)
        )
        if not due:
            continue
        try:
            EmailSender().send_reactivation_calendar(calendar)
            calendar.last_reactivation_email_at = now
            calendar.save(update_fields=['last_reactivation_email_at'])
        except Exception as email_err:
            logger.error(f"Email de reactivacion (calendario) fallo para {calendar.id} (no fatal): {email_err}")

    stale_users = User.objects.filter(
        date_joined__lte=now - timedelta(days=_REACTIVATION_FIRST_DAYS_ANALYSIS),
        tenant__subscription__plan__name='User',
    ).annotate(
        jobs_count=Count('analysis_jobs')
    ).filter(jobs_count=0)
    for user in stale_users:
        due = (
            user.last_reactivation_email_at is None
            or user.last_reactivation_email_at <= now - timedelta(days=_REACTIVATION_REPEAT_DAYS)
        )
        if not due:
            continue
        try:
            EmailSender().send_reactivation_analysis(user)
            user.last_reactivation_email_at = now
            user.save(update_fields=['last_reactivation_email_at'])
        except Exception as email_err:
            logger.error(f"Email de reactivacion (analisis) fallo para {user.id} (no fatal): {email_err}")
