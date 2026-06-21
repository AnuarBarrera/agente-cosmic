import base64
import html as _html
import json
import logging
import os
import random
import re
import time
import xml.etree.ElementTree as ET

import google.genai as genai
from google.cloud import storage
from google.genai import types
from django.conf import settings
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3

_SVG_ALLOWED_TAGS = frozenset({
    'svg', 'defs', 'rect', 'circle', 'ellipse', 'line', 'polyline', 'polygon',
    'path', 'g', 'use', 'clipPath', 'mask',
    'linearGradient', 'radialGradient', 'stop',
    'filter', 'feGaussianBlur', 'feOffset', 'feBlend', 'feFlood', 'feComposite',
})
_SVG_DANGEROUS_ATTRS = re.compile(
    r'^on\w+|^xlink:href$|^href$|^style$|^class$', re.IGNORECASE
)


def _sanitize_svg(raw_svg: str) -> str:
    try:
        root = ET.fromstring(raw_svg)
    except ET.ParseError:
        return ''
    _strip_element(root)
    tag = _local_tag(root.tag)
    if tag != 'svg':
        return ''
    return ET.tostring(root, encoding='unicode')


def _local_tag(tag: str) -> str:
    if '}' in tag:
        return tag.split('}', 1)[1]
    return tag


def _strip_element(el):
    children_to_remove = []
    for child in el:
        tag = _local_tag(child.tag)
        if tag not in _SVG_ALLOWED_TAGS:
            children_to_remove.append(child)
        else:
            attrs_to_remove = [k for k in child.attrib if _SVG_DANGEROUS_ATTRS.match(k)]
            for attr in attrs_to_remove:
                del child.attrib[attr]
            _strip_element(child)
    for child in children_to_remove:
        el.remove(child)


def _detect_mime(image_bytes: bytes) -> str:
    """Detecta el MIME type por magic bytes (PNG, WebP, JPEG)."""
    if image_bytes[:4] == b'\x89PNG':
        return 'image/png'
    if image_bytes[:4] == b'RIFF' and image_bytes[8:12] == b'WEBP':
        return 'image/webp'
    return 'image/jpeg'


def _luminance(hex_color: str) -> float:
    """Luminancia relativa (0=negro, 1=blanco) de un color hex."""
    try:
        h = hex_color.strip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return (0.299 * r + 0.587 * g + 0.114 * b) / 255
    except Exception:
        return 0.5


