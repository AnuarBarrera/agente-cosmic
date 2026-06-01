import os
import django_rq
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from core.brand_dna.models import AnalysisJob, BrandDNA


def landing(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
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
