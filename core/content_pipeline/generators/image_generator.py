import io
import logging
import textwrap
import time

import google.genai as genai
from google.cloud import storage
from google.genai import types
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAYS = [10, 20, 40]


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


class ImageGenerator:
    def __init__(self, bucket_name: str):
        self._bucket = bucket_name

    def generate(self, caption: str, colors: list[str], tone: str, filename: str) -> str:
        try:
            prompt = self._build_prompt(caption, colors, tone)
            image_bytes = self._generate_with_retry(prompt)
            image_bytes = self._overlay_text(image_bytes, caption)
            return self._upload_to_storage(image_bytes, filename)
        except Exception as e:
            logger.error(f"ImageGenerator error: {e}")
            return ''

    def _build_prompt(self, caption: str, colors: list[str], tone: str) -> str:
        color_str = ', '.join(colors[:3]) if colors else 'modern vibrant colors'
        return (
            f"Professional social media post image. Concept: {caption[:120]}. "
            f"Use brand colors: {color_str}. Visual style: {tone}, clean, "
            f"high quality, square format 1:1, photographic or illustrated."
        )

    def _overlay_text(self, image_bytes: bytes, caption: str) -> bytes:
        if not caption or not caption.strip():
            return image_bytes

        img = Image.open(io.BytesIO(image_bytes)).convert('RGBA')
        w, h = img.size

        if w == 0 or h == 0:
            return image_bytes

        bar_h = int(h * 0.25)
        overlay = Image.new('RGBA', (w, bar_h), (0, 0, 0, 0))
        draw_overlay = ImageDraw.Draw(overlay)
        for y in range(bar_h):
            alpha = int(180 * (1 - y / bar_h))  # opaco arriba, transparente abajo
            draw_overlay.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))

        img.paste(overlay, (0, h - bar_h), overlay)

        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.load_default(size=max(20, w // 32))
        except TypeError:
            font = ImageFont.load_default()

        padding = w // 20
        max_chars = max(20, w // (max(20, w // 32) // 2))
        lines = textwrap.wrap(caption[:240], width=max_chars)[:4]
        text = '\n'.join(lines)

        text_y = h - bar_h + padding
        draw.text((padding + 2, text_y + 2), text, font=font, fill=(0, 0, 0, 200))
        draw.text((padding, text_y), text, font=font, fill=(255, 255, 255, 255))

        out = io.BytesIO()
        img.convert('RGB').save(out, format='PNG', optimize=True)
        return out.getvalue()

    def _generate_with_retry(self, prompt: str) -> bytes:
        last_error = None
        for attempt in range(_MAX_RETRIES):
            try:
                return self._generate_with_vertex(prompt)
            except Exception as e:
                last_error = e
                if '429' in str(e) and attempt < _MAX_RETRIES - 1:
                    delay = _RETRY_DELAYS[attempt]
                    logger.warning(f"Rate limit en imagen, reintento {attempt + 1} en {delay}s")
                    time.sleep(delay)
                else:
                    raise
        raise last_error

    def _generate_with_vertex(self, prompt: str) -> bytes:
        client = _vertex_client()
        resp = client.models.generate_content(
            model=settings.VERTEX_IMAGE_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=['IMAGE', 'TEXT']
            ),
        )
        for part in resp.candidates[0].content.parts:
            if part.inline_data:
                return part.inline_data.data
        raise ValueError("No image returned by Vertex AI")

    def _upload_to_storage(self, image_bytes: bytes, filename: str) -> str:
        client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
        bucket = client.bucket(self._bucket)
        blob = bucket.blob(f'posts/{filename}.png')
        blob.upload_from_string(image_bytes, content_type='image/png')
        blob.make_public()
        return blob.public_url
