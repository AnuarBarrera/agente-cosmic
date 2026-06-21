import json
import logging
import os
import re
import time as _time
import django_rq
import google.genai as genai
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, FileResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from core.brand_dna.models import AnalysisJob, BrandDNA

logger = logging.getLogger(__name__)

_ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}


def _safe_extension(filename: str) -> str:
    if '.' in filename:
        ext = filename.rsplit('.', 1)[-1].lower()
        if ext in _ALLOWED_IMAGE_EXTENSIONS:
            return ext
    return 'jpg'


def landing(request):
    if not request.user.is_authenticated:
        return redirect('login')
    return render(request, 'brand_dna/landing.html')


def favicon(request):
    path = os.path.join(settings.BASE_DIR, 'core', 'brand_dna', 'static', 'brand_dna', 'img', 'logo.svg')
    return FileResponse(open(path, 'rb'), content_type='image/svg+xml')


@login_required
def analyze_submit(request):
    if request.method != 'POST':
        return redirect('landing')

    from core.brand_dna.rate_limits import can_create_calendar, get_user_plan
    allowed, remaining = can_create_calendar(request.user)
    if not allowed:
        plan = get_user_plan(request.user)
        return render(request, 'brand_dna/landing.html', {
            'error': f'Límite alcanzado: máximo {plan.max_calendars_per_week} calendarios por semana. Vuelve en 7 días o contacta soporte para ampliar tu plan.',
        })

    email = request.user.email
    business_url = request.POST.get('business_url', '').strip()
    posts_text = request.POST.get('posts_text', '').strip()
    profile_url = request.POST.get('profile_url', '').strip()

    job = AnalysisJob.objects.create(
        email=email,
        business_url=business_url,
        posts_text=posts_text,
        profile_url=profile_url,
        user=request.user,
    )

    if 'logo' in request.FILES:
        logo_file = request.FILES['logo']
        ext = _safe_extension(logo_file.name)
        logo_path = f'uploads/logo_{job.id}.{ext}'
        full_path = os.path.join(settings.MEDIA_ROOT, logo_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'wb') as f:
            for chunk in logo_file.chunks():
                f.write(chunk)
        job.logo_file_path = logo_path
        job.save(update_fields=['logo_file_path'])

    post_paths = []
    for i, img_file in enumerate(request.FILES.getlist('post_images')):
        img_path = f'uploads/post_{job.id}_{i}.jpg'
        full_path = os.path.join(settings.MEDIA_ROOT, img_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'wb') as f:
            for chunk in img_file.chunks():
                f.write(chunk)
        post_paths.append(img_path)

    if post_paths:
        job.post_images_paths = post_paths
        job.save(update_fields=['post_images_paths'])

    prod_files = request.FILES.getlist('product_images')[:7]
    if prod_files:
        prod_paths = []
        for idx, prod_file in enumerate(prod_files):
            ext = _safe_extension(prod_file.name)
            prod_path = f'uploads/product_{job.id}_{idx}.{ext}'
            full_path = os.path.join(settings.MEDIA_ROOT, prod_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, 'wb') as f:
                for chunk in prod_file.chunks():
                    f.write(chunk)
            prod_paths.append(prod_path)
        job.product_image_paths = prod_paths
        job.product_image_path = prod_paths[0]
        job.save(update_fields=['product_image_path', 'product_image_paths'])

    from core.brand_dna.tasks import analyze_brand_task
    django_rq.enqueue(analyze_brand_task, str(job.id))

    return redirect('results', job_id=str(job.id))


def results(request, job_id):
    job = get_object_or_404(AnalysisJob, id=job_id)
    brand_dna = getattr(job, 'brand_dna', None)
    calendar = None
    if brand_dna:
        calendar = getattr(brand_dna, 'calendar', None)
    return render(request, 'brand_dna/results.html', {
        'job': job,
        'brand_dna': brand_dna,
        'calendar': calendar,
    })


def status_api(request, job_id):
    job = get_object_or_404(AnalysisJob, id=job_id)
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

    return render(request, 'brand_dna/calendar_review.html', {
        'job': job,
        'brand_dna': brand_dna,
        'posts': posts,
        'max_regenerations': plan.max_post_regenerations,
        'max_edits': plan.max_post_edits,
        'total_regens': total_regens,
        'total_edits': total_edits,
        'pending_feedback': pending_feedback,
        'product_pool': job.product_image_paths,
    })


