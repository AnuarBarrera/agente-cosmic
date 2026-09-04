import logging
import os
import time
import django_rq
from django.conf import settings
from core.brand_dna.models import AnalysisJob, BrandDNA, ProductReferenceAsset
from core.brand_dna.extractors.web_scraper import WebScraper
from core.brand_dna.extractors.manual_extractor import ManualBrandExtractor
from core.brand_dna.extractors.logo_analyzer import LogoAnalyzer
from core.brand_dna.extractors.product_photo_analyzer import ProductPhotoAnalyzer
from core.brand_dna.extractors.product_photo_triage import (
    ProductPhotoTriageAnalyzer, TRIAGE_VERSION, risk_flags_from,
)
from core.brand_dna.reference_assets import reference_assets_for, reference_paths_for
from core.brand_dna.fact_profile import build_brand_fact_profile
from core.content_pipeline.audit import GenerationContext
from core.content_pipeline.image_utils import normalize_image
from core.shared.metrics import ANALYSIS_JOBS_TOTAL, ANALYSIS_DURATION
from core.shared.gcs_uploads import read_upload, upload_exists

logger = logging.getLogger(__name__)


def triage_reference_assets(job, assets=None) -> None:
    """Analyze each pending hash once; failure degrades to preservation."""
    assets = list(assets if assets is not None else reference_assets_for(job))
    triage = ProductPhotoTriageAnalyzer()
    for asset in assets:
        if asset.triage_status != ProductReferenceAsset.TRIAGE_PENDING:
            continue
        try:
            if not upload_exists(asset.storage_path):
                raise FileNotFoundError(asset.storage_path)
            original_bytes = read_upload(asset.storage_path)
            data = triage.analyze(
                original_bytes, asset.mime_type or 'image/jpeg', job.business_description,
                context=GenerationContext(job_id=str(job.id), asset_id=str(asset.id)),
            )
            asset.analysis_description = data['description']
            asset.product_category = data['category']
            asset.commercial_relationship = data['commercial_relationship']
            asset.usage_mode = data['usage_mode']
            asset.risk_flags = {**risk_flags_from(data), 'policy_reason': data['policy_reason']}
            asset.visible_brands = data['visible_brands']
            asset.visible_text_summary = data['visible_text_summary']
            asset.triage_status = ProductReferenceAsset.TRIAGE_COMPLETE
            asset.triage_version = TRIAGE_VERSION
            asset.save()
        except Exception as exc:
            logger.warning('Triage no bloqueante fallo para asset=%s: %s', asset.id, exc)
            asset.usage_mode = ProductReferenceAsset.USAGE_PRESERVE_ONLY
            asset.triage_status = ProductReferenceAsset.TRIAGE_FAILED
            asset.triage_version = TRIAGE_VERSION
            asset.risk_flags = {
                **(asset.risk_flags or {}), 'triage_failed': True,
                'fallback': ProductReferenceAsset.USAGE_PRESERVE_ONLY,
            }
            asset.save(update_fields=[
                'usage_mode', 'triage_status', 'triage_version', 'risk_flags', 'updated_at',
            ])


def triage_reference_assets_task(job_id: str, asset_ids: list[str] | None = None) -> None:
    if not getattr(settings, 'PHOTO_ASSET_TRIAGE_ENABLED', False):
        return
    job = AnalysisJob.objects.get(id=job_id)
    queryset = reference_assets_for(job)
    if asset_ids:
        queryset = queryset.filter(id__in=asset_ids)
    triage_reference_assets(job, queryset)


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

        product_photo_data = {'description': '', 'category': ''}
        assets = list(reference_assets_for(job))
        if getattr(settings, 'PHOTO_ASSET_TRIAGE_ENABLED', False) and assets:
            triage_reference_assets(job, assets)
            first = assets[0]
            first.refresh_from_db()
            product_photo_data = {
                'description': first.analysis_description,
                'category': first.product_category,
            }
        else:
            # Compatibility path while the feature flag rolls out and for
            # jobs constructed by historical callers with JSON only.
            paths = reference_paths_for(job)
            if paths and upload_exists(paths[0]):
                product_photo_bytes = normalize_image(read_upload(paths[0]))
                product_photo_data = ProductPhotoAnalyzer().analyze(product_photo_bytes, 'image/webp')

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
            product_photo_analysis=product_photo_data.get('description', ''),
            product_category=product_photo_data.get('category', ''),
            brand_fact_profile=build_brand_fact_profile(
                job.business_description, keywords=web_data.get('keywords', []),
            ),
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
            django_rq.enqueue(generate_sample_task, str(job_id), job_timeout=2700)

    except Exception as e:
        ANALYSIS_DURATION.observe(time.monotonic() - start)
        ANALYSIS_JOBS_TOTAL.labels(status='failed').inc()
        logger.error(f"analyze_brand_task error para job {job_id}: {e}")
        job.mark_failed(str(e))
