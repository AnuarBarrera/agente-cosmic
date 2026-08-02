import logging
import google.genai as genai
from google.cloud import vision
from google.genai import types
from django.conf import settings
from core.shared.metrics_utils import track_external_api, record_tokens, vertex_labels

logger = logging.getLogger(__name__)

_FALLBACK = {'primary_colors': [], 'logo_elements': ''}

_VISION_PROMPT = (
    "Analiza esta imagen de logo de marca. Describe en 1-2 oraciones: "
    "estilo tipografico, estilo grafico (minimalista, ilustrativo, geometrico), "
    "y sensacion general de la marca. Solo la descripcion, sin listas ni formato."
)


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION_TEXT,
    )


class LogoAnalyzer:
    def analyze(self, image_bytes: bytes, mime_type: str) -> dict:
        try:
            colors = self._extract_colors(image_bytes)
            elements = self._describe_with_vertex(image_bytes, mime_type)
            return {'primary_colors': colors, 'logo_elements': elements}
        except Exception as e:
            logger.error(f"LogoAnalyzer error: {e}")
            return _FALLBACK.copy()

    def _extract_colors(self, image_bytes: bytes) -> list[str]:
        client = vision.ImageAnnotatorClient()
        image = vision.Image(content=image_bytes)
        features = [vision.Feature(type_=vision.Feature.Type.IMAGE_PROPERTIES)]
        request = vision.AnnotateImageRequest(image=image, features=features)
        with track_external_api('cloud_vision'):
            response = client.annotate_image(request=request)
        colors = response.image_properties_annotation.dominant_colors.colors
        hex_colors = []
        for c in sorted(colors, key=lambda x: x.pixel_fraction, reverse=True)[:5]:
            r, g, b = int(c.color.red), int(c.color.green), int(c.color.blue)
            hex_colors.append(f'#{r:02x}{g:02x}{b:02x}')
        return hex_colors

    def _describe_with_vertex(self, image_bytes: bytes, mime_type: str) -> str:
        client = _vertex_client()
        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
        with track_external_api('gemini'):
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL,
                contents=[_VISION_PROMPT, image_part],
                config=types.GenerateContentConfig(labels=vertex_labels()),
            )
        record_tokens(resp)
        return resp.text.strip()
