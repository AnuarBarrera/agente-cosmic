import io
import json
import logging
import os
import re
import time as _time
from urllib.parse import urlparse
import django_rq
import google.genai as genai
from google.genai import types
from PIL import Image
from core.shared.metrics_utils import track_external_api, record_tokens, vertex_labels
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, FileResponse, HttpResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.shared.metrics import POST_ACTIONS
from core.shared.gcs_uploads import save_upload

logger = logging.getLogger(__name__)

_ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}
_ALLOWED_TONES = ('formal', 'casual', 'inspiracional', 'urgente', 'profesional', 'amigable')
_BRAND_DNA_EDITABLE_FIELDS = {'description', 'audience', 'tone', 'keywords', 'primary_colors'}
_BRAND_DNA_REANALYZABLE_FIELDS = {'description', 'audience', 'keywords', 'primary_colors'}
_HEX_COLOR_RE = re.compile(r'^#[0-9A-Fa-f]{3}([0-9A-Fa-f]{3})?$')


def _is_gcs_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlparse(url)
    return parsed.scheme == 'https' and parsed.netloc == 'storage.googleapis.com'


def _safe_extension(filename: str) -> str:
    if '.' in filename:
        ext = filename.rsplit('.', 1)[-1].lower()
        if ext in _ALLOWED_IMAGE_EXTENSIONS:
            return ext
    return 'jpg'


def _validate_image_bytes(data: bytes) -> bool:
    try:
        img = Image.open(io.BytesIO(data))
        img.verify()
        return True
    except Exception:
        return False


def _screenshots_context() -> dict:
    """has_app_screenshots + screenshots_version (mtime, para cache-busting en CDN/navegador —
    la URL de estas imagenes nunca cambia de nombre, asi que sin esto Cloudflare/el navegador
    pueden seguir sirviendo una copia vieja tras regenerarlas con capture_landing_screenshots)."""
    screenshots_dir = os.path.join(
        settings.BASE_DIR, 'core', 'brand_dna', 'static', 'brand_dna', 'img', 'screenshots',
    )
    dashboard_path = os.path.join(screenshots_dir, 'dashboard.webp')
    calendar_path = os.path.join(screenshots_dir, 'calendar.webp')
    has_app_screenshots = os.path.exists(dashboard_path) and os.path.exists(calendar_path)
    version = int(max(os.path.getmtime(dashboard_path), os.path.getmtime(calendar_path))) if has_app_screenshots else 0
    return {'has_app_screenshots': has_app_screenshots, 'screenshots_version': version}


