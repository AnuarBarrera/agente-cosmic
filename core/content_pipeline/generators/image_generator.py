import base64
import html as _html
import json
import logging
import os
import re
import time

import google.genai as genai
from google.cloud import storage
from google.genai import types
from django.conf import settings
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

    def generate(self, caption: str, colors: list[str], tone: str, filename: str, brand_name: str = '', keywords: list[str] = None, description: str = '', product_image_bytes: bytes = None) -> str:
        try:
            image_bytes = self._layered_pipeline(caption, colors, tone, keywords or [], description, product_image_bytes=product_image_bytes)
            return self._upload_to_storage(image_bytes, filename)
        except Exception as e:
            logger.error(f"ImageGenerator error: {e}")
            return ''

    # ------------------------------------------------------------------
    # Layered pipeline
    # ------------------------------------------------------------------

    def _layered_pipeline(self, caption: str, colors: list[str], tone: str, keywords: list[str] = None, description: str = '', product_image_bytes: bytes = None) -> bytes:
        if product_image_bytes:
            background_bytes = product_image_bytes
            kw_str = ', '.join((keywords or [])[:3])
            brand_context = f"{description[:150]}. Tono: {tone}. Palabras clave: {kw_str}." if description else f"Tono: {tone}."
            content = self._generate_post_content(caption, product_image_bytes=product_image_bytes, brand_context=brand_context)
        else:
            background_bytes = self._generate_background(caption, colors, tone, keywords or [], description)
            content = self._generate_post_content(caption, product_image_bytes=None)
        return self._render_html_template(background_bytes, content, colors)

    def _generate_background(self, caption: str, colors: list[str], tone: str, keywords: list[str] = None, description: str = '') -> bytes:
        color_str = ', '.join(colors[:3]) if colors else 'modern vibrant colors'
        # Ground Imagen 3 in the client's actual business context
        subject_hint = ''
        kw = [k for k in (keywords or []) if k][:4]
        if kw:
            subject_hint = f"Show: {', '.join(kw)}. "
        elif description:
            subject_hint = f"Context: {description[:80]}. "
        prompt = (
            f"Real-world stock photograph, bright natural lighting, shallow depth of field, DSLR camera quality. "
            f"Subject: {caption[:80]}. "
            f"{subject_hint}"
            f"Color palette: {color_str}. "
            f"Scene: authentic professional environment, real objects, real surfaces — "
            f"desk, materials, tools, products, plants, coffee, notebooks. "
            f"Any screens or monitors must show a BLANK or turned-off display — "
            f"NO content, NO images, NO graphics inside any screen. "
            f"NOT a CGI render. NOT a 3D illustration. NOT a digital composite. "
            f"NOT abstract shapes. NOT floating objects. NOT a portrait. "
            f"NOT minimalist. NOT dark moody lighting. "
            f"Square 1:1 format, photorealistic. "
            f"Absolutely NO text, NO letters, NO words, NO UI elements, "
            f"NO logos, NO templates, NO social media layouts."
        )
        last_bytes = None
        for attempt in range(3):
            last_bytes = self._generate_with_retry(prompt)
            if self._validate_background(last_bytes):
                return last_bytes
            if attempt < 2:
                logger.warning(f"Background QC failed (attempt {attempt + 1}/3), regenerating...")
        logger.warning("Background QC: all retries exhausted, using last generated image")
        return last_bytes

    def _validate_background(self, image_bytes: bytes) -> bool:
        """Gemini reviews the generated image for forbidden elements. Returns True if ok."""
        try:
            client = _vertex_client()
            image_part = types.Part.from_bytes(data=image_bytes, mime_type='image/png')
            prompt = (
                "Analyze this image strictly. Reply ONLY with this JSON (no markdown):\n"
                "{\"has_text\": <bool>, \"is_abstract_3d\": <bool>, \"has_screen_content\": <bool>, \"ok\": <bool>}\n\n"
                "has_text: true if ANY visible letters, words or text appear in the image.\n"
                "is_abstract_3d: true if the image has floating 3D geometric shapes, abstract CGI objects, or surreal renders.\n"
                "has_screen_content: true if any screen/monitor shows visible content (not blank or off).\n"
                "ok: true ONLY if has_text=false AND is_abstract_3d=false AND has_screen_content=false."
            )
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL,
                contents=[image_part, prompt],
            )
            raw = resp.text.strip()
            match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
                ok = bool(data.get('ok', True))
                if not ok:
                    flags = [k for k in ('has_text', 'is_abstract_3d', 'has_screen_content') if data.get(k)]
                    logger.warning(f"Background QC rejected: {', '.join(flags)}")
                return ok
        except Exception as e:
            logger.warning(f"Background QC error (assuming ok): {e}")
        return True

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

    def _generate_post_content(self, caption: str, product_image_bytes: bytes = None, brand_context: str = '') -> dict:
        """Gemini generates {headline, subtitle, cta, tag}. Multimodal if product_image_bytes provided."""
        _FALLBACK = {
            'headline': self._extract_headline(caption),
            'subtitle': (caption[:120] if caption else '').strip(),
            'cta': 'Contáctanos hoy',
            'tag': 'DESTACADO',
        }
        try:
            client = _vertex_client()
            if product_image_bytes:
                mime = 'image/png' if product_image_bytes[:4] == b'\x89PNG' else 'image/jpeg'
                image_part = types.Part.from_bytes(data=product_image_bytes, mime_type=mime)
                prompt = (
                    f"ADN de marca: {brand_context}\n"
                    f"Caption del post (refleja la propuesta de la marca): \"{caption[:200]}\"\n\n"
                    "Hay una imagen adjunta que se usará como FONDO VISUAL del post.\n"
                    "Tu tarea: genera copy que comunique la propuesta de valor DE LA MARCA,\n"
                    "usando la imagen como contexto o punto de conexión — NO como tema principal.\n"
                    "Si la imagen conecta naturalmente con la marca, úsala. Si no, el copy habla de la marca\n"
                    "y el visual simplemente acompaña.\n\n"
                    "Genera 4 elementos:\n"
                    "1. headline: 3-5 palabras. Frase gancho que represente la marca. Sin nombres de marca.\n"
                    "2. subtitle: 8-15 palabras. Beneficio clave o propuesta de valor de la marca.\n"
                    "3. cta: 2-4 palabras. Llamada a la acción acorde a la marca.\n"
                    "4. tag: 1-3 palabras EN MAYÚSCULAS. Sector o categoría de la marca.\n\n"
                    "REGLAS: Español impecable. Sin inventar palabras.\n"
                    "Responde ÚNICAMENTE este JSON (sin markdown):\n"
                    "{\"headline\":\"...\",\"subtitle\":\"...\",\"cta\":\"...\",\"tag\":\"...\"}"
                )
                contents = [image_part, prompt]
            else:
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
                contents = prompt
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL,
                contents=contents,
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

        bg_mime = 'image/png' if background_bytes[:4] == b'\x89PNG' else 'image/jpeg'
        bg_b64 = base64.b64encode(background_bytes).decode()
        primary = colors[0] if colors else '#e94560'

        html = html.replace('{{bg_data_url}}', f'data:{bg_mime};base64,{bg_b64}')
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
