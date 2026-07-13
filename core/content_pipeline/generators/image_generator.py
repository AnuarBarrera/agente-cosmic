import base64
import hashlib
import html as _html
import json
import logging
import os
import random
import re
import time
import defusedxml.ElementTree as ET

import google.genai as genai
from google.cloud import storage
from google.genai import types
from django.conf import settings
from playwright.sync_api import sync_playwright
from core.shared.metrics import GCS_OPERATIONS
from core.shared.metrics_utils import track_external_api, record_tokens, record_imagen_generation
from core.shared.rate_limiter import call_with_429_retry

from PIL import Image
import io

logger = logging.getLogger(__name__)

# Tipografías reales via Google Fonts (antes: font-family: Arial hardcodeado en los
# 3 templates HTML). Solo varía la fuente — el color de acento/botón sigue viniendo
# de la paleta real de la marca (primary_color/button_color), no de estos presets.
_FONT_PRESETS = [
    {'font_family': "'Poppins', sans-serif", 'font_import': 'Poppins:wght@400;600;700;900'},
    {'font_family': "'Playfair Display', serif", 'font_import': 'Playfair+Display:wght@400;600;700;900'},
    {'font_family': "'Space Grotesk', sans-serif", 'font_import': 'Space+Grotesk:wght@400;500;600;700'},
    {'font_family': "'Bebas Neue', sans-serif", 'font_import': 'Bebas+Neue'},
    {'font_family': "'DM Sans', sans-serif", 'font_import': 'DM+Sans:wght@400;500;700'},
]


def _choose_font_preset(seed: str) -> dict:
    """Elige una fuente de forma determinista a partir de `seed` (el job_id del
    calendario) en vez de puramente al azar — así las 7 imagenes de una misma
    semana usan la MISMA fuente (consistencia de marca, ver H35), incluso si el
    usuario regenera un solo post despues (nueva instancia de ImageGenerator,
    pero mismo seed => mismo preset)."""
    if not seed:
        return random.choice(_FONT_PRESETS)
    digest = hashlib.sha256(seed.encode()).hexdigest()
    idx = int(digest, 16) % len(_FONT_PRESETS)
    return _FONT_PRESETS[idx]


