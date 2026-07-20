import json
import logging
import re
import google.genai as genai
from google.genai import types
from django.conf import settings
from core.brand_dna.models import BrandDNA
from core.shared.metrics_utils import track_external_api, record_tokens, vertex_labels
from core.shared.rate_limiter import call_with_429_retry
from core.content_pipeline.generators.text_generator import _is_sensitive_niche, _strip_accents

logger = logging.getLogger(__name__)

_FALLBACK_SCENES = [
    "Overhead flat lay of the product on a clean surface with soft natural light, slow push-in camera movement, no people, no text, no logos.",
    "Close-up detail shot of the product with shallow depth of field, gentle rotation, warm bokeh background, no text, no logos.",
    "Product displayed in a lifestyle setting with soft ambient light, no people, no text, no logos.",
    "Macro shot of texture and materials up close, soft directional light, shallow depth of field, no text, no logos.",
    "Hands arranging or presenting the product on a clean surface, natural light, no text, no logos.",
    "Wide clean studio shot of the product centered with soft shadow, minimal background, no text, no logos.",
]

# HALLAZGO 77 (hallazgos.txt, 2026-07-20): Gemini a veces describe la
# etiqueta/empaque del producto en la parte AFIRMATIVA de scene_prompts[0]
# (ej. "a candle with a label reading {business_name}"), y esa descripcion
# especifica le gana al negative_prompt generico ("no logos") que se manda
# a Veo/Imagen por separado — resultado: logo alucinado con el nombre real
# del negocio impreso, confirmado en un reel de MariBelas (sin ningun asset
# de marca real cargado). Pendiente: (1) prohibir explicitamente en esta
# instruccion que scene_prompts mencione el nombre del negocio, etiquetas o
# empaque con texto — no solo el sufijo "no text, no logos"; (2) agregar un
# backstop por regex tras recibir la respuesta de Gemini (mismo patron que
# caption_safety_qc/caption_safety_fix) que descarte una escena puntual si
# menciona business_name/label/logo/brand y la reemplace con
# _FALLBACK_SCENES. El negative_prompt de reel_generator.py (HALLAZGO 73)
# ya esta bien — el fix es aqui, en como se construye el prompt de Gemini.
_PROMPT = (
    "Eres un guionista de reels para redes sociales. Genera el guion completo para un "
    "reel de ~18 segundos (1 escena de video + 5 shots de imagen) sobre este negocio "
    "real llamado \"{business_name}\", basado en este post:\n\n"
    "CAPTION DEL POST: {caption}\n"
    "TONO: {tone}\n"
    "DESCRIPCION: {description}\n\n"
    "Genera:\n"
    "1. hook_text: 3-8 palabras, gancho de apertura potente (aparece 0-3s).\n"
    "2. highlight_word: UNA palabra dentro de hook_text a resaltar visualmente.\n"
    "3. tag_cta: 2-4 palabras, llamada a la accion de cierre (aparece en los ultimos 3s).\n"
    "4. narration_script: guion de voz en off en espanol, ~15-20 segundos hablados "
    "(unas 40-50 palabras), tono conversacional, sin leer literalmente el hook ni el CTA. "
    "Si mencionas el nombre del negocio, usa el nombre real exacto \"{business_name}\" tal "
    "cual — nunca escribas la palabra generica \"marca\" ni un placeholder entre corchetes "
    "como [Marca].\n"
    "5. scene_prompts: exactamente 6 prompts EN INGLES describiendo 6 escenas visuales "
    "relacionadas al negocio, con roles DISTINTOS por posicion:\n"
    "   - scene_prompts[0]: para un GENERADOR DE VIDEO. Debe ser un plano amplio o de "
    "ambiente con movimiento de camara (push-in, pan lento, rotacion suave). NO debe "
    "incluir manipulacion precisa de objetos con las manos (atornillar, cablear, cortar, "
    "ensamblar, escribir a mano en primer plano) porque el generador de video falla en "
    "coherencia fisica de manos con herramientas entre frames.\n"
    "   - scene_prompts[1] a scene_prompts[5]: para un GENERADOR DE IMAGEN FIJA, 5 shots "
    "cortos e independientes (~2 segundos cada uno en el reel final) — como una rafaga "
    "de tomas distintas en un comercial: detalles del producto/servicio, manos "
    "trabajando, texturas, ambiente, resultados. Los 5 deben mostrar variedad visual "
    "real entre si, no la misma composicion repetida. Aqui SI se prefiere el detalle de "
    "precision (manos, herramientas, texturas de cerca) porque cada uno es una imagen "
    "fija y no necesita coherencia fisica en el tiempo.\n"
    "   Las 6 evitan describir pantallas, laptops, monitores o interfaces con contenido — "
    "el generador alucina texto falso/ilegible cuando la escena implica una pantalla con "
    "informacion. Cada prompt debe terminar con: 'no text, no logos, no people speaking "
    "to camera.'\n"
    "6. music_mood: 1 frase corta en ingles describiendo el mood musical (ej. "
    "'upbeat corporate, optimistic, minimal percussion').\n\n"
    "REGLA DE SEGURIDAD: si el negocio pertenece a un nicho sensible, usa tono neutro-positivo, "
    "sin promesas absolutas ('garantizado', 'aseguramos', '100%').\n\n"
    "Responde UNICAMENTE con este JSON (sin markdown):\n"
    '{{"hook_text":"...","highlight_word":"...","tag_cta":"...",'
    '"narration_script":"...","scene_prompts":["...","...","...","...","...","..."],'
    '"music_mood":"..."}}'
)


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


