import json
import logging
import google.genai as genai
from google.genai import types
from django.conf import settings
from pydantic import BaseModel
from core.shared.metrics_utils import track_external_api, record_tokens, vertex_labels

logger = logging.getLogger(__name__)

_FALLBACK = {'description': '', 'category': ''}

_PROMPT = (
    "Analiza esta foto de un producto real subida por un negocio.\n\n"
    "description: 1-2 oraciones describiendo el producto -- tipo, colores, "
    "materiales, estilo, detalles distintivos. Solo la descripcion, sin "
    "listas ni formato.\n"
    "category: el giro/tipo de producto en 1-3 palabras, forma normalizada "
    "en espanol (ej. 'joyeria', 'reposteria', 'ropa', 'muebles')."
)


class ProductPhotoAnalysisSchema(BaseModel):
    description: str
    category: str


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION_TEXT,
    )


class ProductPhotoAnalyzer:
    def analyze(self, image_bytes: bytes, mime_type: str) -> dict:
        try:
            client = _vertex_client()
            image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            with track_external_api('gemini', operation='product_photo_analysis'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[_PROMPT, image_part],
                    config=types.GenerateContentConfig(
                        labels=vertex_labels(),
                        response_mime_type="application/json",
                        response_schema=ProductPhotoAnalysisSchema,
                    ),
                )
            record_tokens(resp, operation='product_photo_analysis',
                          prompt_preview=_PROMPT[:500],
                          response_preview=resp.text[:500] if resp.text else '')
            data = json.loads(resp.text)
            return {
                'description': (data.get('description') or '').strip(),
                'category': (data.get('category') or '').strip(),
            }
        except Exception as e:
            logger.error(f"ProductPhotoAnalyzer error: {e}")
            return _FALLBACK.copy()
