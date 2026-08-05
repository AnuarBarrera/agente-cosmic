import logging
import json
import os
import subprocess
import tempfile
import time
import uuid

from django.conf import settings
from google.cloud import storage, vision

from core.content_pipeline.image_utils import enhance_photo_classic
from core.shared.metrics import GCS_OPERATIONS
from core.shared.metrics_utils import track_external_api, record_hyperframes_generation

logger = logging.getLogger(__name__)

_HYPERFRAMES_PROJECT_DIR = os.path.normpath(os.path.join(
    os.path.dirname(__file__), '..', 'hyperframes_reel',
))
_HYPERFRAMES_BINARY = os.path.join(_HYPERFRAMES_PROJECT_DIR, 'node_modules', '.bin', 'hyperframes')
_HYPERFRAMES_TIMEOUT_SECONDS = 120
_SHOWCASE_COMPOSITION = 'compositions/product-showcase.html'

_SCREENSHOT_LABELS = {'screenshot', 'user interface', 'software'}
_SCREENSHOT_LABEL_THRESHOLD = 0.5

_FALLBACK_PRIMARY_COLOR = '#e94560'
_FALLBACK_SECONDARY_COLOR = '#3ED694'

_REJECT_SCREENSHOT_MESSAGE = (
    'La foto que subiste parece ser una captura de pantalla (de una app o red social), '
    'no una foto directa del producto. Sube una foto tomada directamente del producto, '
    'no una captura de pantalla.'
)
_REJECT_UNSAFE_MESSAGE = 'El resultado fue rechazado por posible contenido sensible. Intenta con otra foto.'