class ReelScriptGenerator:
    def generate(self, post_data: dict, brand_dna: BrandDNA) -> dict:
        caption = post_data.get('caption', '')
        fallback = {
            'hook_text': ' '.join(caption.split()[:6]) or 'Descubre algo nuevo',
            'highlight_word': (caption.split()[0] if caption.split() else 'nuevo'),
            'tag_cta': 'Contáctanos hoy',
            'narration_script': caption[:200],
            'scene_prompts': list(_FALLBACK_SCENES),
            'music_mood': f"background music matching a {brand_dna.tone} mood, instrumental only",
        }
        try:
            client = _vertex_client()
            prompt = _PROMPT.format(
                business_name=brand_dna.business_name,
                caption=caption,
                tone=brand_dna.tone,
                description=brand_dna.description,
            )

            def _call():
                with track_external_api('gemini', operation='reel_script'):
                    return client.models.generate_content(
                        model=settings.VERTEX_TEXT_MODEL, contents=prompt,
                        config=types.GenerateContentConfig(labels=vertex_labels()),
                    )
            resp = call_with_429_retry(_call, settings.VERTEX_TEXT_MODEL)
            record_tokens(resp, operation='reel_script',
                          prompt_preview=prompt[:500],
                          response_preview=resp.text[:500] if resp.text else '')
            raw = resp.text.strip()
            raw = re.sub(r'^```(?:json)?\n?', '', raw)
            raw = re.sub(r'\n?```$', '', raw)
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return fallback
            data = json.loads(match.group())
            scene_prompts = data.get('scene_prompts') or []
            if len(scene_prompts) != 6:
                scene_prompts = list(_FALLBACK_SCENES)
            result = {
                'hook_text': str(data.get('hook_text', '')).strip() or fallback['hook_text'],
                'highlight_word': str(data.get('highlight_word', '')).strip() or fallback['highlight_word'],
                'tag_cta': str(data.get('tag_cta', '')).strip() or fallback['tag_cta'],
                'narration_script': str(data.get('narration_script', '')).strip() or fallback['narration_script'],
                'scene_prompts': scene_prompts,
                'music_mood': str(data.get('music_mood', '')).strip() or fallback['music_mood'],
            }
            if _is_sensitive_niche(brand_dna):
                text_to_check = _strip_accents(f"{result['hook_text']} {result['narration_script']}".lower())
                banned = ('garantizado', 'garantizamos', 'asegurar', 'aseguramos', '100%')
                if any(word in text_to_check for word in banned):
                    logger.warning("ReelScriptGenerator: guion rechazado por lenguaje prohibido en nicho sensible, usando fallback")
                    return fallback
            return result
        except Exception as e:
            logger.warning(f"ReelScriptGenerator fallback: {e}")
            return fallback