def home(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return redirect(settings.MARKETING_SITE_URL)


def new_analysis(request):
    if not request.user.is_authenticated:
        return redirect('login')
    from core.brand_dna.rate_limits import get_user_plan
    context = _screenshots_context()
    context['allows_sample_generation'] = get_user_plan(request.user).allows_sample_generation
    return render(request, 'brand_dna/new_analysis.html', context)


def favicon(request):
    path = os.path.join(settings.BASE_DIR, 'core', 'brand_dna', 'static', 'brand_dna', 'img', 'logo.svg')
    return FileResponse(open(path, 'rb'), content_type='image/svg+xml')


def privacy_policy(request):
    return render(request, 'brand_dna/legal/privacy.html')


def terms_of_service(request):
    return render(request, 'brand_dna/legal/terms.html')


@login_required
def analyze_submit(request):
    if request.method != 'POST':
        return redirect('new_analysis')

    from core.brand_dna.rate_limits import can_create_calendar, get_user_plan
    allowed, remaining = can_create_calendar(request.user)
    if not allowed:
        plan = get_user_plan(request.user)
        return render(request, 'brand_dna/new_analysis.html', {
            'error': f'Límite alcanzado: ya generaste el máximo de {plan.max_calendars_per_week} calendarios de tu plan. Contacta soporte para ampliar tu acceso.',
        })

    email = request.user.email
    business_url = request.POST.get('business_url', '').strip()
    business_description = request.POST.get('business_description', '').strip()
    business_name = request.POST.get('business_name', '').strip()

    if not business_name or not business_description:
        return render(request, 'brand_dna/new_analysis.html', {
            'error': 'Ingresa el nombre y la descripción de tu negocio.',
        })

    from core.brand_dna.moderation import check_business_legitimacy
    is_legit, _reason = check_business_legitimacy(business_name, business_description)
    if not is_legit:
        return render(request, 'brand_dna/new_analysis.html', {
            'error': 'No pudimos procesar esta descripción. Revisa que describa un negocio real y vuelve a intentar.',
        })

    business_description = f"{business_name}\n{business_description}"

    # Reenvio accidental del mismo formulario (recarga de pagina, segunda
    # pestana) mientras el analisis anterior sigue en curso — el boton ya se
    # deshabilita en el primer clic (new_analysis.html), pero eso no protege
    # contra una carga de pagina nueva. Redirige al job existente en vez de
    # duplicar el consumo de API.
    duplicate_job = AnalysisJob.objects.filter(
        user=request.user,
        business_description=business_description,
        status__in=[AnalysisJob.STATUS_PENDING, AnalysisJob.STATUS_PROCESSING],
    ).first()
    if duplicate_job:
        return redirect('results', job_id=duplicate_job.id)

    # get_user_plan ya esta importado arriba en esta funcion (linea ~98,
    # junto a can_create_calendar) — no hace falta reimportarlo.
    requested_mode = request.POST.get('generation_mode', AnalysisJob.MODE_FULL)
    valid_modes = {AnalysisJob.MODE_FULL, AnalysisJob.MODE_SAMPLE_IMAGE, AnalysisJob.MODE_SAMPLE_REEL}
    if requested_mode not in valid_modes or not get_user_plan(request.user).allows_sample_generation:
        requested_mode = AnalysisJob.MODE_FULL

    job = AnalysisJob.objects.create(
        email=email,
        business_url=business_url,
        business_description=business_description,
        user=request.user,
        generation_mode=requested_mode,
    )

    if 'logo' in request.FILES:
        logo_file = request.FILES['logo']
        logo_bytes = logo_file.read()
        if not _validate_image_bytes(logo_bytes):
            return render(request, 'brand_dna/new_analysis.html', {'error': 'El logo no es una imagen válida.'})
        ext = _safe_extension(logo_file.name)
        logo_path = f'uploads/logo_{job.id}.{ext}'
        save_upload(logo_bytes, logo_path)
        job.logo_file_path = logo_path
        job.save(update_fields=['logo_file_path'])

    from core.brand_dna.tasks import analyze_brand_task
    django_rq.enqueue(analyze_brand_task, str(job.id))

    return redirect('dashboard')


@login_required
def results(request, job_id):
    job = get_object_or_404(AnalysisJob, id=job_id, user=request.user)
    brand_dna = getattr(job, 'brand_dna', None)
    calendar = None
    if brand_dna:
        calendar = getattr(brand_dna, 'calendar', None)
    from core.brand_dna.rate_limits import can_create_calendar
    can_create = can_create_calendar(request.user)[0]
    return render(request, 'brand_dna/results.html', {
        'job': job,
        'brand_dna': brand_dna,
        'calendar': calendar,
        'can_create_calendar': can_create,
        'tone_choices': _ALLOWED_TONES,
    })


@login_required
def status_api(request, job_id):
    job = get_object_or_404(AnalysisJob, id=job_id, user=request.user)
    brand_dna = getattr(job, 'brand_dna', None)
    brand_dna_data = None
    calendar_data = None

    if brand_dna:
        brand_dna_data = {
            'business_name': brand_dna.business_name,
            'description': brand_dna.description,
            'audience': brand_dna.audience,
            'tone': brand_dna.tone,
            'keywords': brand_dna.keywords,
            'primary_colors': brand_dna.primary_colors,
            'logo_elements': brand_dna.logo_elements,
            'posting_style': brand_dna.posting_style,
        }
        calendar = getattr(brand_dna, 'calendar', None)
        if calendar:
            calendar_data = [
                {
                    'day_number': p.day_number,
                    'caption': p.caption,
                    'image_url': p.image_url,
                    'suggested_time': str(p.suggested_time),
                    'hashtags': p.hashtags,
                }
                for p in calendar.posts.all()
            ]

    return JsonResponse({
        'status': job.status,
        'stage': job.stage,
        'progress': job.progress,
        'error': job.error_message,
        'brand_dna': brand_dna_data,
        'calendar': calendar_data,
    })


@login_required
def calendar_review_view(request, job_id):
    from core.brand_dna.rate_limits import get_user_plan
    from core.content_pipeline.models import WeeklyFeedback
    job = get_object_or_404(AnalysisJob, id=job_id, user=request.user)
    brand_dna = getattr(job, 'brand_dna', None)
    calendar = getattr(brand_dna, 'calendar', None) if brand_dna else None
    posts = list(calendar.posts.order_by('day_number')) if calendar else []
    plan = get_user_plan(request.user)
    total_regens = sum(p.regen_count for p in posts)
    total_edits = sum(p.edit_count for p in posts)

    pending_feedback = None
    if calendar:
        pending_feedback = calendar.feedback_entries.filter(
            continue_decision=WeeklyFeedback.CONTINUE_PENDING
        ).order_by('-week_number').first()

    from core.brand_dna.rate_limits import can_create_calendar
    can_create, _ = can_create_calendar(request.user)

    week_groups = []
    if posts:
        posts_by_week = {}
        for p in posts:
            week_num = ((p.day_number - 1) // 7) + 1
            posts_by_week.setdefault(week_num, []).append(p)
        current_week = max(posts_by_week)
        for week_num in sorted(posts_by_week, reverse=True):
            week_posts = posts_by_week[week_num]
            week_groups.append({
                'week_number': week_num,
                'posts': week_posts,
                'is_current': week_num == current_week,
                'start_iso': min(p.scheduled_at for p in week_posts).isoformat(),
                'end_iso': max(p.scheduled_at for p in week_posts).isoformat(),
            })

    return render(request, 'brand_dna/calendar_review.html', {
        'job': job,
        'brand_dna': brand_dna,
        'calendar': calendar,
        'posts': posts,
        'week_groups': week_groups,
        'max_regenerations': plan.max_post_regenerations,
        'max_edits': plan.max_post_edits,
        'total_regens': total_regens,
        'total_edits': total_edits,
        'can_create_calendar': can_create,
        'pending_feedback': pending_feedback,
    })


@login_required
@require_POST
def delete_calendar_api(request, job_id):
    from django.utils import timezone
    import django_rq
    job = get_object_or_404(AnalysisJob, id=job_id, user=request.user)
    job.deleted_at = timezone.now()
    job.save(update_fields=['deleted_at'])

    # Cancelar todos los jobs RQ programados para los posts de este calendario
    try:
        calendar = job.brand_dna.calendar
        post_ids = set(str(pid) for pid in calendar.posts.values_list('id', flat=True))
        queue = django_rq.get_queue('default')
        registry = queue.scheduled_job_registry
        cancelled = 0
        for rq_job_id in registry.get_job_ids():
            rq_job = queue.fetch_job(rq_job_id)
            if rq_job and rq_job.args and str(rq_job.args[0]) in post_ids:
                registry.remove(rq_job)
                rq_job.cancel()
                cancelled += 1
        if cancelled:
            logger.info(f"delete_calendar: {cancelled} RQ jobs cancelados para job {job_id}")
    except Exception as e:
        logger.warning(f"delete_calendar: error cancelando RQ jobs: {e}")

    return JsonResponse({'status': 'ok'})


@login_required
def download_post_image(request, post_id):
    """Sirve la imagen del post como descarga forzada (Content-Disposition: attachment).
    Evita depender de fetch()+blob en el navegador — el bucket de GCS no manda headers
    CORS, así que un fetch cross-origin directo desde el JS del cliente falla en
    silencio. Proxeamos la imagen desde el backend (mismo origen, sin CORS).
    Posts carrusel (H20 + roadmap #5) se sirven como un único .zip con las N
    slides — descargar 4 archivos sueltos por click es mala UX y los navegadores
    suelen bloquear descargas múltiples automáticas como si fueran popups."""
    import urllib.request
    from core.content_pipeline.models import ContentPost
    post = get_object_or_404(
        ContentPost.objects.select_related('calendar__brand_dna__job'),
        id=post_id,
        calendar__brand_dna__job__user=request.user,
    )
    if not post.image_url:
        raise Http404

    if post.format == ContentPost.FORMAT_REEL and post.video_url:
        if not _is_gcs_url(post.video_url):
            raise Http404
        try:
            with urllib.request.urlopen(post.video_url, timeout=30) as resp:
                data = resp.read()
        except Exception as e:
            logger.warning(f"download_post_image: no se pudo obtener el reel de {post.video_url}: {e}")
            raise Http404
        response = HttpResponse(data, content_type='video/mp4')
        response['Content-Disposition'] = f'attachment; filename="post-dia-{post.day_number}-reel.mp4"'
        return response

    if post.format == ContentPost.FORMAT_CAROUSEL and post.image_urls:
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for i, slide_url in enumerate(post.image_urls, start=1):
                if not _is_gcs_url(slide_url):
                    continue
                try:
                    with urllib.request.urlopen(slide_url, timeout=15) as resp:
                        zf.writestr(f'slide-{i}.png', resp.read())
                except Exception as e:
                    logger.warning(f"download_post_image: no se pudo obtener la slide {i} de {slide_url}: {e}")
        if not buf.getbuffer().nbytes:
            raise Http404
        response = HttpResponse(buf.getvalue(), content_type='application/zip')
        response['Content-Disposition'] = f'attachment; filename="post-dia-{post.day_number}-carrusel.zip"'
        return response

    if not _is_gcs_url(post.image_url):
        raise Http404
    try:
        with urllib.request.urlopen(post.image_url, timeout=15) as resp:
            data = resp.read()
    except Exception as e:
        logger.warning(f"download_post_image: no se pudo obtener la imagen de {post.image_url}: {e}")
        raise Http404
    response = HttpResponse(data, content_type='image/png')
    response['Content-Disposition'] = f'attachment; filename="post-dia-{post.day_number}.png"'
    return response


@login_required
@require_POST
def post_action_api(request, post_id):
    from core.content_pipeline.models import ContentPost
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    action = data.get('action')
    value = data.get('value', '').strip()

    post = get_object_or_404(
        ContentPost.objects.select_related('calendar__brand_dna__job'),
        id=post_id,
        calendar__brand_dna__job__user=request.user,
    )

    if action == 'approve':
        post.user_status = ContentPost.USER_STATUS_APPROVED
        post.save(update_fields=['user_status'])
        POST_ACTIONS.labels(action='approved').inc()
        logger.info(
            f"POST APROBADO | user={request.user.email} | "
            f"job={post.calendar.brand_dna.job_id} | día={post.day_number} | "
            f"post={post_id}"
        )
        return JsonResponse({'status': 'ok'})

    if action == 'mark_published':
        from django.utils import timezone
        if not post.published_at:
            post.published_at = timezone.now()
            post.save(update_fields=['published_at'])
            POST_ACTIONS.labels(action='published').inc()
            delta = post.published_at - post.scheduled_at
            logger.info(
                f"POST PUBLICADO | user={request.user.email} | "
                f"job={post.calendar.brand_dna.job_id} | día={post.day_number} | "
                f"post={post_id} | delta_desde_programado={delta}"
            )
        return JsonResponse({'status': 'ok', 'published_at': post.published_at.isoformat()})

    if action == 'mark_downloaded':
        from django.utils import timezone
        if not post.downloaded_at:
            post.downloaded_at = timezone.now()
            post.save(update_fields=['downloaded_at'])
            POST_ACTIONS.labels(action='downloaded').inc()
        return JsonResponse({'status': 'ok', 'downloaded_at': post.downloaded_at.isoformat()})

    if action == 'edit':
        if not value:
            return JsonResponse({'error': 'Caption vacío'}, status=400)
        from core.brand_dna.rate_limits import can_edit
        allowed, remaining = can_edit(post, request.user)
        if not allowed:
            return JsonResponse({
                'error': 'Límite de ediciones alcanzado para este post (máximo 2).',
                'limit_reached': True,
            }, status=429)
        post.caption = value
        post.user_status = ContentPost.USER_STATUS_EDITED
        post.edit_count += 1
        post.save(update_fields=['caption', 'user_status', 'edit_count'])
        POST_ACTIONS.labels(action='edited').inc()
        return JsonResponse({'status': 'ok', 'caption': post.caption, 'remaining_edits': remaining - 1})

    if action == 'regenerate':
        if post.format == ContentPost.FORMAT_REEL:
            return JsonResponse({'error': 'La regeneración no está disponible para reels todavía.'}, status=400)
        if not value:
            return JsonResponse({'error': 'Feedback vacío'}, status=400)
        from core.brand_dna.rate_limits import can_regenerate
        allowed, remaining = can_regenerate(post, request.user)
        if not allowed:
            return JsonResponse({
                'error': 'Límite de regeneraciones alcanzado para este post (máximo 2).',
                'limit_reached': True,
            }, status=429)
        new_caption = _regenerate_caption(post, value)
        post.caption = new_caption
        post.user_note = value
        post.user_status = ContentPost.USER_STATUS_CHANGE_REQUESTED
        post.regen_count += 1

        # Regenerar imagen (o slides del carrusel, H20 + roadmap #5) con el nuevo caption
        new_image_url = post.image_url
        try:
            from core.content_pipeline.generators.image_generator import ImageGenerator
            from core.content_pipeline.tasks import _generate_post_media
            brand_dna = post.calendar.brand_dna
            job_id = str(brand_dna.job.id)
            image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
            generated_url, generated_urls, _ = _generate_post_media(
                image_gen,
                None,  # reel_script_gen
                None,  # reel_gen
                fmt=post.format,
                filename=f"{job_id}-day{post.day_number}-regen-{int(_time.time())}",
                caption=new_caption,
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                audience=brand_dna.audience,
                max_qc_retries=0,  # regen es síncrono — sin reintentos QC para evitar timeout
            )
            if generated_url:
                new_image_url = generated_url
                post.image_url = new_image_url
                post.image_urls = generated_urls
                post.save(update_fields=['caption', 'user_note', 'user_status', 'image_url', 'image_urls', 'regen_count'])
            else:
                post.save(update_fields=['caption', 'user_note', 'user_status', 'regen_count'])
        except Exception as img_err:
            logger.error(f"Image regeneration error for post {post_id}: {img_err}")
            post.save(update_fields=['caption', 'user_note', 'user_status', 'regen_count'])
        POST_ACTIONS.labels(action='regenerated').inc()

        return JsonResponse({
            'status': 'ok',
            'caption': new_caption,
            'image_url': new_image_url,
            'image_urls': post.image_urls,
            'remaining_regens': remaining - 1,
        })

    return JsonResponse({'error': 'Acción desconocida'}, status=400)


@login_required
@require_POST
def calendar_feedback_api(request, job_id):
    from django.utils import timezone
    from core.content_pipeline.models import WeeklyFeedback
    from core.content_pipeline.tasks import generate_next_week

    job = get_object_or_404(AnalysisJob, id=job_id, user=request.user)
    calendar = job.brand_dna.calendar
    feedback = get_object_or_404(
        WeeklyFeedback, calendar=calendar, continue_decision=WeeklyFeedback.CONTINUE_PENDING
    )

    rating_raw = request.POST.get('rating', '').strip()
    rating = None
    if rating_raw:
        try:
            rating = int(rating_raw)
        except ValueError:
            return JsonResponse({'error': 'Rating inválido'}, status=400)
        if not (1 <= rating <= 5):
            return JsonResponse({'error': 'Rating inválido'}, status=400)

    continue_decision = request.POST.get('continue_decision')
    if continue_decision not in (WeeklyFeedback.CONTINUE_YES, WeeklyFeedback.CONTINUE_NO):
        return JsonResponse({'error': 'Decisión inválida'}, status=400)

    feedback.rating = rating
    feedback.comment = request.POST.get('comment', '')
    feedback.continue_decision = continue_decision
    feedback.responded_at = timezone.now()
    feedback.save(update_fields=['rating', 'comment', 'continue_decision', 'responded_at'])

    if feedback.continue_decision == WeeklyFeedback.CONTINUE_YES:
        next_week = feedback.week_number + 1
        calendar.next_week_generating = True
        calendar.save(update_fields=['next_week_generating'])
        django_rq.enqueue(generate_next_week, str(calendar.id), next_week, job_timeout=2400)

    return JsonResponse({'status': 'ok', 'continue_decision': feedback.continue_decision})


def _regenerate_caption(post, feedback: str) -> str:
    brand_dna = post.calendar.brand_dna
    prompt = (
        f"Eres un experto en marketing de contenidos. Reescribe el siguiente post de redes sociales "
        f"para la marca '{brand_dna.business_name}' considerando el feedback del cliente.\n\n"
        f"Post original:\n{post.caption}\n\n"
        f"Feedback del cliente: {feedback}\n\n"
        f"Tono de la marca: {brand_dna.tone}\n"
        f"Audiencia: {brand_dna.audience}\n\n"
        f"Responde ÚNICAMENTE con el nuevo texto del post, sin comillas, sin explicaciones. "
        f"Máximo {brand_dna.avg_caption_length} caracteres."
    )
    try:
        client = genai.Client(
            vertexai=True,
            project=settings.GOOGLE_CLOUD_PROJECT,
            location=settings.GOOGLE_CLOUD_LOCATION,
        )
        with track_external_api('gemini', operation='caption_regen'):
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL, contents=prompt,
                config=types.GenerateContentConfig(labels=vertex_labels()),
            )
        record_tokens(resp, operation='caption_regen', response_preview=resp.text[:200] if resp.text else '')
        new_caption = resp.text.strip().strip('"').strip("'")
        raw = re.sub(r'^```.*?\n', '', new_caption, flags=re.DOTALL)
        raw = re.sub(r'\n?```$', '', raw)
        return raw.strip() or post.caption
    except Exception as e:
        logger.error(f"Caption regeneration error: {e}")
        return post.caption


@login_required
@require_POST
def brand_dna_field_action_api(request, job_id):
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    field = data.get('field')
    action = data.get('action')
    raw_value = data.get('value', '')
    value = raw_value.strip() if isinstance(raw_value, str) else raw_value

    if field not in _BRAND_DNA_EDITABLE_FIELDS:
        return JsonResponse({'error': 'Campo no editable'}, status=400)

    job = get_object_or_404(AnalysisJob, id=job_id, user=request.user)
    brand_dna = getattr(job, 'brand_dna', None)
    if not brand_dna:
        return JsonResponse({'error': 'Sin análisis de marca todavía'}, status=404)

    if action == 'edit':
        if field == 'tone':
            if value not in _ALLOWED_TONES:
                return JsonResponse({'error': 'Tono inválido'}, status=400)
            brand_dna.tone = value
        elif field == 'keywords':
            keywords = [k.strip() for k in (value or '').split(',') if k.strip()]
            if not keywords:
                return JsonResponse({'error': 'Agrega al menos una keyword'}, status=400)
            brand_dna.keywords = keywords[:8]
        elif field == 'primary_colors':
            colors = [c.strip() for c in (value or '').split(',') if c.strip()]
            if not colors or not all(_HEX_COLOR_RE.match(c) for c in colors):
                return JsonResponse({'error': 'Usa colores en formato hex, ej: #E94560'}, status=400)
            brand_dna.primary_colors = colors[:5]
        else:  # description, audience
            if not value:
                return JsonResponse({'error': 'El campo no puede quedar vacío'}, status=400)
            setattr(brand_dna, field, value)
        brand_dna.save(update_fields=[field])
        POST_ACTIONS.labels(action='brand_dna_edited').inc()
        return JsonResponse({'status': 'ok', 'field': field, 'value': getattr(brand_dna, field)})

    if action == 'reanalyze':
        if field not in _BRAND_DNA_REANALYZABLE_FIELDS:
            return JsonResponse({'error': 'Este campo no se puede reanalizar, edítalo directamente.'}, status=400)
        try:
            new_value = _reanalyze_brand_field(brand_dna, job, field, value or '')
        except ValueError as e:
            return JsonResponse({'error': str(e)}, status=400)
        setattr(brand_dna, field, new_value)
        brand_dna.save(update_fields=[field])
        POST_ACTIONS.labels(action='brand_dna_reanalyzed').inc()
        return JsonResponse({'status': 'ok', 'field': field, 'value': new_value})

    return JsonResponse({'error': 'Acción desconocida'}, status=400)


def _reanalyze_brand_field(brand_dna, job, field: str, feedback: str):
    if field == 'primary_colors':
        if not job.business_url:
            raise ValueError('Sin sitio web no se puede reanalizar el color — edítalo directamente.')
        from core.brand_dna.extractors.web_scraper import WebScraper
        try:
            _, colors = WebScraper().fetch_context(job.business_url)
        except Exception as e:
            raise ValueError(f'No se pudo re-escanear el sitio web: {e}')
        if not colors:
            raise ValueError('No se detectaron colores en el sitio web.')
        return colors[:5]

    field_labels = {
        'description': 'descripción del negocio',
        'audience': 'audiencia objetivo',
        'keywords': 'palabras clave',
    }
    current_value = brand_dna.keywords if field == 'keywords' else getattr(brand_dna, field)
    prompt = (
        f"Eres un experto en branding. El usuario quiere corregir el campo "
        f"'{field_labels[field]}' del análisis de marca de '{brand_dna.business_name}'.\n\n"
        f"Valor actual: {current_value}\n"
        f"Qué no refleja su marca (feedback del usuario): {feedback or 'sin detalle, genera una alternativa distinta'}\n\n"
        f"Contexto adicional — tono: {brand_dna.tone}, descripción: {brand_dna.description}\n\n"
    )
    if field == 'keywords':
        prompt += 'Responde ÚNICAMENTE con un array JSON de 5 palabras clave, sin markdown. Ej: ["a","b","c","d","e"]'
    else:
        prompt += f"Responde ÚNICAMENTE con el nuevo texto para '{field_labels[field]}', sin comillas ni explicaciones."

    client = genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )
    with track_external_api('gemini', operation='brand_dna_reanalyze'):
        resp = client.models.generate_content(
            model=settings.VERTEX_TEXT_MODEL, contents=prompt,
            config=types.GenerateContentConfig(labels=vertex_labels()),
        )
    record_tokens(resp, operation='brand_dna_reanalyze', response_preview=resp.text[:200] if resp.text else '')
    raw = resp.text.strip()
    raw = re.sub(r'^```(?:json)?\n?', '', raw)
    raw = re.sub(r'\n?```$', '', raw)
    raw = raw.strip().strip('"').strip("'")
    if field == 'keywords':
        return json.loads(raw)
    return raw


