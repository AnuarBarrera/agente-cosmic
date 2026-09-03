import io

import pytest
from PIL import Image

from core.brand_dna.models import AnalysisJob, ProductReferenceAsset
from core.brand_dna.reference_assets import create_reference_asset, reference_paths_for

pytestmark = pytest.mark.django_db


def image_bytes(color='red'):
    output = io.BytesIO()
    Image.new('RGB', (17, 11), color=color).save(output, format='PNG')
    return output.getvalue()


def test_same_binary_creates_exactly_one_asset_per_job():
    job = AnalysisJob.objects.create(email='owner@example.com')
    first, created = create_reference_asset(job, 'uploads/first.png', image_bytes(), 0)
    duplicate, duplicate_created = create_reference_asset(job, 'uploads/repeated.png', image_bytes(), 1)

    assert created is True
    assert duplicate_created is False
    assert duplicate.pk == first.pk
    assert ProductReferenceAsset.objects.filter(job=job).count() == 1
    assert (first.mime_type, first.width, first.height) == ('image/png', 17, 11)


def test_normalized_reader_precedes_legacy_json():
    job = AnalysisJob.objects.create(
        email='owner@example.com', product_reference_image_paths=['uploads/legacy.jpg'],
    )
    create_reference_asset(job, 'uploads/normalized.png', image_bytes(), 0)
    assert reference_paths_for(job) == ['uploads/normalized.png']


def test_reader_falls_back_for_historical_job_without_assets():
    job = AnalysisJob.objects.create(
        email='owner@example.com', product_reference_image_paths=['uploads/legacy.jpg'],
    )
    assert reference_paths_for(job) == ['uploads/legacy.jpg']
