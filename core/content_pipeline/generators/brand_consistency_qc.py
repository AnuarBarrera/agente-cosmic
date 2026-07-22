import json
import logging
import re
import google.genai as genai
from google.genai import types
from django.conf import settings
from core.brand_dna.models import BrandDNA
from core.shared.metrics_utils import track_external_api, record_tokens, vertex_labels

logger = logging.getLogger(__name__)

_AUDIT_PROMPT = (
    "Eres un auditor de identidad de marca. Evalua si estos textos generados "
    "por IA son consistentes con la marca, o si accidentalmente cambiaron "
    "terminologia o tono de forma que perjudica su posicionamiento.\n\n"
    "MARCA: {business_name}\n"
    "DESCRIPCION (fuente de verdad de terminologia/posicionamiento): {description}\n"
    "TONO: {tone}\n"
    "KEYWORDS: {keywords}\n\n"
    "TEXTOS A EVALUAR:\n{fields_block}\n\n"
    "Marca un problema en un campo SOLO si:\n"
    "- Reemplaza un termino especifico de la marca (presente en la descripcion "
    "o keywords) por un sinonimo generico con connotacion distinta o inferior "
    "(ej: \"upcycling\" -> \"materiales reutilizados\" suena a segunda mano, "
    "cuando upcycling es un termino de moda sostenible premium).\n"
    "- El tono no coincide con {tone} (ej: mezcla registros, usa un acento o "
    "variante regional inesperada).\n"
    "NO marques problemas de gusto o estilo menores — solo casos donde el "
    "cambio daña activamente el posicionamiento de la marca.\n\n"
    "Responde UNICAMENTE con este JSON (sin markdown), una entrada por cada "
    "campo listado arriba:\n"
    '{{"nombre_campo": {{"ok": <bool>, "reason": "..."}}, ...}}'
)

_REWRITE_PROMPT = (
    "Reescribe el siguiente texto para corregir este problema de consistencia "
    "de marca: {reason}\n\n"
    "Texto original: \"{text}\"\n"
    "Terminologia/posicionamiento de referencia (descripcion de la marca): {description}\n"
    "Tono de la marca: {tone}\n\n"
    "Manten el mismo mensaje central y longitud aproximada. Responde "
    "UNICAMENTE con el texto corregido, sin comillas ni explicaciones."
)


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


def audit_brand_consistency(fields: dict, brand_dna: BrandDNA) -> dict:
    """Audita todos los campos en una sola llamada a Gemini. Devuelve
    {nombre_campo: reason} solo para los campos con problema. Fail-open:
    cualquier error devuelve {} (no bloquea el pipeline)."""
    if not fields:
        return {}
    try:
        client = _vertex_client()
        fields_block = '\n'.join(f'{name}: "{text}"' for name, text in fields.items())
        prompt = _AUDIT_PROMPT.format(
            business_name=brand_dna.business_name,
            description=brand_dna.description,
            tone=brand_dna.tone,
            keywords=', '.join(brand_dna.keywords or []),
            fields_block=fields_block,
        )
        with track_external_api('gemini', operation='brand_consistency_audit'):
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL, contents=prompt,
                config=types.GenerateContentConfig(labels=vertex_labels()),
            )
        record_tokens(resp, operation='brand_consistency_audit',
                      prompt_preview=prompt[:500],
                      response_preview=resp.text[:500] if resp.text else '')
        raw = resp.text.strip()
        raw = re.sub(r'^```(?:json)?\n?', '', raw)
        raw = re.sub(r'\n?```$', '', raw)
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            return {}
        data = json.loads(match.group())
        issues = {}
        for name in fields:
            entry = data.get(name)
            if isinstance(entry, dict) and not entry.get('ok', True):
                issues[name] = str(entry.get('reason', '')).strip() or 'Inconsistente con la identidad de marca'
        return issues
    except Exception as e:
        logger.warning(f"audit_brand_consistency fallo (fail-open, se asume ok): {e}")
        return {}


def rewrite_for_brand_consistency(field_name: str, text: str, reason: str, brand_dna: BrandDNA) -> str:
    """Reescribe un campo puntual para corregir 'reason'. Fail-open: si la
    llamada falla, devuelve el texto original sin cambios."""
    try:
        client = _vertex_client()
        prompt = _REWRITE_PROMPT.format(
            reason=reason,
            text=text,
            description=brand_dna.description,
            tone=brand_dna.tone,
        )
        with track_external_api('gemini', operation='brand_consistency_fix'):
            resp = client.models.generate_content(
                model=settings.VERTEX_TEXT_MODEL, contents=prompt,
                config=types.GenerateContentConfig(labels=vertex_labels()),
            )
        record_tokens(resp, operation='brand_consistency_fix',
                      response_preview=resp.text[:300] if resp.text else '')
        new_text = resp.text.strip().strip('"').strip("'")
        raw = re.sub(r'^```.*?\n', '', new_text, flags=re.DOTALL)
        raw = re.sub(r'\n?```$', '', raw)
        return raw.strip() or text
    except Exception as e:
        logger.error(f"rewrite_for_brand_consistency fallo para campo '{field_name}': {e}")
        return text
