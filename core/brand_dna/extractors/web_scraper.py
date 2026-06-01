import json
import logging
import re
import requests
import google.genai as genai
from bs4 import BeautifulSoup
from django.conf import settings

logger = logging.getLogger(__name__)

_HEX_RE = re.compile(r'#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b')

_PROMPT_TEMPLATE = """
Analiza el siguiente texto extraído de un sitio web de negocio y extrae su información de marca.
Responde ÚNICAMENTE con un JSON válido, sin markdown, con esta estructura exacta:
{{
  "business_name": "nombre del negocio",
  "description": "qué hace el negocio en 1-2 oraciones",
  "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"],
  "audience": "descripción del cliente ideal en 1 oración",
  "tone": "uno de: formal, casual, inspiracional, urgente, profesional, amigable",
  "brand_colors": ["#hexcolor1", "#hexcolor2"]
}}

Colores CSS detectados en el sitio (úsalos como referencia para brand_colors, filtra blancos/negros puros):
{css_colors}

Texto del sitio:
{html}
"""

_FALLBACK = {
    'business_name': 'Negocio',
    'description': 'Empresa con presencia digital.',
    'keywords': [],
    'audience': 'Clientes generales',
    'tone': 'profesional',
    'brand_colors': [],
}


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


def _normalize_hex(h: str) -> str:
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    return f'#{h.upper()}'


def _is_neutral(h: str) -> bool:
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    brightness = (r + g + b) / 3
    spread = max(r, g, b) - min(r, g, b)
    return brightness > 240 or brightness < 15 or spread < 20


class WebScraper:
    def extract(self, url: str) -> dict:
        try:
            text, css_colors = self._fetch_text_and_colors(url)
            return self._analyze_with_vertex(text, css_colors)
        except Exception as e:
            logger.error(f"WebScraper error para {url}: {e}")
            return _FALLBACK.copy()

    def _fetch_text_and_colors(self, url: str) -> tuple[str, list[str]]:
        response = requests.get(url, timeout=15, headers={'User-Agent': 'Mozilla/5.0'})
        soup = BeautifulSoup(response.text, 'html.parser')

        css_text = ' '.join(tag.get_text() for tag in soup.find_all('style'))
        raw_colors = _HEX_RE.findall(css_text)
        seen, colors = set(), []
        for h in raw_colors:
            normalized = _normalize_hex(h)
            if normalized not in seen and not _is_neutral(normalized[1:]):
                seen.add(normalized)
                colors.append(normalized)
            if len(colors) >= 10:
                break

        for tag in soup(['script', 'style', 'nav', 'footer']):
            tag.decompose()
        text = soup.get_text(separator=' ', strip=True)[:4000]
        return text, colors

    def _analyze_with_vertex(self, text: str, css_colors: list[str]) -> dict:
        client = _vertex_client()
        colors_str = ', '.join(css_colors) if css_colors else 'No se detectaron colores'
        prompt = _PROMPT_TEMPLATE.format(html=text, css_colors=colors_str)
        resp = client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, contents=prompt)
        raw = resp.text.strip()
        raw = re.sub(r'^```(?:json)?\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw)
        result = json.loads(raw.strip())
        result.setdefault('brand_colors', css_colors[:5])
        return result
