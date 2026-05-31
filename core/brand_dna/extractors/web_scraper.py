import json
import logging
import requests
import google.genai as genai
from bs4 import BeautifulSoup
from django.conf import settings

logger = logging.getLogger(__name__)

_PROMPT_TEMPLATE = """
Analiza el siguiente texto extraído de un sitio web de negocio y extrae su información de marca.
Responde ÚNICAMENTE con un JSON válido, sin markdown, con esta estructura exacta:
{{
  "business_name": "nombre del negocio",
  "description": "qué hace el negocio en 1-2 oraciones",
  "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"],
  "audience": "descripción del cliente ideal en 1 oración",
  "tone": "uno de: formal, casual, inspiracional, urgente, profesional, amigable"
}}

Texto del sitio:
{html}
"""

_FALLBACK = {
    'business_name': 'Negocio',
    'description': 'Empresa con presencia digital.',
    'keywords': [],
    'audience': 'Clientes generales',
    'tone': 'profesional',
}


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


class WebScraper:
    def extract(self, url: str) -> dict:
        try:
            text = self._fetch_text(url)
            return self._analyze_with_vertex(text)
        except Exception as e:
            logger.error(f"WebScraper error para {url}: {e}")
            return _FALLBACK.copy()

    def _fetch_text(self, url: str) -> str:
        response = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(response.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer']):
            tag.decompose()
        return soup.get_text(separator=' ', strip=True)[:4000]

    def _analyze_with_vertex(self, text: str) -> dict:
        client = _vertex_client()
        prompt = _PROMPT_TEMPLATE.format(html=text)
        resp = client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, contents=prompt)
        raw = resp.text.strip().lstrip('```json').lstrip('```').rstrip('```').strip()
        return json.loads(raw)
