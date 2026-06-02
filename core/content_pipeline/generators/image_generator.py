import io
import json
import logging
import random
import re
import textwrap
import time

import google.genai as genai
from google.cloud import storage
from google.genai import types
from django.conf import settings
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAYS = [10, 20, 40]


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


def _darken_color(hex_color: str) -> tuple:
    """Convert #RRGGBB to a darkened (R, G, B) suitable as text backing."""
    h = hex_color.lstrip('#')
    try:
        r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
        return (max(10, r // 3), max(10, g // 3), max(10, b // 3))
    except (ValueError, IndexError):
        return (15, 15, 40)


class ImageGenerator:
    def __init__(self, bucket_name: str):
        self._bucket = bucket_name

    def generate(self, caption: str, colors: list[str], tone: str, filename: str) -> str:
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
        from core.content_pipeline.generators.layer_composer import composite_layers

        background_bytes = self._generate_background(caption, colors, tone)

        # Combined Gemini call: placement coords + AI-crafted headline
        analysis = self._analyze_background(background_bytes, caption)
        headline = analysis['headline']

        text_asset_bytes = self._generate_text_asset(headline, colors)

        rotation = random.uniform(-5.0, 5.0) if random.random() < 0.4 else 0.0

        if text_asset_bytes:
            return composite_layers(
                background_bytes=background_bytes,
                text_asset_bytes=text_asset_bytes,
                x=analysis['x'],
                y=analysis['y'],
                width=analysis['width'],
                rotation_deg=rotation,
            )

        logger.warning("Text asset failed — returning background only")
        return background_bytes

    def _generate_background(self, caption: str, colors: list[str], tone: str) -> bytes:
        color_str = ', '.join(colors[:3]) if colors else 'modern vibrant colors'
        prompt = (
            f"Clean professional photograph or illustration. "
            f"Subject related to: {caption[:80]}. "
            f"Dominant colors: {color_str}. Mood: {tone}. "
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

    def _analyze_background(self, background_bytes: bytes, caption: str) -> dict:
        """Ask Gemini for text placement coords AND a punchy 3-5 word headline."""
        _FALLBACK = {
            'x': 0.05, 'y': 0.62, 'width': 0.9,
            'headline': self._extract_headline(caption),
        }
        try:
            client = _vertex_client()
            image_part = types.Part.from_bytes(data=background_bytes, mime_type='image/png')
            prompt = (
                f"Caption: \"{caption[:200]}\"\n\n"
                "1. Find the best spot in this image for a text bar overlay "
                "(prefer lower third, flat or darker areas, avoid faces/focal points).\n"
                "2. Write a SHORT punchy headline (3-5 words) that captures the key message "
                "of the caption — must be grammatically complete, no mid-sentence cuts.\n\n"
                "Reply with ONLY this JSON (no markdown):\n"
                "{\"x\": <left 0.0-1.0>, \"y\": <top 0.0-1.0>, "
                "\"width\": <0.6-0.95>, \"headline\": \"<3-5 words>\"}"
            )
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL,
                contents=[image_part, prompt],
            )
            raw = resp.text.strip()
            match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
                headline = str(data.get('headline', '')).strip()
                return {
                    'x': max(0.0, min(0.9, float(data.get('x', 0.05)))),
                    'y': max(0.0, min(0.85, float(data.get('y', 0.62)))),
                    'width': max(0.6, min(0.95, float(data.get('width', 0.9)))),
                    'headline': headline if headline else self._extract_headline(caption),
                }
        except Exception as e:
            logger.warning(f"Background analysis failed, using fallback: {e}")
        return _FALLBACK

    def _generate_text_asset(self, headline: str, colors: list[str], max_retries: int = 2) -> bytes | None:
        """Render headline on exact magenta #FF00FF using PIL with dark backing rectangle.

        PIL guarantees exact (255,0,255) so remove_chroma_key works perfectly.
        The dark backing rectangle survives chroma key and ensures text legibility.
        max_retries kept for caller compatibility (no API call needed).
        """
        try:
            W, H = 1024, 512
            _DEJAVU = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
            text = headline.upper()
            padding = 60
            max_w = W - 2 * padding

            dummy_img = Image.new('RGB', (1, 1))
            dummy_draw = ImageDraw.Draw(dummy_img)

            # Find font size: try single line, then 2-line split
            font = None
            lines = [text]
            chosen_size = 80

            for size in range(110, 39, -10):
                try:
                    f = ImageFont.truetype(_DEJAVU, size=size)
                except (OSError, IOError):
                    f = ImageFont.load_default(size=size)
                single_w = dummy_draw.textbbox((0, 0), text, font=f)[2]
                if single_w <= max_w:
                    font, lines, chosen_size = f, [text], size
                    break
                words = text.split()
                mid = max(1, len(words) // 2)
                l1, l2 = ' '.join(words[:mid]), ' '.join(words[mid:])
                if l1 and l2:
                    w1 = dummy_draw.textbbox((0, 0), l1, font=f)[2]
                    w2 = dummy_draw.textbbox((0, 0), l2, font=f)[2]
                    if max(w1, w2) <= max_w:
                        font, lines, chosen_size = f, [l1, l2], size
                        break

            if font is None:
                try:
                    font = ImageFont.truetype(_DEJAVU, size=40)
                except (OSError, IOError):
                    font = ImageFont.load_default()
                lines = [text[:28]]
                chosen_size = 40

            img = Image.new('RGB', (W, H), (255, 0, 255))  # magenta — removed by chroma key
            draw = ImageDraw.Draw(img)

            line_spacing = 18
            bboxes = [draw.textbbox((0, 0), line, font=font) for line in lines]
            total_h = sum(b[3] - b[1] for b in bboxes) + line_spacing * (len(lines) - 1)
            y_start = (H - total_h) // 2

            # Dark backing rectangle — NOT magenta so it survives chroma key
            backing = _darken_color(colors[0]) if colors else (15, 15, 40)
            rect_pad_x, rect_pad_y = padding - 30, 24
            draw.rectangle(
                [rect_pad_x, y_start - rect_pad_y,
                 W - rect_pad_x, y_start + total_h + rect_pad_y],
                fill=backing,
            )

            y = y_start
            shadow_off = max(2, chosen_size // 30)
            for line, bb in zip(lines, bboxes):
                lw = bb[2] - bb[0]
                lh = bb[3] - bb[1]
                x = (W - lw) // 2
                draw.text((x + shadow_off, y + shadow_off), line, font=font, fill=(0, 0, 0))
                draw.text((x, y), line, font=font, fill=(255, 255, 255))
                y += lh + line_spacing

            out = io.BytesIO()
            img.save(out, format='PNG')
            return out.getvalue()
        except Exception as e:
            logger.error(f"PIL text asset generation failed: {e}")
            return None

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