def _pick_button_color(colors: list[str]) -> str:
    """Elige el color más oscuro de la paleta que tenga buen contraste con texto blanco.
    Si todos son claros, oscurece el primario."""
    _FALLBACK = '#1a1a2e'
    if not colors:
        return _FALLBACK
    dark = [c for c in colors if _luminance(c) < 0.45]
    if dark:
        return min(dark, key=_luminance)  # el más oscuro disponible
    # Todos claros: oscurecer el primero al 40%
    try:
        h = colors[0].strip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f'#{int(r*0.4):02x}{int(g*0.4):02x}{int(b*0.4):02x}'
    except Exception:
        return _FALLBACK


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

    def generate(self, caption: str, colors: list[str], tone: str, filename: str, brand_name: str = '', keywords: list[str] = None, description: str = '', product_image_bytes: bytes = None, max_qc_retries: int = 2) -> str:
        try:
            image_bytes = self._layered_pipeline(caption, colors, tone, keywords or [], description, product_image_bytes=product_image_bytes, max_qc_retries=max_qc_retries)
            return self._upload_to_storage(image_bytes, filename)
        except Exception as e:
            logger.error(f"ImageGenerator error: {e}")
            return ''

    # ------------------------------------------------------------------
    # Layered pipeline
    # ------------------------------------------------------------------

    def _layered_pipeline(self, caption: str, colors: list[str], tone: str, keywords: list[str] = None, description: str = '', product_image_bytes: bytes = None, max_qc_retries: int = 2) -> bytes:
        if product_image_bytes:
            kw_str = ', '.join((keywords or [])[:3])
            brand_context = f"{description[:150]}. Tono: {tone}. Palabras clave: {kw_str}." if description else f"Tono: {tone}."
            background_bytes, svg_overlay = self._generate_product_scene(
                product_image_bytes, caption, colors, tone, max_qc_retries=max_qc_retries
            )
            content = self._generate_post_content(caption, product_image_bytes=product_image_bytes, brand_context=brand_context)
            result = self._render_html_template(background_bytes, content, colors, svg_overlay=svg_overlay)
            if max_qc_retries > 0 and svg_overlay and not self._validate_final_image(result):
                logger.warning("Final QC falló — reintentando sin SVG overlay")
                result = self._render_html_template(background_bytes, content, colors, svg_overlay='')
            return result
        else:
            background_bytes = self._generate_background(caption, colors, tone, keywords or [], description, max_qc_retries=max_qc_retries)
            content = self._generate_post_content(caption, product_image_bytes=None)
            svg_overlay = ''
        return self._render_html_template(background_bytes, content, colors, svg_overlay=svg_overlay)

    def _generate_product_scene(self, product_image_bytes: bytes, caption: str, colors: list[str], tone: str, max_qc_retries: int = 2) -> tuple[bytes, str]:
        """Pipeline agéntico de 3 pasos con QC en la escena generada:
        1. Gemini Director de Arte → prompt de entorno premium específico para este producto
        2. Imagen 3 BGSWAP → producto pixel-perfect sobre ese entorno (con reintento si QC falla)
        3. Gemini Iluminador → SVG overlay de sombra/luz para armonizar (solo si BGSWAP tuvo éxito)
        """
        env_prompt = self._analyze_product_style(product_image_bytes, caption, colors, tone)
        total_attempts = max_qc_retries + 1
        scene_bytes, bgswap_ok = product_image_bytes, False
        for attempt in range(total_attempts):
            candidate_bytes, candidate_ok = self._bgswap_product(product_image_bytes, env_prompt)
            if not candidate_ok:
                scene_bytes, bgswap_ok = product_image_bytes, False
                break
            if max_qc_retries == 0 or self._validate_background(candidate_bytes):
                scene_bytes, bgswap_ok = candidate_bytes, True
                break
            if attempt < max_qc_retries:
                logger.warning(f"Scene QC falló (intento {attempt + 1}/{total_attempts}), reintentando BGSWAP...")
            else:
                logger.warning("Scene QC: reintentos agotados, usando última escena generada")
                scene_bytes, bgswap_ok = candidate_bytes, True
        svg_overlay = self._generate_svg_overlay(scene_bytes, colors) if bgswap_ok else ''
        return scene_bytes, svg_overlay

    def _analyze_product_style(self, product_image_bytes: bytes, caption: str, colors: list[str], tone: str) -> str:
        """Gemini Director de Arte: analiza el producto y genera prompt de entorno premium para Imagen 3."""
        color_str = ', '.join(colors[:3]) if colors else 'warm neutrals'
        _FALLBACK = (
            f"Professional editorial product photography background. "
            f"Elegant real-world environment: wooden surface, marble, or lifestyle context. "
            f"Natural lighting, shallow depth of field, warm bokeh. Mood: {tone}. "
            f"NOT white background. NOT abstract. NOT 3D render. Absolutely NO text, NO logos."
        )
        try:
            client = _vertex_client()
            mime = _detect_mime(product_image_bytes)
            image_part = types.Part.from_bytes(data=product_image_bytes, mime_type=mime)
            prompt = (
                f"You are an Art Director for premium brand advertising.\n"
                f"Analyze this product image and generate a specific Imagen 3 prompt (max 100 words) "
                f"for the BACKGROUND ENVIRONMENT ONLY — where this product would look spectacular.\n"
                f"Brand context: {caption[:80]}. Color palette: {color_str}. Mood: {tone}.\n\n"
                f"Describe: surface/pedestal/setting, lighting style, atmosphere, complementary textures.\n"
                f"Do NOT mention the product itself — only the environment that showcases it.\n"
                f"End with: 'NOT abstract. NOT 3D render. Absolutely NO text, NO logos.'\n"
                f"Return ONLY the prompt text, no explanations."
            )
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL,
                contents=[image_part, prompt],
            )
            result = resp.text.strip().strip('"').strip("'")
            if result:
                logger.info(f"Art Director env prompt: {result[:100]}...")
                return result
        except Exception as e:
            logger.warning(f"Product style analysis failed (using fallback): {e}")
        return _FALLBACK

    def _bgswap_product(self, product_image_bytes: bytes, environment_prompt: str) -> tuple[bytes, bool]:
        """Imagen 3 BGSWAP: mantiene el producto exacto y reemplaza el fondo con el entorno del Director de Arte.
        Retorna (image_bytes, success). MASK_MODE_BACKGROUND para que Imagen 3 detecte el fondo automáticamente.
        """
        mime = _detect_mime(product_image_bytes)
        try:
            client = _vertex_client()
            resp = client.models.edit_image(
                model=settings.VERTEX_IMAGE_EDIT_MODEL,
                prompt=environment_prompt,
                reference_images=[
                    types.RawReferenceImage(
                        reference_image=types.Image(image_bytes=product_image_bytes, mime_type=mime),
                        reference_id=1,
                    ),
                    types.MaskReferenceImage(
                        reference_id=2,
                        config=types.MaskReferenceConfig(
                            mask_mode=types.MaskReferenceMode.MASK_MODE_BACKGROUND,
                        ),
                    ),
                ],
                config=types.EditImageConfig(
                    edit_mode=types.EditMode.EDIT_MODE_BGSWAP,
                    number_of_images=1,
                    aspect_ratio='1:1',
                ),
            )
            if resp.generated_images:
                logger.info("BGSWAP exitoso — producto sobre entorno premium")
                return resp.generated_images[0].image.image_bytes, True
            logger.warning("BGSWAP sin imágenes, usando foto original")
        except Exception as e:
            logger.warning(f"BGSWAP fallido (usando foto original): {e}")
        return product_image_bytes, False

    def _generate_svg_overlay(self, image_bytes: bytes, colors: list[str]) -> str:
        """Gemini Iluminador: genera SVG de sombra/luz para armonizar el producto con el nuevo fondo."""
        try:
            client = _vertex_client()
            mime = _detect_mime(image_bytes)
            image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime)
            primary = colors[0] if colors else '#ffffff'
            prompt = (
                f"Analyze this product advertising image. Generate an SVG transparent overlay (1080x1080) that:\n"
                f"1. Adds ONLY a gentle ambient light gradient matching the scene's dominant light direction\n"
                f"2. Applies a very soft color wash using {primary} at opacity 0.04-0.06 to harmonize\n\n"
                f"Rules:\n"
                f"- Use ONLY: <defs>, <rect>, <radialGradient>, <linearGradient> elements\n"
                f"- NO shadow ellipses, NO dark blobs, NO ellipse elements\n"
                f"- All fills must use opacity 0.10 or lower — barely visible, purely atmospheric\n"
                f"- No solid opaque fills. SVG root has no background-color.\n"
                f"- Return ONLY valid SVG starting with <svg and ending with </svg>. No markdown."
            )
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL,
                contents=[image_part, prompt],
            )
            raw = resp.text.strip()
            svg_match = re.search(r'<svg[\s\S]*?</svg>', raw, re.DOTALL)
            if svg_match:
                logger.info("SVG lighting overlay generado")
                return svg_match.group()
        except Exception as e:
            logger.warning(f"SVG overlay fallido (omitiendo): {e}")
        return ''

    _SCENE_FALLBACKS = [
        "warm coffee shop interior, wooden tables, ambient light, steam from cups, cozy atmosphere",
        "outdoor urban street scene, city architecture, natural daylight, people in motion blur",
        "lush tropical plants and leaves, natural textures, soft green light filtering through",
        "modern kitchen counter, fresh fruits and ingredients, warm morning light",
        "beach or coastal scene, sand and waves, golden hour light, natural calm",
        "vibrant local market, colorful products, warm candid atmosphere, authentic textures",
        "creative studio workspace with paints, fabrics and textures — no screens",
        "rooftop terrace at sunset, city skyline far in background, warm bokeh",
        "minimalist zen garden, stones, water, natural wood textures, soft diffused light",
        "artisanal workshop with hands working on materials, tools, warm focused light",
    ]

    def _analyze_brand_scene(self, caption: str, keywords: list[str], description: str, tone: str, colors: list[str]) -> str:
        """Gemini Art Director: analiza la marca y genera prompt de escena lifestyle creativa.
        Evita explícitamente escenas de oficina, laptops y escritorios."""
        color_str = ', '.join(colors[:3]) if colors else 'warm neutrals'
        kw_str = ', '.join(keywords[:4]) if keywords else ''
        brand_ctx = description[:120] if description else caption[:120]
        fallback_scene = random.choice(self._SCENE_FALLBACKS)
        _FALLBACK = (
            f"Real-world lifestyle photograph, {fallback_scene}. "
            f"Natural lighting, shallow depth of field. Color palette: {color_str}. Mood: {tone}. "
            f"NO laptops, NO computers, NO phones, NO desk, NO office, NO keyboard. "
            f"NO text, NO logos, NO UI elements. Square 1:1 format. Photorealistic."
        )
        try:
            client = _vertex_client()
            prompt = (
                f"You are an Art Director creating Instagram post backgrounds for brand advertising.\n"
                f"Brand: {brand_ctx}. Keywords: {kw_str}. Tone: {tone}. Colors: {color_str}.\n\n"
                f"Generate ONE Imagen 3 prompt (max 80 words) for a LIFESTYLE BACKGROUND PHOTO.\n"
                f"Rules:\n"
                f"- Choose a creative real-world scene that EVOKES the brand's values emotionally\n"
                f"- ABSOLUTELY NO offices, laptops, computers, desks, keyboards, or screens\n"
                f"- Think: where do this brand's CUSTOMERS live their lives? Coffee shops? Outdoors? Home?\n"
                f"- The scene should feel aspirational and authentic — real textures, natural light, depth\n"
                f"- Vary the scene type: nature, urban, food, home, travel, craft, market, garden, etc.\n\n"
                f"End with: 'Natural lighting. Photorealistic. NO laptops. NO computers. NO text. NO logos.'\n"
                f"Return ONLY the prompt text, no explanations."
            )
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL,
                contents=prompt,
            )
            result = resp.text.strip().strip('"').strip("'")
            if len(result) > 20:
                logger.info(f"Brand scene prompt: {result[:120]}...")
                return result
        except Exception as e:
            logger.warning(f"Brand scene analysis failed (fallback): {e}")
        return _FALLBACK

    def _generate_background(self, caption: str, colors: list[str], tone: str, keywords: list[str] = None, description: str = '', max_qc_retries: int = 2) -> bytes:
        scene_prompt = self._analyze_brand_scene(caption, keywords or [], description, tone, colors)
        # Hard constraints appended regardless of what Gemini generated
        prompt = (
            f"{scene_prompt} "
            f"DSLR camera quality, shallow depth of field, photorealistic. "
            f"NOT a CGI render. NOT a 3D illustration. NOT abstract shapes. NOT minimalist. "
            f"Absolutely NO text, NO letters, NO words, NO logos, NO UI elements anywhere."
        )
        logger.info(f"Background prompt (first 150): {prompt[:150]}")
        last_bytes = None
        total_attempts = max_qc_retries + 1
        for attempt in range(total_attempts):
            last_bytes = self._generate_with_retry(prompt)
            if self._validate_background(last_bytes):
                return last_bytes
            if attempt < max_qc_retries:
                logger.warning(f"Background QC failed (attempt {attempt + 1}/{total_attempts}), regenerando con nueva escena...")
                scene_prompt = self._analyze_brand_scene(caption, keywords or [], description, tone, colors)
                prompt = (
                    f"{scene_prompt} "
                    f"DSLR camera quality, shallow depth of field, photorealistic. "
                    f"NOT a CGI render. NOT abstract. "
                    f"Absolutely NO text, NO letters, NO words, NO logos, NO UI elements anywhere."
                )
        logger.warning("Background QC: reintentos agotados, usando última imagen generada")
        return last_bytes

    def _validate_background(self, image_bytes: bytes) -> bool:
        """Gemini reviews the generated image for forbidden elements. Returns True if ok."""
        try:
            client = _vertex_client()
            image_part = types.Part.from_bytes(data=image_bytes, mime_type='image/png')
            prompt = (
                "Analyze this image strictly. Reply ONLY with this JSON (no markdown):\n"
                "{\"has_text\": <bool>, \"is_abstract_3d\": <bool>, \"has_screen_content\": <bool>, \"ok\": <bool>}\n\n"
                "has_text: true if ANY readable letters, words, numbers or text appear ANYWHERE in the image — "
                "including text on signs, labels, books, packaging, walls, or any surface. "
                "Even partial words or blurry text count. Be very strict.\n"
                "is_abstract_3d: true if the image has floating 3D geometric shapes, abstract CGI objects, or surreal renders.\n"
                "has_screen_content: true if any computer monitor, laptop screen, phone screen, TV, or digital display "
                "shows visible content — including websites, text, images, graphics, UI elements, or any non-blank content. "
                "A screen must be completely BLACK or clearly turned off to not count. Be very strict.\n"
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
                if ok:
                    logger.info(f"Background QC OK: {data}")
                else:
                    flags = [k for k in ('has_text', 'is_abstract_3d', 'has_screen_content') if data.get(k)]
                    logger.warning(f"Background QC REJECTED: {', '.join(flags)} | full={data}")
                return ok
        except Exception as e:
            logger.warning(f"Background QC error (assuming ok): {e}")
        return True

    def _validate_final_image(self, image_bytes: bytes) -> bool:
        """QC del post renderizado final. Detecta problemas técnicos y calidad estética. Retorna True si es aceptable."""
        try:
            client = _vertex_client()
            image_part = types.Part.from_bytes(data=image_bytes, mime_type='image/png')
            prompt = (
                "Analyze this social media advertising post image strictly.\n"
                "NOTE: The image intentionally has a designed text overlay (headline, subtitle, CTA) — "
                "IGNORE that foreground text, it is part of the design.\n\n"
                "Reply ONLY with this JSON (no markdown):\n"
                "{\"has_background_text\": <bool>, \"has_shadow_artifacts\": <bool>, "
                "\"plain_white_background\": <bool>, \"ok\": <bool>}\n\n"
                "has_background_text: true if the BACKGROUND scene contains visible text, signs, or watermarks.\n"
                "has_shadow_artifacts: true if there are unnatural dark blobs or shadow ellipses that look "
                "like AI artifacts — especially a dark oval/circle in the center or bottom of the image.\n"
                "plain_white_background: true if the background behind the product is plain white, solid grey, "
                "or a simple flat color with no depth, texture, or environmental context. "
                "A professional advertising image must have an interesting background, not a plain studio backdrop.\n"
                "ok: true ONLY if has_background_text=false AND has_shadow_artifacts=false AND plain_white_background=false."
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
                    flags = [k for k in ('has_background_text', 'has_shadow_artifacts') if data.get(k)]
                    logger.warning(f"Final image QC rechazado: {', '.join(flags)}")
                return ok
        except Exception as e:
            logger.warning(f"Final image QC error (asumiendo ok): {e}")
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
                mime = _detect_mime(product_image_bytes)
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

    _TEMPLATES = [
        'instagram_post.html',         # lower third — texto abajo
        'instagram_post_center.html',  # panel centrado glassmorphism
        'instagram_post_top.html',     # upper third — texto arriba
    ]

    def _render_html_template(self, background_bytes: bytes, content: dict, colors: list[str], svg_overlay: str = '') -> bytes:
        """Inject background + content + optional SVG overlay into a randomly chosen HTML template → PNG."""
        template_name = random.choice(self._TEMPLATES)
        _TEMPLATE_PATH = os.path.normpath(os.path.join(
            os.path.dirname(__file__),
            '..', 'templates', 'content_pipeline', template_name,
        ))
        logger.info(f"Template seleccionado: {template_name}")
        with open(_TEMPLATE_PATH) as f:
            html = f.read()

        bg_mime = _detect_mime(background_bytes)
        bg_b64 = base64.b64encode(background_bytes).decode()
        primary = colors[0] if colors else '#e94560'

        safe_svg = _sanitize_svg(svg_overlay) if svg_overlay else ''
        svg_div = (
            f'<div style="position:absolute;inset:0;pointer-events:none;z-index:1;">{safe_svg}</div>'
            if safe_svg else ''
        )

        button_color = _pick_button_color(colors)
        html = html.replace('{{bg_data_url}}', f'data:{bg_mime};base64,{bg_b64}')
        html = html.replace('{{primary_color}}', primary)
        html = html.replace('{{button_color}}', button_color)
        html = html.replace('{{svg_overlay}}', svg_div)
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
