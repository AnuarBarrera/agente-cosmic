import logging
import os
import django_rq
from django.conf import settings
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.brand_dna.extractors.web_scraper import WebScraper
from core.brand_dna.extractors.logo_analyzer import LogoAnalyzer
from core.brand_dna.extractors.posts_analyzer import PostsAnalyzer

logger = logging.getLogger(__name__)


def analyze_brand_task(job_id: str) -> None:
    job = AnalysisJob.objects.get(id=job_id)
    job.status = AnalysisJob.STATUS_PROCESSING
    job.save(update_fields=['status'])

    try:
        job.update_progress(AnalysisJob.STAGE_WEB, 10)
        scraper = WebScraper()
        web_data = scraper.extract(job.business_url)
        job.update_progress(AnalysisJob.STAGE_WEB, 30)

        job.update_progress(AnalysisJob.STAGE_LOGO, 35)
        logo_data = {'primary_colors': [], 'logo_elements': ''}
        if job.logo_file_path:
            logo_path = os.path.join(settings.MEDIA_ROOT, job.logo_file_path)
            if os.path.exists(logo_path):
                with open(logo_path, 'rb') as f:
                    logo_bytes = f.read()
                mime = 'image/png' if logo_path.endswith('.png') else 'image/jpeg'
                analyzer = LogoAnalyzer()
                logo_data = analyzer.analyze(logo_bytes, mime)
        job.update_progress(AnalysisJob.STAGE_LOGO, 55)

        job.update_progress(AnalysisJob.STAGE_POSTS, 58)
        posts_images = []
        for img_path in (job.post_images_paths or []):
            full_path = os.path.join(settings.MEDIA_ROOT, img_path)
            if os.path.exists(full_path):
                with open(full_path, 'rb') as f:
                    posts_images.append(f.read())
        posts_analyzer = PostsAnalyzer()
        posts_data = posts_analyzer.analyze(
            images=posts_images if posts_images else None,
            text=job.posts_text if job.posts_text else None,
            profile_url=job.profile_url if job.profile_url else None,
        )
        job.update_progress(AnalysisJob.STAGE_POSTS, 75)

        BrandDNA.objects.create(
            job=job,
            business_name=web_data.get('business_name', 'Mi Negocio'),
            business_url=job.business_url,
            description=web_data.get('description', ''),
            keywords=web_data.get('keywords', []),
            audience=web_data.get('audience', ''),
            tone=web_data.get('tone', 'profesional'),
            primary_colors=logo_data.get('primary_colors', []),
            logo_elements=logo_data.get('logo_elements', ''),
            posting_style=posts_data.get('posting_style', ''),
            avg_caption_length=posts_data.get('avg_caption_length', 150),
            common_hashtags=posts_data.get('common_hashtags', []),
        )
        job.update_progress(AnalysisJob.STAGE_CONTENT, 78)

        from core.content_pipeline.tasks import content_generation_task
        django_rq.enqueue(content_generation_task, str(job_id))

    except Exception as e:
        logger.error(f"analyze_brand_task error para job {job_id}: {e}")
        job.mark_failed(str(e))
