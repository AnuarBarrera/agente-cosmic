import base64
import html as _html
import io
import json
import logging
import os
import re
import textwrap
import time

import google.genai as genai
from google.cloud import storage
from google.genai import types
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import sync_playwright

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

    def generate(self, caption: str, colors: list[str], tone: str, filename: str, brand_name: str = '') -> str:
        try:
            image_bytes = self._layered_pipeline(caption, colors, tone)
            return self._upload_to_storage(image_bytes, filename)
        except Exception as e:
            logger.error(f"ImageGenerator error: {e}")
            return ''

    # ------------------------------------------------------------------
    # Layered pipeline
    # ------------------------------------------------------------------

    def _layered_pipeline(self, caption: str, colors: list[str], tone: str) -> bytes:
        background_bytes = self._generate_background(caption, colors, tone)
        content = self._generate_post_content(caption)
        return self._render_html_template(background_bytes, content, colors)

    def _generate_background(self, caption: str, colors: list[str], tone: str) -> bytes:
        color_str = ', '.join(colors[:3]) if colors else 'modern vibrant colors'
        prompt = (
            f"Clean professional photograph or stylized illustration. "
            f"Theme: {caption[:80]}. "
            f"Dominant colors: {color_str}. Mood: {tone}. "
            f"Focus on the product, service, environment, or concept — "
            f"NOT a portrait or headshot. Show objects, spaces, or abstract visuals. "
            f"Square 1:1 format, high quality. "
            f"Absolutely NO text, NO letters, NO words, NO UI elements, "
            f"NO logos, NO design templates, NO social media template layout."
        )
        return self._generate_with_retry(prompt)

    def _extract_headline(self, caption: str) -> str:
        """Fallback headline extraction when Gemini is unavailable."""
        clean = ' '.join(w for w in caption.split() if not w.startswith('#'))
        # Take first complete sentence if short enough
        sentences = re.split(r'(?<=[.!?])\s+', clean)
        first = sentences[0].strip() if sentences else clean
        words = first.split()
        if len(words) <= 5:
            return first or caption[:30]
        # Long sentence: take 4 words, drop trailing connectors
        _CONNECTORS = {'de', 'que', 'la', 'el', 'los', 'las', 'un', 'una', 'y',
                       'o', 'pero', 'con', 'por', 'para', 'sin', 'su', 'lo', 'al',
                       'del', 'se', 'en', 'a', 'es', 'no', 'si', 'le', 'tu', 'te'}
        selected = words[:4]
        while selected and selected[-1].lower().strip('¡¿.,!?') in _CONNECTORS:
            selected.pop()
        return ' '.join(selected) or clean[:25]

    def _generate_post_content(self, caption: str) -> dict:
        """Text-only Gemini call → {headline, subtitle, cta, tag} for HTML template."""
        _FALLBACK = {
            'headline': self._extract_headline(caption),
            'subtitle': (caption[:120] if caption else '').strip(),
            'cta': 'Contáctanos hoy',
            'tag': 'DESTACADO',
        }
        try:
            client = _vertex_client()
            prompt = (
                f"Caption del post: \"{caption[:300]}\"\n\n"
                "Genera el contenido para un post de Instagram con estos 4 elementos:\n"
                "1. headline: 3-5 palabras. Frase gancho, memorable. Sin nombres de marca, URLs, hashtags.\n"
                "2. subtitle: 8-15 palabras. Amplía el headline con el beneficio clave. Español correcto.\n"
                "3. cta: 2-4 palabras. Llamada a la acción directa. (Ej: 'Empieza hoy', 'Solicita tu demo')\n"
                "4. tag: 1-3 palabras EN MAYÚSCULAS. Categoría del sector. (Ej: 'DISEÑO WEB', 'NUTRICIÓN')\n\n"
                "REGLAS: Español impecable. Sin inventar palabras. Sin duplicar letras.\n"
                "Responde ÚNICAMENTE este JSON (sin markdown):\n"
                "{\"headline\":\"...\",\"subtitle\":\"...\",\"cta\":\"...\",\"tag\":\"...\"}"
            )
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    system_instruction=(
                        "Eres 'Cosmic', Director Creativo de Agente Cosmic. "
                        "Generas contenido de marketing para redes sociales. "
                        "Español impecable. Cero errores ortográficos. Nunca inventes palabras. "
                        "Frases para imagen: cortas, impactantes, máximo 5 palabras."
                    ),
                ),
            )
            raw = resp.text.strip()
            match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
                return {
                    'headline': str(data.get('headline', '')).strip() or _FALLBACK['headline'],
                    'subtitle': str(data.get('subtitle', '')).strip() or _FALLBACK['subtitle'],
                    'cta': str(data.get('cta', '')).strip() or _FALLBACK['cta'],
                    'tag': str(data.get('tag', '')).strip().upper() or _FALLBACK['tag'],
                }
        except Exception as e:
            logger.warning(f"Post content generation failed, using fallback: {e}")
        return _FALLBACK

    def _render_html_template(self, background_bytes: bytes, content: dict, colors: list[str]) -> bytes:
        """Inject Imagen 3 background + content into HTML template, render via Playwright → PNG."""
        _TEMPLATE_PATH = os.path.normpath(os.path.join(
            os.path.dirname(__file__),
            '..', 'templates', 'content_pipeline', 'instagram_post.html',
        ))
        with open(_TEMPLATE_PATH) as f:
            html = f.read()

        bg_b64 = base64.b64encode(background_bytes).decode()
        primary = colors[0] if colors else '#e94560'

        html = html.replace('{{bg_data_url}}', f'data:image/png;base64,{bg_b64}')
        html = html.replace('{{primary_color}}', primary)
        html = html.replace('{{tag}}', _html.escape(content.get('tag', 'DESTACADO')))
        html = html.replace('{{headline}}', _html.escape(content.get('headline', '')))
        html = html.replace('{{subtitle}}', _html.escape(content.get('subtitle', '')))
        html = html.replace('{{cta}}', _html.escape(content.get('cta', 'Ver más')))

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
            )
            page = browser.new_page(viewport={'width': 1080, 'height': 1080})
            page.set_content(html, wait_until='domcontentloaded')
            png_bytes = page.screenshot(full_page=False)
            browser.close()

        return png_bytes

    # ------------------------------------------------------------------
    # Low-level helpers (kept for fallback/legacy use)
    # ------------------------------------------------------------------

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
            alpha = int(180 * (1 - y / bar_h))
            draw_overlay.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))

        img.paste(overlay, (0, h - bar_h), overlay)

        draw = ImageDraw.Draw(img)
        font_size = max(22, w // 28)
        _DEJAVU = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
        try:
            font = ImageFont.truetype(_DEJAVU, size=font_size)
        except (OSError, IOError):
            try:
                font = ImageFont.load_default(size=font_size)
            except TypeError:
                font = ImageFont.load_default()

        padding = w // 20
        max_chars = max(20, w // (font_size // 2))
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
                    logger.warning(f"Rate limit, reintento {attempt + 1} en {delay}s")
                    time.sleep(delay)
                else:
                    raise

    def _generate_with_vertex(self, prompt: str) -> bytes:
        client = _vertex_client()
        model = settings.VERTEX_IMAGE_MODEL
        if 'imagen' in model:
            resp = client.models.generate_images(
                model=model,
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio='1:1',
                ),
            )
            if resp.generated_images:
                return resp.generated_images[0].image.image_bytes
            raise ValueError("No image returned by Imagen")
        resp = client.models.generate_content(
            model=model,
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
