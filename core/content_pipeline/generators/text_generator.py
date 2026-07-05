import json
import logging
import re
import unicodedata
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
    "REGLA DE SEGURIDAD (siempre aplica): si el negocio, keywords o audiencia sugieren un "
    "nicho sensible (niños, salud, medicina, finanzas, credito, temas legales), usa tono "
    "neutro-positivo y evita lenguaje retador o de urgencia con audiencias vulnerables. "
    "PROHIBIDO usar las palabras/frases: 'garantizado', 'garantizamos', 'asegurar', "
    "'aseguramos', 'asegurando', 'resultados 100% seguros', 'nunca falla', 'sin riesgo'. "
    "No afirmes resultados medicos, financieros, legales o educativos que no puedan "
    "verificarse (ej: no digas que un tratamiento 'asegura' o 'garantiza' un resultado).\n\n"
    "Responde UNICAMENTE con un array JSON de 7 objetos, sin markdown:\n"
    "[\n"
    '  {{"caption": "texto del post, maximo {avg_length} caracteres",\n'
    '   "hashtags": ["#tag1", "#tag2", "#tag3"],\n'
    '   "suggested_time": "HH:MM"}}\n'
    "]\n\n"
    "Los horarios sugeridos deben variar entre 09:00, 12:00, 17:00 y 19:00."
)


_SENSITIVE_KEYWORDS = (
    'nino', 'nina', 'infantil', 'bebe',
    'pediatr', 'salud', 'medic', 'clinica', 'doctor', 'hospital',
    'terapia', 'psicolog', 'nutrici', 'dental', 'dentista',
    'finanzas', 'financier', 'credito', 'prestamo', 'inversion',
    'seguro de vida', 'legal', 'abogad', 'juridic',
)


def _strip_accents(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')

_SAFETY_QC_PROMPT = (
    "Analiza este texto de marketing para redes sociales de forma estricta.\n"
    "Contexto de la marca — tono: {tone}, audiencia: {audience}\n\n"
    "Texto: \"{caption}\"\n\n"
    "Responde UNICAMENTE con este JSON (sin markdown):\n"
    '{{"has_absolute_promise": <bool>, "has_unverifiable_claim": <bool>, "ok": <bool>}}\n\n'
    "has_absolute_promise: true si usa palabras o frases como 'garantizado', 'garantizamos', "
    "'asegurar', 'aseguramos', 'asegurando', '100%', 'nunca falla', 'sin riesgo', o cualquier "
    "promesa absoluta de resultado.\n"
    "has_unverifiable_claim: true si afirma un resultado medico, financiero, legal o educativo "
    "especifico que no se puede verificar (ej: 'aseguramos un desarrollo optimo', "
    "'garantizamos tu recuperacion', 'triplica tus ingresos').\n"
    "ok: true SOLO si ambos son false."
)

_SAFETY_FIX_PROMPT = (
    "Reescribe el siguiente post de marketing para que NO haga promesas absolutas ni afirme "
    "resultados de salud, financieros, legales o educativos no verificables. Mantén el mismo "
    "mensaje central y longitud aproximada, pero en tono neutro-positivo, sin palabras como "
    "'garantizado', 'asegurar', 'aseguramos', '100%'.\n\n"
    "Post original: {caption}\n\n"
    "Tono de la marca: {tone}\n"
    "Responde UNICAMENTE con el texto corregido, sin comillas ni explicaciones."
)


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


def _is_sensitive_niche(brand_dna: BrandDNA) -> bool:
    haystack = _strip_accents(' '.join([
        brand_dna.description or '',
        brand_dna.audience or '',
        ' '.join(brand_dna.keywords or []),
    ]).lower())
    return any(kw in haystack for kw in _SENSITIVE_KEYWORDS)


class TextGenerator:
    def generate(self, brand_dna: BrandDNA, max_qc_retries: int = 2) -> list[dict]:
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
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if not match:
            raise ValueError(f"No se encontro un array JSON en la respuesta de Gemini: {raw[:200]}")
        posts = json.loads(match.group())[:7]

        if _is_sensitive_niche(brand_dna):
            logger.info(f"Nicho sensible detectado para '{brand_dna.business_name}' — auditando captions")
            for post in posts:
                post['caption'] = self._ensure_safe_caption(post['caption'], brand_dna, max_qc_retries)
        return posts

    def _ensure_safe_caption(self, caption: str, brand_dna: BrandDNA, max_qc_retries: int) -> str:
        for attempt in range(max_qc_retries + 1):
            if self._validate_caption_safety(caption, brand_dna.tone, brand_dna.audience):
                return caption
            if attempt < max_qc_retries:
                logger.warning(f"Caption safety QC falló (intento {attempt + 1}/{max_qc_retries + 1}), regenerando...")
                caption = self._regenerate_safe_caption(caption, brand_dna.tone)
        logger.warning(f"Safety QC: reintentos agotados para '{brand_dna.business_name}', se usa el ultimo caption generado")
        return caption

    def _validate_caption_safety(self, caption: str, tone: str, audience: str) -> bool:
        try:
            client = _vertex_client()
            prompt = _SAFETY_QC_PROMPT.format(caption=caption, tone=tone, audience=audience)
            with track_external_api('gemini', operation='caption_safety_qc'):
                resp = client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, contents=prompt)
            record_tokens(resp, operation='caption_safety_qc',
                          prompt_preview=prompt[:500],
                          response_preview=resp.text[:300] if resp.text else '')
            raw = resp.text.strip()
            raw = re.sub(r'^```(?:json)?\n?', '', raw)
            raw = re.sub(r'\n?```$', '', raw)
            match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
                ok = bool(data.get('ok', True))
                if not ok:
                    flags = [k for k in ('has_absolute_promise', 'has_unverifiable_claim') if data.get(k)]
                    logger.warning(f"Caption safety QC REJECTED: {', '.join(flags)} | caption={caption[:100]}")
                return ok
        except Exception as e:
            logger.warning(f"Caption safety QC error (asumiendo ok): {e}")
        return True

    def _regenerate_safe_caption(self, caption: str, tone: str) -> str:
        try:
            client = _vertex_client()
            prompt = _SAFETY_FIX_PROMPT.format(caption=caption, tone=tone)
            with track_external_api('gemini', operation='caption_safety_fix'):
                resp = client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, contents=prompt)
            record_tokens(resp, operation='caption_safety_fix',
                          response_preview=resp.text[:300] if resp.text else '')
            new_caption = resp.text.strip().strip('"').strip("'")
            raw = re.sub(r'^```.*?\n', '', new_caption, flags=re.DOTALL)
            raw = re.sub(r'\n?```$', '', raw)
            return raw.strip() or caption
        except Exception as e:
            logger.error(f"Error regenerando caption seguro: {e}")
            return caption
