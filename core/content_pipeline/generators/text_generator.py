import json
import logging
import re
import unicodedata
import google.genai as genai
from django.conf import settings
from core.brand_dna.models import BrandDNA
from core.shared.metrics_utils import track_external_api, record_tokens
from core.shared.rate_limiter import call_with_429_retry

logger = logging.getLogger(__name__)

# Pilares de contenido (H20): cada dia tiene un PROPOSITO ESTRATEGICO distinto en vez
# de 7 variaciones del mismo tema generico — la falta de esto era la razon real de que
# los posts "no generaran impacto" pese a ser tecnicamente correctos. Orden fijo,
# indice 0 = dia 1.
CONTENT_PILLARS = [
    {'day': 1, 'name': 'Producto', 'angle': 'Presenta que vendes o que servicio ofreces de forma directa y atractiva.'},
    {'day': 2, 'name': 'Diferenciador', 'angle': 'Explica que te hace unico frente a otras opciones del mismo mercado.'},
    {'day': 3, 'name': 'Antes y despues', 'angle': 'Cuenta una transformacion tipica: el problema que enfrenta tu audiencia antes de conocerte y como tu producto/servicio cambia esa situacion — ilustrativo, sin inventar datos verificables falsos.'},
    {'day': 4, 'name': 'Beneficio en profundidad', 'angle': 'Profundiza en UN beneficio o caracteristica especifica de tu producto/servicio (distinto al enfoque general del dia 1).'},
    {'day': 5, 'name': 'Educativo', 'angle': 'Comparte un tip o dato util relevante para tu audiencia, sin vender directamente.'},
    {'day': 6, 'name': 'CTA / Oferta', 'angle': 'Invita a la accion de forma directa — una oferta, promocion, o llamada clara a contactar.'},
    {'day': 7, 'name': 'Conexion emocional', 'angle': 'Describe como se siente tu cliente al usar tu producto/servicio — la emocion o sensacion que genera (tranquilidad, confianza, orgullo, alivio), sin afirmar resultados o datos verificables.'},
]

# El pilar "Antes y despues" se presta naturalmente a un formato de varias slides
# (problema, transicion, beneficio, CTA) — es el unico dia que usa carrusel.
CAROUSEL_DAY = 3

# El pilar "Producto" (dia 1, mayor exposicion de la semana) usa reel — Veo + Lyria 3 +
# TTS + overlay de texto (roadmap #7). tasks.py::_product_image_for_day baja esto a
# 'single' si el usuario subio una foto real para el dia 1 (ver reel_generator.py).
REEL_DAY = 1

_PROMPT = (
    "Eres un experto en marketing de contenidos. Genera exactamente 7 posts para redes sociales "
    "para la siguiente marca — cada uno con un PROPOSITO ESTRATEGICO DISTINTO (pilar de "
    "contenido), no 7 variaciones del mismo tema generico. Usa el tono y audiencia de la marca "
    "en todos.\n\n"
    "MARCA: {business_name}\n"
    "DESCRIPCION: {description}\n"
    "AUDIENCIA: {audience}\n"
    "TONO: {tone}\n"
    "KEYWORDS: {keywords}\n"
    "ESTILO DE POSTS PREVIOS: {posting_style}\n"
    "HASHTAGS COMUNES: {hashtags}\n\n"
    "PILARES DE CONTENIDO (uno por dia, EN ESTE ORDEN EXACTO — el post 1 de tu respuesta usa "
    "el pilar 1, el post 2 usa el pilar 2, etc.):\n"
    "{pillars_block}\n\n"
    "REGLA DE SEGURIDAD (siempre aplica): si el negocio, keywords o audiencia sugieren un "
    "nicho sensible (niños, salud, medicina, finanzas, credito, temas legales), usa tono "
    "neutro-positivo y evita lenguaje retador o de urgencia con audiencias vulnerables. "
    "PROHIBIDO usar las palabras/frases: 'garantizado', 'garantizamos', 'asegurar', "
    "'aseguramos', 'asegurando', 'resultados 100% seguros', 'nunca falla', 'sin riesgo'. "
    "No afirmes resultados medicos, financieros, legales o educativos que no puedan "
    "verificarse (ej: no digas que un tratamiento 'asegura' o 'garantiza' un resultado).\n\n"
    "Responde UNICAMENTE con un array JSON de 7 objetos EN EL MISMO ORDEN que los pilares de "
    "arriba, sin markdown:\n"
    "[\n"
    '  {{"caption": "texto del post, maximo {avg_length} caracteres",\n'
    '   "hashtags": ["#tag1", "#tag2", "#tag3"],\n'
    '   "suggested_time": "HH:MM"}}\n'
    "]\n\n"
    "Los horarios sugeridos deben variar entre 09:00, 12:00, 17:00 y 19:00."
)