@login_required
@require_POST
def regenerate_calendar_api(request, job_id):
    from core.content_pipeline.generators.text_generator import TextGenerator
    from core.content_pipeline.generators.image_generator import ImageGenerator
    from core.content_pipeline.models import ContentPost

    job = get_object_or_404(AnalysisJob, id=job_id, user=request.user)
    brand_dna = getattr(job, 'brand_dna', None)
    calendar = getattr(brand_dna, 'calendar', None) if brand_dna else None
    if not calendar:
        return JsonResponse({'error': 'No hay calendario para regenerar'}, status=404)

    pending_posts = list(calendar.posts.exclude(status=ContentPost.STATUS_SENT).order_by('day_number'))
    if not pending_posts:
        return JsonResponse({'error': 'No hay posts pendientes por regenerar — todos ya fueron enviados.'}, status=400)

    try:
        posts_data = TextGenerator().generate(brand_dna)
    except Exception as e:
        logger.error(f"Error regenerando texto para job {job_id}: {e}")
        return JsonResponse({'error': 'No se pudo regenerar el contenido. Intenta de nuevo.'}, status=500)

    posts_by_day = {p.day_number: p for p in pending_posts}
    regenerated_days = []
    for i, post_data in enumerate(posts_data, start=1):
        post = posts_by_day.get(i)
        if not post:
            continue
        post.caption = post_data['caption']
        post.hashtags = post_data.get('hashtags', [])
        post.user_status = ContentPost.USER_STATUS_PENDING
        post.save(update_fields=['caption', 'hashtags', 'user_status'])
        regenerated_days.append(i)

    day1 = posts_by_day.get(1)
    if day1 and day1.image_url:
        try:
            image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
            new_image_url = image_gen.generate(
                caption=day1.caption,
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                filename=f"{job_id}-day1-regen-{int(_time.time())}",
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                audience=brand_dna.audience,
                max_qc_retries=0,
            )
            if new_image_url:
                day1.image_url = new_image_url
                day1.save(update_fields=['image_url'])
        except Exception as e:
            logger.error(f"Error regenerando imagen dia 1 para job {job_id}: {e}")

    POST_ACTIONS.labels(action='brand_dna_regenerated_calendar').inc()
    logger.info(f"Calendario regenerado tras cambios en Brand DNA | job={job_id} | user={request.user.email}")
    return JsonResponse({'status': 'ok', 'regenerated_days': regenerated_days})
