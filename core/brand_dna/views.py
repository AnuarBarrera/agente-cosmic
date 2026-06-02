import json
import logging
import os
import re
import time as _time
import django_rq
import google.genai as genai
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST
from core.brand_dna.models import AnalysisJob, BrandDNA

logger = logging.getLogger(__name__)


def landing(request):
    if not request.user.is_authenticated:
        return redirect('login')
    return render(request, 'brand_dna/landing.html')


@login_required
def analyze_submit(request):
    if request.method != 'POST':
        return redirect('landing')

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
        ext = logo_file.name.rsplit('.', 1)[-1].lower()
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
    from core.content_pipeline.models import ContentPost
    job = get_object_or_404(AnalysisJob, id=job_id, user=request.user)
    brand_dna = getattr(job, 'brand_dna', None)
    calendar = getattr(brand_dna, 'calendar', None) if brand_dna else None
    posts = list(calendar.posts.order_by('day_number')) if calendar else []
    return render(request, 'brand_dna/calendar_review.html', {
        'job': job,
        'brand_dna': brand_dna,
        'posts': posts,
    })


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
        return JsonResponse({'status': 'ok'})

    if action == 'edit':
        if not value:
            return JsonResponse({'error': 'Caption vacío'}, status=400)
        post.caption = value
        post.user_status = ContentPost.USER_STATUS_EDITED
        post.save(update_fields=['caption', 'user_status'])
        return JsonResponse({'status': 'ok', 'caption': post.caption})

    if action == 'regenerate':
        if not value:
            return JsonResponse({'error': 'Feedback vacío'}, status=400)
        new_caption = _regenerate_caption(post, value)
        post.caption = new_caption
        post.user_note = value
        post.user_status = ContentPost.USER_STATUS_CHANGE_REQUESTED

        # Regenerar imagen con el nuevo caption
        new_image_url = post.image_url
        try:
            from core.content_pipeline.generators.image_generator import ImageGenerator
            brand_dna = post.calendar.brand_dna
            job_id = str(brand_dna.job.id)
            image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
            generated = image_gen.generate(
                caption=new_caption,
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                filename=f"{job_id}-day{post.day_number}-regen-{int(_time.time())}",
                brand_name=brand_dna.business_name,
            )
            if generated:
                new_image_url = generated
                post.image_url = new_image_url
                post.save(update_fields=['caption', 'user_note', 'user_status', 'image_url'])
            else:
                post.save(update_fields=['caption', 'user_note', 'user_status'])
        except Exception as img_err:
            logger.error(f"Image regeneration error for post {post_id}: {img_err}")
            post.save(update_fields=['caption', 'user_note', 'user_status'])

        return JsonResponse({'status': 'ok', 'caption': new_caption, 'image_url': new_image_url})

    return JsonResponse({'error': 'Acción desconocida'}, status=400)


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