def _pillars_block() -> str:
    return '\n'.join(
        f"Dia {p['day']} — {p['name']}: {p['angle']}" for p in CONTENT_PILLARS
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
    '{{"has_absolute_promise": <bool>, "has_unverifiable_claim": <bool>, "has_website_mention": <bool>, "ok": <bool>}}\n\n'
    "has_absolute_promise: true si usa palabras o frases como 'garantizado', 'garantizamos', "
    "'asegurar', 'aseguramos', 'asegurando', '100%', 'nunca falla', 'sin riesgo', o cualquier "
    "promesa absoluta de resultado.\n"
    "has_unverifiable_claim: true si afirma un resultado medico, financiero, legal o educativo "
    "especifico que no se puede verificar (ej: 'aseguramos un desarrollo optimo', "
    "'garantizamos tu recuperacion', 'triplica tus ingresos').\n"
    "has_website_mention: true si el texto invita a visitar un sitio web, pagina o URL "
    "(ej. 'visita nuestra web', 'entra a nuestro sitio', menciona www. o una URL).\n"
    "ok: true SOLO si has_absolute_promise y has_unverifiable_claim son false. "
    "Ignora has_website_mention para calcular ok — se evalua aparte en el codigo."
)

_SAFETY_FIX_PROMPT = (
    "Reescribe el siguiente post de marketing para que NO haga promesas absolutas ni afirme "
    "resultados de salud, financieros, legales o educativos no verificables, y que NO invite a "
    "visitar un sitio web, pagina o URL. Mantén el mismo mensaje central y longitud aproximada, "
    "pero en tono neutro-positivo, sin palabras como 'garantizado', 'asegurar', 'aseguramos', "
    "'100%', ni frases como 'visita nuestra web'.\n\n"
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
            pillars_block=_pillars_block(),
        )
        def _call():
            with track_external_api('gemini', operation='text_gen'):
                return client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, contents=prompt)
        resp = call_with_429_retry(_call, settings.VERTEX_TEXT_MODEL)
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

        # Pilar/formato por posicion — el orden de la respuesta debe coincidir con
        # CONTENT_PILLARS (se lo pedimos explicitamente en el prompt). Si Gemini
        # devuelve menos de 7 posts, los ultimos pilares simplemente no se usan.
        for i, post in enumerate(posts):
            pillar = CONTENT_PILLARS[i] if i < len(CONTENT_PILLARS) else None
            post['pillar'] = pillar['name'] if pillar else ''
            if pillar and pillar['day'] == REEL_DAY:
                post['format'] = 'reel'
            elif pillar and pillar['day'] == CAROUSEL_DAY:
                post['format'] = 'carousel'
            else:
                post['format'] = 'single'

        if _is_sensitive_niche(brand_dna) or not brand_dna.business_url:
            logger.info(f"Auditando captions para '{brand_dna.business_name}' (nicho sensible o sin business_url)")
            for post in posts:
                post['caption'] = self._ensure_safe_caption(post['caption'], brand_dna, max_qc_retries)
        return posts

    def _ensure_safe_caption(self, caption: str, brand_dna: BrandDNA, max_qc_retries: int) -> str:
        for attempt in range(max_qc_retries + 1):
            if self._validate_caption_safety(caption, brand_dna.tone, brand_dna.audience, brand_dna.business_url):
                return caption
            if attempt < max_qc_retries:
                logger.warning(f"Caption safety QC falló (intento {attempt + 1}/{max_qc_retries + 1}), regenerando...")
                caption = self._regenerate_safe_caption(caption, brand_dna.tone)
        logger.warning(f"Safety QC: reintentos agotados para '{brand_dna.business_name}', se usa el ultimo caption generado")
        return caption

    def _validate_caption_safety(self, caption: str, tone: str, audience: str, business_url: str) -> bool:
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
                if not business_url and data.get('has_website_mention'):
                    ok = False
                if not ok:
                    flags = [k for k in ('has_absolute_promise', 'has_unverifiable_claim', 'has_website_mention') if data.get(k)]
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
