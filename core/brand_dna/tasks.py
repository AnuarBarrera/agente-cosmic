import logging
import os
import time
import django_rq
from django.conf import settings
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.brand_dna.extractors.web_scraper import WebScraper
from core.brand_dna.extractors.manual_extractor import ManualBrandExtractor
from core.brand_dna.extractors.logo_analyzer import LogoAnalyzer
from core.content_pipeline.image_utils import normalize_image
from core.shared.metrics import ANALYSIS_JOBS_TOTAL, ANALYSIS_DURATION
from core.shared.gcs_uploads import read_upload, upload_exists

logger = logging.getLogger(__name__)


def analyze_brand_task(job_id: str) -> None:
    job = AnalysisJob.objects.get(id=job_id)
    job.status = AnalysisJob.STATUS_PROCESSING
    job.save(update_fields=['status'])

    start = time.monotonic()
    try:
        job.update_progress(AnalysisJob.STAGE_WEB, 10)
        scraped_context, scraped_colors = '', []
        if job.business_url:
            try:
                scraped_context, scraped_colors = WebScraper().fetch_context(job.business_url)
            except Exception as e:
                logger.warning(f"No se pudo escrapear {job.business_url} para job {job_id}: {e}")
        literal_business_name = job.business_description.split('\n')[0][:100].strip()
        web_data = ManualBrandExtractor().extract(
            business_name=literal_business_name,
            description=job.business_description,
            scraped_context=scraped_context,
            scraped_colors=scraped_colors,
        )
        job.update_progress(AnalysisJob.STAGE_WEB, 30)

        job.update_progress(AnalysisJob.STAGE_LOGO, 35)
        logo_data = {'primary_colors': [], 'logo_elements': ''}
        if job.logo_file_path:
            if upload_exists(job.logo_file_path):
                logo_bytes = normalize_image(read_upload(job.logo_file_path))
                analyzer = LogoAnalyzer()
                logo_data = analyzer.analyze(logo_bytes, 'image/webp')
        job.update_progress(AnalysisJob.STAGE_LOGO, 55)

        job.update_progress(AnalysisJob.STAGE_POSTS, 75)

        BrandDNA.objects.create(
            job=job,
            business_name=literal_business_name or 'Mi Negocio',
            business_url=job.business_url,
            description=web_data.get('description', ''),
            keywords=web_data.get('keywords', []),
            audience=web_data.get('audience', ''),
            tone=web_data.get('tone', 'profesional'),
            primary_colors=logo_data.get('primary_colors') or web_data.get('brand_colors', []),
            logo_elements=logo_data.get('logo_elements', ''),
        )
        job.update_progress(AnalysisJob.STAGE_CONTENT, 78)

        ANALYSIS_DURATION.observe(time.monotonic() - start)
        ANALYSIS_JOBS_TOTAL.labels(status='completed').inc()

        from core.content_pipeline.tasks import content_generation_task, generate_sample_task
        if job.generation_mode == AnalysisJob.MODE_FULL:
            # content_generation_task ahora solo genera texto (rápido) y encadena
            # la generación de imagen/reel en jobs paralelos — ver
            # docs/superpowers/specs/2026-07-25-trial-week-chunking-design.md
            django_rq.enqueue(content_generation_task, str(job_id), job_timeout=300)
        else:
            # generate_sample_task sigue siendo monolítico (1 sola pieza, prospección)
            django_rq.enqueue(generate_sample_task, str(job_id), job_timeout=2400)

    except Exception as e:
        ANALYSIS_DURATION.observe(time.monotonic() - start)
        ANALYSIS_JOBS_TOTAL.labels(status='failed').inc()
        logger.error(f"analyze_brand_task error para job {job_id}: {e}")
        job.mark_failed(str(e))