@login_required
@require_POST
def delete_calendar_api(request, job_id):
    from django.utils import timezone
    job = get_object_or_404(AnalysisJob, id=job_id, user=request.user)
    job.deleted_at = timezone.now()
    job.save(update_fields=['deleted_at'])
    return JsonResponse({'status': 'ok'})


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
        logger.info(
            f"POST APROBADO | user={request.user.email} | "
            f"job={post.calendar.brand_dna.job_id} | día={post.day_number} | "
            f"post={post_id}"
        )
        return JsonResponse({'status': 'ok'})

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
        return JsonResponse({'status': 'ok', 'caption': post.caption, 'remaining_edits': remaining - 1})

    if action == 'regenerate':
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

        # Regenerar imagen con el nuevo caption
        new_image_url = post.image_url
        try:
            from core.content_pipeline.generators.image_generator import ImageGenerator
            brand_dna = post.calendar.brand_dna
            job_id = str(brand_dna.job.id)
            image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
            product_image_bytes = None
            if brand_dna.job.product_image_path:
                prod_full = os.path.join(settings.MEDIA_ROOT, brand_dna.job.product_image_path)
                if os.path.exists(prod_full):
                    with open(prod_full, 'rb') as _f:
                        product_image_bytes = _f.read()
            generated = image_gen.generate(
                caption=new_caption,
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                filename=f"{job_id}-day{post.day_number}-regen-{int(_time.time())}",
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                product_image_bytes=product_image_bytes,
                max_qc_retries=0,  # regen es síncrono — sin reintentos QC para evitar timeout
            )
            if generated:
                new_image_url = generated
                post.image_url = new_image_url
                post.save(update_fields=['caption', 'user_note', 'user_status', 'image_url', 'regen_count'])
            else:
                post.save(update_fields=['caption', 'user_note', 'user_status', 'regen_count'])
        except Exception as img_err:
            logger.error(f"Image regeneration error for post {post_id}: {img_err}")
            post.save(update_fields=['caption', 'user_note', 'user_status', 'regen_count'])

        return JsonResponse({
            'status': 'ok',
            'caption': new_caption,
            'image_url': new_image_url,
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

    try:
        rating = int(request.POST.get('rating'))
    except (TypeError, ValueError):
        rating = None
    if rating is None or not (1 <= rating <= 5):
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
        _update_active_product_images(calendar, job, request, next_week)
        generate_next_week(calendar, next_week)

    return JsonResponse({'status': 'ok', 'continue_decision': feedback.continue_decision})


def _update_active_product_images(calendar, job, request, next_week):
    choice = request.POST.get('image_choice', 'reuse')
    if choice == 'new':
        files = request.FILES.getlist('product_images')[:7]
        new_paths = []
        for idx, f in enumerate(files):
            ext = f.name.rsplit('.', 1)[-1].lower() if '.' in f.name else 'jpg'
            path = f'uploads/product_{job.id}_w{next_week}_{idx}.{ext}'
            full = os.path.join(settings.MEDIA_ROOT, path)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, 'wb') as out:
                for chunk in f.chunks():
                    out.write(chunk)
            new_paths.append(path)
        if new_paths:
            job.product_image_paths = job.product_image_paths + new_paths
            job.save(update_fields=['product_image_paths'])
            calendar.active_product_images = new_paths
            calendar.save(update_fields=['active_product_images'])
    elif choice == 'reuse':
        pool = job.product_image_paths
        if len(pool) > 7:
            selected = request.POST.getlist('selected_images')[:7]
            valid = [p for p in selected if p in pool]
            if valid:
                calendar.active_product_images = valid
                calendar.save(update_fields=['active_product_images'])


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
        resp = client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, contents=prompt)
        new_caption = resp.text.strip().strip('"').strip("'")
        raw = re.sub(r'^```.*?\n', '', new_caption, flags=re.DOTALL)
        raw = re.sub(r'\n?```$', '', raw)
        return raw.strip() or post.caption
    except Exception as e:
        logger.error(f"Caption regeneration error: {e}")
        return post.caption
