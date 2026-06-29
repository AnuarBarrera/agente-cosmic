import json
import logging
import re
import google.genai as genai
from django.conf import settings
from core.brand_dna.models import BrandDNA
from core.shared.metrics_utils import track_external_api, record_tokens

logger = logging.getLogger(__name__)

_PROMPT = (
    "Eres un experto en marketing de contenidos. Genera exactamente 7 posts para redes sociales "
    "para la siguiente marca. Cada post debe ser unico y usar el tono y audiencia de la marca.\n\n"
    "MARCA: {business_name}\n"
    "DESCRIPCION: {description}\n"
    "AUDIENCIA: {audience}\n"
    "TONO: {tone}\n"
    "KEYWORDS: {keywords}\n"
    "ESTILO DE POSTS PREVIOS: {posting_style}\n"
    "HASHTAGS COMUNES: {hashtags}\n\n"
    "Responde UNICAMENTE con un array JSON de 7 objetos, sin markdown:\n"
    "[\n"
    '  {{"caption": "texto del post, maximo {avg_length} caracteres",\n'
    '   "hashtags": ["#tag1", "#tag2", "#tag3"],\n'
    '   "suggested_time": "HH:MM"}}\n'
    "]\n\n"
    "Los horarios sugeridos deben variar entre 09:00, 12:00, 17:00 y 19:00."
)


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


class TextGenerator:
    def generate(self, brand_dna: BrandDNA) -> list[dict]:
        client = _vertex_client()
        prompt = _PROMPT.format(
            business_name=brand_dna.business_name,
            description=brand_dna.description,
            audience=brand_dna.audience,
            tone=brand_dna.tone,
            keywords=', '.join(brand_dna.keywords or []),
            posting_style=brand_dna.posting_style or 'No disponible',
            hashtags=', '.join(brand_dna.common_hashtags or []),
            avg_length=brand_dna.avg_caption_length,
        )
        with track_external_api('gemini', operation='text_gen'):
            resp = client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, contents=prompt)
        record_tokens(resp, operation='text_gen',
                      prompt_preview=prompt[:500],
                      response_preview=resp.text[:500] if resp.text else '')
        raw = resp.text.strip()
        raw = re.sub(r'^```(?:json)?\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw)
        raw = raw.strip()
        posts = json.loads(raw)
        return posts[:7]
