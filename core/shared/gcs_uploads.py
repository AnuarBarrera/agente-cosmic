import logging
from django.conf import settings
from google.cloud import storage

logger = logging.getLogger(__name__)

_CONTENT_TYPES = {
    'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
    'png': 'image/png', 'webp': 'image/webp', 'gif': 'image/gif',
}


def _client():
    return storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)


def save_upload(file_bytes: bytes, gcs_path: str) -> None:
    ext = gcs_path.rsplit('.', 1)[-1].lower() if '.' in gcs_path else 'jpg'
    content_type = _CONTENT_TYPES.get(ext, 'application/octet-stream')
    bucket = _client().bucket(settings.GOOGLE_CLOUD_STORAGE_BUCKET)
    blob = bucket.blob(gcs_path)
    blob.upload_from_string(file_bytes, content_type=content_type)
    logger.debug(f"GCS upload: {gcs_path}")


def read_upload(gcs_path: str) -> bytes:
    bucket = _client().bucket(settings.GOOGLE_CLOUD_STORAGE_BUCKET)
    blob = bucket.blob(gcs_path)
    data = blob.download_as_bytes()
    logger.debug(f"GCS download: {gcs_path}")
    return data


def read_upload_from_public_url(url: str) -> bytes:
    """Lee de GCS a partir de la URL publica que guardamos en el modelo
    (https://storage.googleapis.com/<bucket>/<path>[?query]). Lanza IndexError
    si la URL no pertenece al bucket configurado -- el caller decide que hacer."""
    path = url.split(f'{settings.GOOGLE_CLOUD_STORAGE_BUCKET}/', 1)[1].split('?', 1)[0]
    return read_upload(path)


def upload_exists(gcs_path: str) -> bool:
    bucket = _client().bucket(settings.GOOGLE_CLOUD_STORAGE_BUCKET)
    return bucket.blob(gcs_path).exists()