class ProductShowcaseGenerator:
    """Pipeline solo-admin: toma una foto real de producto (sin regenerarla con IA) y la
    compone dentro de una plantilla 3D animada (HyperFrames/Three.js). Reemplaza el
    pipeline anterior (Gemini regenera la escena + Veo la anima) para eliminar
    alucinaciones y rechazos falsos-positivos de marca de agua (HALLAZGO IMG-13).
    Ver docs/superpowers/specs/2026-08-05-product-showcase-3d-pipeline-design.md."""

    def __init__(self, bucket_name: str):
        self._bucket = bucket_name

    def _check_photo_safety(self, photo_bytes: bytes) -> str:
        try:
            client = vision.ImageAnnotatorClient(
                client_options={'quota_project_id': settings.GOOGLE_CLOUD_PROJECT},
            )
            image = vision.Image(content=photo_bytes)
            features = [
                vision.Feature(type_=vision.Feature.Type.SAFE_SEARCH_DETECTION),
                vision.Feature(type_=vision.Feature.Type.LABEL_DETECTION, max_results=10),
            ]
            request = vision.AnnotateImageRequest(image=image, features=features)
            with track_external_api('cloud_vision', operation='product_showcase_safety'):
                resp = client.annotate_image(request=request)
            ss = resp.safe_search_annotation
            unsafe_floor = vision.Likelihood.LIKELY
            if ss.adult >= unsafe_floor or ss.violence >= unsafe_floor or ss.racy >= unsafe_floor:
                logger.warning(f"ProductShowcaseGenerator: gate de seguridad rechazo (adult={ss.adult.name}, "
                                f"violence={ss.violence.name}, racy={ss.racy.name})")
                return _REJECT_UNSAFE_MESSAGE
            for label in resp.label_annotations:
                if label.description.lower() in _SCREENSHOT_LABELS and label.score >= _SCREENSHOT_LABEL_THRESHOLD:
                    logger.warning(f"ProductShowcaseGenerator: gate de screenshot rechazo (label={label.description}, "
                                    f"score={label.score:.2f})")
                    return _REJECT_SCREENSHOT_MESSAGE
            return ''
        except Exception as e:
            logger.warning(f"ProductShowcaseGenerator._check_photo_safety error (fail-open): {e}")
            return ''

    def _generate_showcase(self, enhanced_photo_bytes: bytes, primary_color: str, secondary_color: str) -> bytes | None:
        assets_tmp_dir = os.path.join(_HYPERFRAMES_PROJECT_DIR, 'assets', 'tmp')
        os.makedirs(assets_tmp_dir, exist_ok=True)
        photo_filename = f'{uuid.uuid4().hex}.png'
        photo_path = os.path.join(assets_tmp_dir, photo_filename)
        with open(photo_path, 'wb') as f:
            f.write(enhanced_photo_bytes)
        try:
            variables = {
                'photo_src': f'assets/tmp/{photo_filename}',
                'primary_color': primary_color,
                'secondary_color': secondary_color,
            }
            with tempfile.TemporaryDirectory() as tmp:
                vars_path = os.path.join(tmp, 'vars.json')
                with open(vars_path, 'w') as f:
                    json.dump(variables, f)
                output_path = os.path.join(tmp, 'output.mp4')
                try:
                    subprocess.run(
                        [_HYPERFRAMES_BINARY, 'render', '.', '-c', _SHOWCASE_COMPOSITION,
                         '-o', output_path, '--variables-file', vars_path, '--fps', '24', '--quiet'],
                        cwd=_HYPERFRAMES_PROJECT_DIR, check=True, capture_output=True,
                        timeout=_HYPERFRAMES_TIMEOUT_SECONDS,
                    )
                except Exception as e:
                    logger.warning(f"ProductShowcaseGenerator._generate_showcase fallo: {e}")
                    return None
                record_hyperframes_generation('product_showcase')
                with open(output_path, 'rb') as f:
                    return f.read()
        finally:
            try:
                os.remove(photo_path)
            except OSError:
                pass

    def _extract_frame(self, video_bytes: bytes, offset_seconds: float) -> bytes | None:
        try:
            with tempfile.TemporaryDirectory() as tmp:
                video_path = os.path.join(tmp, 'video.mp4')
                with open(video_path, 'wb') as f:
                    f.write(video_bytes)
                frame_path = os.path.join(tmp, 'frame.png')
                subprocess.run(
                    ['ffmpeg', '-y', '-ss', str(offset_seconds), '-i', video_path, '-vframes', '1', frame_path],
                    check=True, capture_output=True,
                )
                with open(frame_path, 'rb') as f:
                    return f.read()
        except Exception as e:
            logger.warning(f"ProductShowcaseGenerator._extract_frame fallo en offset {offset_seconds}s: {e}")
            return None

    def _upload_to_storage(self, data: bytes, filename: str, content_type: str, folder: str) -> str:
        ext = 'mp4' if content_type == 'video/mp4' else 'png'
        with track_external_api('gcs'):
            client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
            bucket = client.bucket(self._bucket)
            blob = bucket.blob(f'{folder}/{filename}.{ext}')
            blob.upload_from_string(data, content_type=content_type)
        GCS_OPERATIONS.labels(operation='upload').inc()
        return f'{blob.public_url}?v={int(time.time())}'

    def generate_reel(self, product_photo_bytes: bytes, filename_prefix: str, colors: list[str] = None) -> tuple[str, str, str]:
        try:
            rejection = self._check_photo_safety(product_photo_bytes)
            if rejection:
                return '', '', rejection

            enhanced_bytes = enhance_photo_classic(product_photo_bytes)

            colors = colors or []
            primary_color = colors[0] if colors else _FALLBACK_PRIMARY_COLOR
            secondary_color = colors[1] if len(colors) > 1 else _FALLBACK_SECONDARY_COLOR

            video_bytes = self._generate_showcase(enhanced_bytes, primary_color, secondary_color)
            if video_bytes is None:
                video_bytes = self._generate_showcase(enhanced_bytes, primary_color, secondary_color)  # 1 reintento
            if video_bytes is None:
                return '', '', 'No se pudo generar el video. Vuelve a intentar.'

            poster_bytes = self._extract_frame(video_bytes, offset_seconds=1.0)
            poster_url = self._upload_to_storage(
                poster_bytes if poster_bytes else enhanced_bytes, f'{filename_prefix}-poster', 'image/png', 'product-samples',
            )
            video_url = self._upload_to_storage(video_bytes, filename_prefix, 'video/mp4', 'product-samples')
            return video_url, poster_url, ''
        except Exception as e:
            logger.warning(f"ProductShowcaseGenerator.generate_reel fallo: {e}")
            return '', '', 'Ocurrió un error inesperado generando el video. Vuelve a intentar.'
