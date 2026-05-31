import logging
import google.genai as genai
from google.genai import types
from google.cloud import storage
from django.conf import settings

logger = logging.getLogger(__name__)


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
            image_bytes = self._generate_with_vertex(prompt)
            return self._upload_to_storage(image_bytes, filename)
        except Exception as e:
            logger.error(f"ImageGenerator error: {e}")
            return ''

    def _build_prompt(self, caption: str, colors: list[str], tone: str) -> str:
        color_str = ', '.join(colors[:3]) if colors else 'modern vibrant colors'
        return (
            f"Professional social media post image. Concept: {caption[:120]}. "
            f"Use brand colors: {color_str}. Visual style: {tone}, clean, "
            f"high quality, square format 1:1, no text overlay."
        )

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
