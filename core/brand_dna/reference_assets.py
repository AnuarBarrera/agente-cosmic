import hashlib
import io

from PIL import Image
from django.db import IntegrityError, transaction

from core.brand_dna.models import ProductReferenceAsset


_MIME_BY_FORMAT = {
    'JPEG': 'image/jpeg', 'PNG': 'image/png', 'WEBP': 'image/webp', 'GIF': 'image/gif',
}


def inspect_image(image_bytes: bytes) -> tuple[str, int | None, int | None]:
    """Return trustworthy metadata without retaining decoded image data."""
    with Image.open(io.BytesIO(image_bytes)) as image:
        return _MIME_BY_FORMAT.get(image.format, ''), image.width, image.height


def create_reference_asset(job, storage_path: str, image_bytes: bytes, position: int):
    """Persist one binary once per job, while tolerating repeated UI events."""
    digest = hashlib.sha256(image_bytes).hexdigest()
    mime_type, width, height = inspect_image(image_bytes)
    try:
        with transaction.atomic():
            return ProductReferenceAsset.objects.create(
                job=job,
                position=position,
                storage_path=storage_path,
                sha256=digest,
                mime_type=mime_type,
                width=width,
                height=height,
            ), True
    except IntegrityError:
        # A repeated browser event may race with the original request. The
        # unique hash is the authority; never launch a second analysis.
        return ProductReferenceAsset.objects.get(job=job, sha256=digest), False


def reference_assets_for(job):
    return job.product_reference_assets.order_by('position', 'created_at')


def reference_paths_for(job) -> list[str]:
    """Compatibility reader: normalized assets first, historical JSON second."""
    paths = list(reference_assets_for(job).values_list('storage_path', flat=True))
    return paths if paths else list(job.product_reference_image_paths or [])
