import json
import logging
import os
import re
import subprocess
import tempfile
import time

from django.conf import settings
from google.cloud import storage
from google.genai import types

from core.content_pipeline.generators.image_generator import _detect_mime, _vertex_client
from core.shared.metrics import GCS_OPERATIONS
from core.shared.metrics_utils import track_external_api, record_tokens, vertex_labels

logger = logging.getLogger(__name__)

# gemini-3-pro-image / gemini-3.1-flash-image devuelven 404 (sin acceso) en el
# proyecto de Vertex AI de Cosmic al 2026-07-27 — gemini-2.5-flash-image confirmado
# funcional con llamada real, usar este hasta que se confirme acceso a los mas nuevos.
_REFERENCE_IMAGE_MODEL = 'publishers/google/models/gemini-2.5-flash-image'

_VEO_POLL_TIMEOUT_SECONDS = 300
_VEO_POLL_INTERVAL_SECONDS = 10

_SCENE_PROMPT_TEMPLATE = (
    "Using the product shown in this reference image, generate a brand-new professional "
    "product photograph for {business_name}: a completely new scene, new background, new "
    "lighting and composition — NOT an edit of the input image. Incorporate this exact "
    "product as it appears (same shape, color, texture, any visible branding) as the subject "
    "of the new photograph. Photorealistic, studio-quality, natural lighting."
)

_VIDEO_PROMPT_TEMPLATE = (
    "Cinematic slow push-in on this product photography scene for {business_name}. "
    "Gentle ambient motion (light shifting, soft background movement) — keep the product "
    "and composition stable. Photorealistic, 4k."
)

_QC_PROMPT = (
    "Analyze this image strictly. Reply ONLY with this JSON (no markdown):\n"
    "{\"has_text\": <bool>, \"is_abstract_3d\": <bool>, \"has_screen_content\": <bool>, "
    "\"has_malformed_object\": <bool>, \"has_unrealistic_grounding\": <bool>, \"ok\": <bool>}\n\n"
    "has_text: true if ANY readable letters, words, numbers or text appear ANYWHERE in the image — "
    "including text on signs, labels, books, packaging, walls, or any surface — OR any logo/brand "
    "mark of any kind, even a purely graphic symbol with no letters (real or invented). Even partial "
    "words or blurry text count. Be very strict.\n"
    "is_abstract_3d: true if the image has floating 3D geometric shapes, abstract CGI objects, or surreal renders.\n"
    "has_screen_content: true if any computer monitor, laptop screen, phone screen, TV, or digital display "
    "shows visible content — including websites, text, images, graphics, UI elements, or any non-blank content. "
    "A screen must be completely BLACK or clearly turned off to not count. Be very strict.\n"
    "has_malformed_object: true if any object, tool, instrument, hand, or mechanical item is anatomically or "
    "physically impossible or distorted — wrong number of parts, parts connected incorrectly, missing pieces "
    "a real version of the object would have, or a structurally implausible shape. Examine objects with "
    "multiple connected parts (tools, instruments, hands, machinery) closely. Only flag clear, obvious cases.\n"
    "has_unrealistic_grounding: true if the main subject (person, animal, or product) appears to float, "
    "hover, or is otherwise disconnected from the surface/floor/background it should be resting or "
    "standing on — no visible contact point, no matching contact shadow directly beneath it, wrong "
    "scale or perspective versus the background, or a dynamic mid-air pose (jumping, running) composited "
    "onto a background that implies the subject is stationary. This commonly happens when a subject's "
    "pose doesn't match its new background. Only flag clear, obvious cases where it looks physically wrong.\n"
    "ok: true ONLY if has_text=false AND is_abstract_3d=false AND has_screen_content=false "
    "AND has_malformed_object=false AND has_unrealistic_grounding=false."
)

# Offsets de frames a auditar dentro del video (segundos) — inicio/medio/fin, mismo
# criterio que reproduce el hallazgo real del 2026-07-27 (logo alucinado en un frame
# intermedio que no estaba en el frame inicial).
_QC_FRAME_OFFSETS = (1.0, 4.0, 7.0)