def _crop_to_square(image_bytes: bytes) -> bytes:
    """Crop image to 1:1 aspect ratio with top-biased center crop.
    For portrait images, keeps the upper 1/3 as center point instead of
    the geometric center — preserves heads, faces, and product tops."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        w, h = img.size
        if w == h:
            return image_bytes
        side = min(w, h)
        if w > h:
            left = (w - side) // 2
            box = (left, 0, left + side, side)
        else:
            top = max(0, (h - side) // 3)
            box = (0, top, side, top + side)
        cropped = img.crop(box)
        buf = io.BytesIO()
        fmt = 'JPEG' if img.format in (None, 'JPEG', 'MPO') else img.format
        cropped.save(buf, format=fmt, quality=90)
        return buf.getvalue()
    except Exception as e:
        logger.warning(f"_crop_to_square failed, using original: {e}")
        return image_bytes

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


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


class ImageGenerator:
    def __init__(self, bucket_name: str):
        self._bucket = bucket_name

    def generate(self, caption: str, colors: list[str], tone: str, filename: str, brand_name: str = '', keywords: list[str] = None, description: str = '', audience: str = '', product_image_bytes: bytes = None, max_qc_retries: int = 2) -> str:
        try:
            # job_id (sin el sufijo "-dayN") como seed de fuente — asi las 7 imagenes
            # de una semana comparten tipografia, incluso si se regenera un solo post.
            font_seed = filename.rsplit('-day', 1)[0] if '-day' in filename else filename
            image_bytes = self._layered_pipeline(caption, colors, tone, keywords or [], description, audience=audience, product_image_bytes=product_image_bytes, max_qc_retries=max_qc_retries, font_seed=font_seed)
            return self._upload_to_storage(image_bytes, filename)
        except Exception as e:
            logger.error(f"ImageGenerator error: {e}")
            return ''

    # ------------------------------------------------------------------
    # Layered pipeline
    # ------------------------------------------------------------------

    def _layered_pipeline(self, caption: str, colors: list[str], tone: str, keywords: list[str] = None, description: str = '', audience: str = '', product_image_bytes: bytes = None, max_qc_retries: int = 2, font_seed: str = '') -> bytes:
        if product_image_bytes:
            kw_str = ', '.join((keywords or [])[:3])
            brand_context = f"{description[:150]}. Tono: {tone}. Palabras clave: {kw_str}." if description else f"Tono: {tone}."
            background_bytes, svg_overlay = self._generate_product_scene(
                product_image_bytes, caption, colors, tone, max_qc_retries=max_qc_retries
            )
            content = self._generate_post_content(caption, product_image_bytes=product_image_bytes, brand_context=brand_context)
            result = self._render_html_template(background_bytes, content, colors, svg_overlay=svg_overlay, font_seed=font_seed)
            if max_qc_retries > 0 and svg_overlay and not self._validate_final_image(result):
                logger.warning("Final QC falló — reintentando sin SVG overlay")
                result = self._render_html_template(background_bytes, content, colors, svg_overlay='', font_seed=font_seed)
            return result
        else:
            background_bytes = self._generate_background(caption, colors, tone, keywords or [], description, audience=audience, max_qc_retries=max_qc_retries)
            kw_str = ', '.join((keywords or [])[:4])
            brand_ctx = f"{description[:150]}. Tono: {tone}. Palabras clave: {kw_str}." if description else f"Tono: {tone}."
            content = self._generate_post_content(caption, product_image_bytes=None, brand_context=brand_ctx)
            svg_overlay = ''
        return self._render_html_template(background_bytes, content, colors, svg_overlay=svg_overlay, font_seed=font_seed)

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
            with track_external_api('gemini', operation='image_product'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[image_part, prompt],
                )
            record_tokens(resp, operation='image_product',
                          prompt_preview=prompt[:500],
                          response_preview=resp.text[:500] if resp.text else '')
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

        def _call_edit_image():
            client = _vertex_client()
            with track_external_api('imagen3', operation='bgswap'):
                return client.models.edit_image(
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

        try:
            resp = call_with_429_retry(_call_edit_image, settings.VERTEX_IMAGE_EDIT_MODEL)
            if resp.generated_images:
                record_imagen_generation('bgswap')
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
            with track_external_api('gemini', operation='svg_overlay'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[image_part, prompt],
                )
            record_tokens(resp, operation='svg_overlay',
                          prompt_preview=prompt[:500],
                          response_preview=resp.text[:500] if resp.text else '')
            raw = resp.text.strip()
            svg_match = re.search(r'<svg[\s\S]*?</svg>', raw, re.DOTALL)
            if svg_match:
                logger.info("SVG lighting overlay generado")
                return svg_match.group()
        except Exception as e:
            logger.warning(f"SVG overlay fallido (omitiendo): {e}")
        return ''

    _SCENE_FALLBACKS = [
        "warm coffee shop interior, wooden tables, ambient light, steam from cups, cozy atmosphere — no signs, no text",
        "lush tropical plants and leaves, natural textures, soft green light filtering through — no text",
        "modern kitchen counter, fresh fruits and ingredients, warm morning light — no text, no labels",
        "beach or coastal scene, sand and waves, golden hour light, natural calm — no text",
        "creative studio workspace with paints, fabrics and textures, no screens, no signs",
        "rooftop terrace at sunset, city skyline far in background, warm bokeh — no text",
        "minimalist zen garden, stones, water, natural wood textures, soft diffused light — no text",
        "lush green forest path, dappled sunlight through leaves, peaceful atmosphere — no text",
        "abstract warm bokeh lights, soft golden tones, shallow depth of field — no objects, no text",
        "rustic wooden surface with natural props, warm candid atmosphere — no text, no signs",
    ]

    _PRODUCT_FALLBACKS = [
        "colorful ice cream scoops and popsicles arranged on a vibrant pastel surface, overhead flat lay, studio lighting — no people, no text",
        "fresh colorful food products arranged artfully on marble, top-down product photography — no people, no text",
        "festive colorful balloons and ribbons on pastel background, party decoration flat lay — no people, no text",
        "close-up of colorful frozen treats with vibrant sprinkles, shallow depth of field — no people, no text",
        "abstract colorful bokeh background, festive warm tones, soft light — no people, no objects, no text",
        "vibrant tropical fruits and sweet treats scattered on white background, product photography — no people, no text",
        "artful arrangement of colorful sweets on wooden board, warm bokeh background — no people, no text",
        "colorful gradient pastel background with soft light bokeh, festive feel — no people, no text",
        "product photography of colorful treats on white marble, minimal props, studio light — no people, no text",
    ]

    _MINOR_KEYWORDS = frozenset([
        'niños', 'ninos', 'niño', 'nino', 'kids', 'kid', 'infantil', 'menores', 'menor',
        'children', 'child', 'bebés', 'bebes', 'bebé', 'bebe', 'escolares', 'escolar',
        'escuelas', 'escuela', 'colegio', 'colegios', 'infantes', 'infante', 'infant',
        'infancia', 'preescolar', 'jardín de niños', 'jardin de ninos',
    ])

    @classmethod
    def _targets_minors(cls, audience: str, description: str = '') -> bool:
        text = f"{audience or ''} {description or ''}".lower()
        return any(k in text for k in cls._MINOR_KEYWORDS)

    def _analyze_brand_scene(self, caption: str, keywords: list[str], description: str, tone: str, colors: list[str], audience: str = '') -> tuple[str, bool]:
        """Gemini Art Director: decide el modo (product/lifestyle) y genera el prompt para Imagen 3.
        Retorna (scene_prompt, product_mode). Gemini evalúa si la escena natural de la marca
        activaría el content safety de Imagen (menores, eventos infantiles) y elige el modo."""
        color_str = ', '.join(colors[:3]) if colors else 'warm neutrals'
        kw_str = ', '.join(keywords[:4]) if keywords else ''
        brand_ctx = description[:180] if description else caption[:180]
        # Detección rápida por keywords como safety net si Gemini falla
        keyword_product_mode = self._targets_minors(audience, description)

        _FALLBACK_PROMPT = (
            f"Real-world {'product photography' if keyword_product_mode else 'lifestyle photograph'} inspired by: {brand_ctx[:100]}. "
            f"Natural lighting, shallow depth of field. Prominently feature the brand color palette ({color_str}) "
            f"in props, backdrop, or accent elements — the background should visibly reflect these colors, not "
            f"look like a generic neutral stock photo. Mood: {tone}. "
            f"{'NO people, NO children, NO hands. Focus on the product itself.' if keyword_product_mode else 'Authentic setting, real textures, professional photography style.'} "
            f"NO laptops, NO computers, NO phones, NO desk, NO office, NO keyboard. "
            f"NO text, NO logos, NO UI elements. Square 1:1 format. Photorealistic."
        )
        try:
            client = _vertex_client()
            gemini_prompt = (
                f"You are an Art Director creating Instagram post backgrounds for brand advertising.\n"
                f"Brand: {brand_ctx}. Audience: {(audience or '')[:120]}. Keywords: {kw_str}. Tone: {tone}. Colors: {color_str}.\n\n"
                f"STEP 1 — Imagen 3 content safety check:\n"
                f"Imagen 3 BLOCKS any scene that includes or implies: children, minors, school events with kids,\n"
                f"birthday parties with children, or any person under 18 years old.\n"
                f"Would a natural lifestyle photo for this brand risk triggering that restriction?\n\n"
                f"STEP 2 — Generate a background prompt (max 80 words):\n"
                f"- If risk=YES → mode=\"product\": focus on the product/food/objects only, NO people of any age, NO hands.\n"
                f"  Think: overhead flat lay, artful food arrangement, colorful props matching brand palette.\n"
                f"- If risk=NO  → mode=\"lifestyle\": real-world scene reflecting the brand's world and customers.\n"
                f"  Think: service environment, lifestyle moment, nature matching brand values. NO offices or screens.\n\n"
                f"Both modes: real textures, natural light, depth. Make the brand colors ({color_str}) VISUALLY "
                f"PROMINENT in the scene (props, walls, fabrics, accents) — avoid plain neutral/beige backgrounds "
                f"that could belong to any brand; the color palette should be clearly recognizable at a glance. "
                f"End with: 'Natural lighting. Photorealistic. NO text. NO logos.' (add 'NO people.' if mode=product)\n\n"
                f"Respond ONLY with this JSON (no markdown, no explanation):\n"
                f'{{\"mode\": \"product\", \"prompt\": \"...\"}} or {{\"mode\": \"lifestyle\", \"prompt\": \"...\"}}'
            )
            with track_external_api('gemini', operation='image_bg'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=gemini_prompt,
                )
            record_tokens(resp, operation='image_bg',
                          prompt_preview=gemini_prompt[:500],
                          response_preview=resp.text[:500] if resp.text else '')
            raw = resp.text.strip()
            match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
                mode = data.get('mode', '')
                scene_prompt = (data.get('prompt') or '').strip().strip('"').strip("'")
                if mode in ('product', 'lifestyle') and len(scene_prompt) > 20:
                    product_mode = (mode == 'product')
                    logger.info(f"Brand scene prompt (mode={mode}): {scene_prompt[:120]}...")
                    return scene_prompt, product_mode
            # Gemini respondió texto libre sin JSON válido — intentar extraer el prompt
            if len(raw) > 20:
                logger.warning("image_bg: Gemini no devolvió JSON válido, usando keyword fallback para modo")
                return raw[:400], keyword_product_mode
        except Exception as e:
            logger.warning(f"Brand scene analysis failed (fallback): {e}")
        return _FALLBACK_PROMPT, keyword_product_mode

    _SAFE_CONSTRAINTS = (
        " DSLR camera quality, shallow depth of field, photorealistic. "
        "NOT a CGI render. NOT a 3D illustration. NOT abstract shapes. NOT minimalist. "
        "Absolutely NO text, NO letters, NO words, NO logos, NO UI elements anywhere."
    )

    def _generate_background(self, caption: str, colors: list[str], tone: str, keywords: list[str] = None, description: str = '', audience: str = '', max_qc_retries: int = 2) -> bytes:
        scene_prompt, product_mode = self._analyze_brand_scene(caption, keywords or [], description, tone, colors, audience=audience)
        fallbacks = self._PRODUCT_FALLBACKS if product_mode else self._SCENE_FALLBACKS
        prompt = scene_prompt + self._SAFE_CONSTRAINTS
        logger.info(f"Background prompt (mode={'product' if product_mode else 'lifestyle'}, first 150): {prompt[:150]}")
        last_bytes = None
        total_attempts = max_qc_retries + 1
        for attempt in range(total_attempts):
            try:
                last_bytes = self._generate_with_retry(prompt)
            except ValueError:
                fallback_scene = fallbacks[attempt % len(fallbacks)]
                prompt = fallback_scene + self._SAFE_CONSTRAINTS
                logger.warning(f"Imagen rechazó prompt (intento {attempt + 1}), usando {'producto' if product_mode else 'escena neutral'}: {fallback_scene[:60]}...")
                last_bytes = self._generate_with_retry(prompt)
            if self._validate_background(last_bytes):
                return last_bytes
            if attempt < max_qc_retries:
                logger.warning(f"Background QC failed (attempt {attempt + 1}/{total_attempts}), regenerando con nueva escena...")
                scene_prompt, product_mode = self._analyze_brand_scene(caption, keywords or [], description, tone, colors, audience=audience)
                fallbacks = self._PRODUCT_FALLBACKS if product_mode else self._SCENE_FALLBACKS
                prompt = scene_prompt + self._SAFE_CONSTRAINTS
        logger.warning("Background QC: reintentos agotados, usando última imagen generada")
        return last_bytes

    def _validate_background(self, image_bytes: bytes) -> bool:
        """Gemini reviews the generated image for forbidden elements. Returns True if ok."""
        try:
            client = _vertex_client()
            image_part = types.Part.from_bytes(data=image_bytes, mime_type='image/png')
            prompt = (
                "Analyze this image strictly. Reply ONLY with this JSON (no markdown):\n"
                "{\"has_text\": <bool>, \"is_abstract_3d\": <bool>, \"has_screen_content\": <bool>, "
                "\"has_malformed_object\": <bool>, \"has_unrealistic_grounding\": <bool>, \"ok\": <bool>}\n\n"
                "has_text: true if ANY readable letters, words, numbers or text appear ANYWHERE in the image — "
                "including text on signs, labels, books, packaging, walls, or any surface. "
                "Even partial words or blurry text count. Be very strict.\n"
                "is_abstract_3d: true if the image has floating 3D geometric shapes, abstract CGI objects, or surreal renders.\n"
                "has_screen_content: true if any computer monitor, laptop screen, phone screen, TV, or digital display "
                "shows visible content — including websites, text, images, graphics, UI elements, or any non-blank content. "
                "A screen must be completely BLACK or clearly turned off to not count. Be very strict.\n"
                "has_malformed_object: true if any object, tool, instrument, hand, or mechanical item is anatomically or "
                "physically impossible or distorted — wrong number of parts, parts connected incorrectly, missing pieces "
                "a real version of the object would have, or a structurally implausible shape. Examine objects with "
                "multiple connected parts (tools, instruments, hands, machinery) closely. Only flag clear, obvious cases.\n"
                "has_unrealistic_grounding: true if the main subject (person, animal, or product) appears to float, "
                "hover, or is otherwise disconnected from the surface/floor/background it should be resting or "
                "standing on — no visible contact point, no matching contact shadow directly beneath it, wrong "
                "scale or perspective versus the background, or a dynamic mid-air pose (jumping, running) composited "
                "onto a background that implies the subject is stationary. This commonly happens when a subject's "
                "pose doesn't match its new background. Only flag clear, obvious cases where it looks physically wrong.\n"
                "ok: true ONLY if has_text=false AND is_abstract_3d=false AND has_screen_content=false "
                "AND has_malformed_object=false AND has_unrealistic_grounding=false."
            )
            with track_external_api('gemini', operation='image_qc'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[image_part, prompt],
                )
            record_tokens(resp, operation='image_qc',
                          prompt_preview=prompt[:500],
                          response_preview=resp.text[:500] if resp.text else '')
            raw = resp.text.strip()
            match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
                ok = bool(data.get('ok', True))
                if ok:
                    logger.info(f"Background QC OK: {data}")
                else:
                    flags = [k for k in ('has_text', 'is_abstract_3d', 'has_screen_content', 'has_malformed_object', 'has_unrealistic_grounding') if data.get(k)]
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
            with track_external_api('gemini', operation='image_qc'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[image_part, prompt],
                )
            record_tokens(resp, operation='image_qc',
                          prompt_preview=prompt[:500],
                          response_preview=resp.text[:500] if resp.text else '')
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
                ctx_line = f"ADN de marca: {brand_context}\n" if brand_context else ""
                prompt = (
                    f"{ctx_line}"
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
            with track_external_api('gemini', operation='post_content'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=(
                            "Eres 'Cosmic', Director Creativo de Agente Cosmic. "
                            "Generas contenido de marketing para redes sociales. "
                            "Español impecable. Cero errores ortográficos. Nunca inventes palabras. "
                            "Frases para imagen: cortas, impactantes, máximo 5 palabras. "
                            "Regla de seguridad (siempre aplica): si la marca pertenece a un nicho "
                            "sensible (niños, salud, medicina, finanzas, crédito, temas legales), usa "
                            "tono neutro-positivo, evita promesas absolutas y evita lenguaje retador "
                            "o de urgencia con audiencias vulnerables. PROHIBIDO usar las palabras/frases: "
                            "'garantizado', 'garantizamos', 'asegurar', 'aseguramos', 'asegurando', "
                            "'resultados 100% seguros', 'nunca falla', 'sin riesgo'."
                        ),
                    ),
                )
            record_tokens(resp, operation='post_content',
                          prompt_preview=prompt[:500],
                          response_preview=resp.text[:500] if resp.text else '')
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

    _TEMPLATE_ZONE_MAP = {
        'bottom': 'instagram_post.html',
        'top': 'instagram_post_top.html',
        'center': 'instagram_post_center.html',
    }

    def _choose_template_for_image(self, background_bytes: bytes) -> str:
        """Gemini analiza la imagen final (ya recortada al cuadrado) y elige la plantilla
        que menos interfiere con el sujeto principal, en vez de una elección aleatoria."""
        try:
            client = _vertex_client()
            image_part = types.Part.from_bytes(data=background_bytes, mime_type='image/png')
            prompt = (
                "Esta imagen es el fondo de un post de Instagram. Se superpondrá texto "
                "(titulo, subtitulo, boton) en una franja de la imagen.\n"
                "Responde UNICAMENTE con este JSON (sin markdown):\n"
                '{"safe_zone": "top" | "bottom" | "center"}\n\n'
                "safe_zone es la zona con MENOS elementos visuales importantes (sujeto "
                "principal, producto, rostros, logos, detalles) para superponer texto:\n"
                "- 'bottom': el tercio inferior esta vacio o es fondo simple.\n"
                "- 'top': el tercio superior esta vacio o es fondo simple.\n"
                "- 'center': ningun tercio esta claramente vacio, pero hay espacio para un "
                "panel central semi-transparente sin tapar el sujeto por completo."
            )
            with track_external_api('gemini', operation='template_select'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[image_part, prompt],
                )
            record_tokens(resp, operation='template_select',
                          response_preview=resp.text[:200] if resp.text else '')
            raw = resp.text.strip()
            match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
                zone = data.get('safe_zone', '')
                if zone in self._TEMPLATE_ZONE_MAP:
                    logger.info(f"Zona segura detectada: {zone} -> {self._TEMPLATE_ZONE_MAP[zone]}")
                    return self._TEMPLATE_ZONE_MAP[zone]
        except Exception as e:
            logger.warning(f"Selección de plantilla por IA falló, usando aleatorio: {e}")
        return random.choice(self._TEMPLATES)

    def _render_html_template(self, background_bytes: bytes, content: dict, colors: list[str], svg_overlay: str = '', font_seed: str = '') -> bytes:
        """Inject background + content + optional SVG overlay into an HTML template chosen by
        Gemini based on where the image has visual space for text → PNG."""
        background_bytes = _crop_to_square(background_bytes)
        template_name = self._choose_template_for_image(background_bytes)
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
        font_preset = _choose_font_preset(font_seed)
        html = html.replace('{{bg_data_url}}', f'data:{bg_mime};base64,{bg_b64}')
        html = html.replace('{{primary_color}}', primary)
        html = html.replace('{{button_color}}', button_color)
        html = html.replace('{{font_family}}', font_preset['font_family'])
        html = html.replace('{{font_import}}', font_preset['font_import'])
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
            page.set_content(html, wait_until='load')
            page.evaluate('document.fonts.ready')
            png_bytes = page.screenshot(full_page=False)
            browser.close()

        return png_bytes

    def _generate_with_retry(self, prompt: str) -> bytes:
        return call_with_429_retry(lambda: self._generate_with_vertex(prompt), settings.VERTEX_IMAGE_MODEL)

    def _generate_with_vertex(self, prompt: str) -> bytes:
        client = _vertex_client()
        model = settings.VERTEX_IMAGE_MODEL
        if 'imagen' in model:
            with track_external_api('imagen3', operation='image_generate'):
                resp = client.models.generate_images(
                    model=model,
                    prompt=prompt,
                    config=types.GenerateImagesConfig(
                        number_of_images=1,
                        aspect_ratio='1:1',
                    ),
                )
            if resp.generated_images:
                record_imagen_generation('generate')
                return resp.generated_images[0].image.image_bytes
            raise ValueError("No image returned by Imagen")
        with track_external_api('gemini'):
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=['IMAGE', 'TEXT']
                ),
            )
        record_tokens(resp)
        for part in resp.candidates[0].content.parts:
            if part.inline_data:
                return part.inline_data.data
        raise ValueError("No image returned by Vertex AI")

    def _upload_to_storage(self, image_bytes: bytes, filename: str) -> str:
        with track_external_api('gcs'):
            client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
            bucket = client.bucket(self._bucket)
            blob = bucket.blob(f'posts/{filename}.png')
            blob.upload_from_string(image_bytes, content_type='image/png')
        GCS_OPERATIONS.labels(operation='upload').inc()
        # Cache-busting: el mismo filename se reutiliza en regeneraciones (post individual,
        # semana siguiente), y sin esto el navegador puede mostrar la imagen vieja en cache.
        return f'{blob.public_url}?v={int(time.time())}'