class ProductReferenceGenerator:
    """Pipeline experimental, solo-admin: usa una foto real de producto como
    referencia para que Gemini/Veo generen una escena e reel NUEVOS que la
    incorporen — distinto de BGSWAP (HALLAZGO 65, eliminado), que editaba/rellenaba
    el fondo de la foto original. Cadena validada con llamadas reales el 2026-07-27
    (ver docs/superpowers/specs/2026-07-27-product-reference-pipeline-design.md)."""

    def __init__(self, bucket_name: str):
        self._bucket = bucket_name

    def generate_image(self, product_photo_bytes: bytes, business_name: str, filename: str) -> str:
        scene_bytes = self._generate_scene(product_photo_bytes, business_name)
        if scene_bytes is None:
            return ''
        if not self._validate_scene(scene_bytes):
            logger.warning("ProductReferenceGenerator: QC rechazo la escena generada (generate_image)")
            return ''
        return self._upload_to_storage(scene_bytes, filename, 'image/png', 'product-samples')

    def generate_reel(self, product_photo_bytes: bytes, business_name: str, filename_prefix: str) -> tuple[str, str]:
        scene_bytes = self._generate_scene(product_photo_bytes, business_name)
        if scene_bytes is None:
            return '', ''
        if not self._validate_scene(scene_bytes):
            logger.warning("ProductReferenceGenerator: QC rechazo la escena generada (generate_reel)")
            return '', ''

        video_bytes = self._animate_scene(scene_bytes, business_name)
        if video_bytes is None:
            return '', ''

        for offset in _QC_FRAME_OFFSETS:
            frame_bytes = self._extract_frame(video_bytes, offset_seconds=offset)
            if frame_bytes is not None and not self._validate_scene(frame_bytes):
                logger.warning(f"ProductReferenceGenerator: QC rechazo el frame en {offset}s del video")
                return '', ''

        poster_url = self._upload_to_storage(scene_bytes, f'{filename_prefix}-poster', 'image/png', 'product-samples')
        video_url = self._upload_to_storage(video_bytes, filename_prefix, 'video/mp4', 'product-samples')
        return video_url, poster_url

    def _generate_scene(self, product_photo_bytes: bytes, business_name: str) -> bytes | None:
        try:
            client = _vertex_client()
            mime = _detect_mime(product_photo_bytes)
            image_part = types.Part.from_bytes(data=product_photo_bytes, mime_type=mime)
            prompt = _SCENE_PROMPT_TEMPLATE.format(business_name=business_name)
            with track_external_api('gemini', operation='product_reference_scene'):
                resp = client.models.generate_content(
                    model=_REFERENCE_IMAGE_MODEL,
                    contents=[image_part, prompt],
                    config=types.GenerateContentConfig(response_modalities=['IMAGE', 'TEXT']),
                )
            record_tokens(resp, operation='product_reference_scene', prompt_preview=prompt[:500])
            for part in resp.candidates[0].content.parts:
                if part.inline_data:
                    return part.inline_data.data
            return None
        except Exception as e:
            logger.warning(f"ProductReferenceGenerator._generate_scene fallo: {e}")
            return None

    def _animate_scene(self, scene_bytes: bytes, business_name: str) -> bytes | None:
        try:
            client = _vertex_client()
            prompt = _VIDEO_PROMPT_TEMPLATE.format(business_name=business_name)
            with track_external_api('veo', operation='product_reference_video'):
                operation = client.models.generate_videos(
                    model=settings.VERTEX_VIDEO_MODEL,
                    prompt=prompt,
                    image=types.Image(image_bytes=scene_bytes, mime_type='image/png'),
                    config=types.GenerateVideosConfig(
                        aspect_ratio='9:16', duration_seconds=8, number_of_videos=1, generate_audio=False,
                    ),
                )
            poll_start = time.monotonic()
            while not operation.done:
                if time.monotonic() - poll_start > _VEO_POLL_TIMEOUT_SECONDS:
                    logger.warning("ProductReferenceGenerator._animate_scene: timeout esperando a Veo")
                    return None
                time.sleep(_VEO_POLL_INTERVAL_SECONDS)
                operation = client.operations.get(operation)
            if operation.error:
                logger.warning(f"ProductReferenceGenerator._animate_scene: Veo devolvio error: {operation.error}")
                return None
            generated = operation.result.generated_videos
            if not generated:
                return None
            return generated[0].video.video_bytes
        except Exception as e:
            logger.warning(f"ProductReferenceGenerator._animate_scene fallo: {e}")
            return None

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
            logger.warning(f"ProductReferenceGenerator._extract_frame fallo en offset {offset_seconds}s: {e}")
            return None

    def _validate_scene(self, image_bytes: bytes) -> bool:
        try:
            client = _vertex_client()
            image_part = types.Part.from_bytes(data=image_bytes, mime_type='image/png')
            with track_external_api('gemini', operation='product_reference_qc'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[image_part, _QC_PROMPT],
                    config=types.GenerateContentConfig(labels=vertex_labels()),
                )
            record_tokens(resp, operation='product_reference_qc', prompt_preview=_QC_PROMPT[:500])
            raw = resp.text.strip()
            match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
                return bool(data.get('ok', True))
        except Exception as e:
            logger.warning(f"ProductReferenceGenerator._validate_scene error (assuming ok): {e}")
        return True

    def _upload_to_storage(self, data: bytes, filename: str, content_type: str, folder: str) -> str:
        ext = 'mp4' if content_type == 'video/mp4' else 'png'
        with track_external_api('gcs'):
            client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
            bucket = client.bucket(self._bucket)
            blob = bucket.blob(f'{folder}/{filename}.{ext}')
            blob.upload_from_string(data, content_type=content_type)
        GCS_OPERATIONS.labels(operation='upload').inc()
        return f'{blob.public_url}?v={int(time.time())}'
