"""Build a conservative, traceable source of truth from user-provided text."""

from __future__ import annotations

import re
import unicodedata


_COMMERCIAL = re.compile(
    r'\b(promoci[oó]n\w*|oferta\w*|descuento\w*|precio\w*|gratis|\d+\s*%)\b', re.I,
)
_SERVICE_AREA = re.compile(
    r'\b(env[ií]\w*|entreg\w*|cobertura|nacional|todo m[eé]xico|todo el pa[iís])\b', re.I,
)
_CERTIFICATION = re.compile(r'\b(certific\w*|acredit\w*|aval\w*|garant\w*)\b', re.I)
_MODERATE = re.compile(
    r'\b(resistente\w*|durader\w*|c[oó]mod\w*|sostenible\w*|ecol[oó]gic\w*)\b', re.I,
)
_CAPABILITY = re.compile(
    r'\b(hacemos|elaboramos|confeccion\w*|fabric\w*|diseñ\w*|vend\w*|ofrec\w*|prest\w*)\b', re.I,
)


def _normalize(value: str) -> str:
    value = ''.join(
        char for char in unicodedata.normalize('NFD', value or '')
        if unicodedata.category(char) != 'Mn'
    )
    return re.sub(r'\s+', ' ', value.lower()).strip()


def _fragments(source_text: str) -> list[str]:
    return [
        item.strip(' -\t')
        for item in re.split(r'[\n]+|(?<=[.!?;])\s+', source_text or '')
        if item.strip(' -\t')
    ]


def _matching(fragments: list[str], pattern: re.Pattern) -> list[str]:
    return [fragment for fragment in fragments if pattern.search(fragment)]


def build_brand_fact_profile(source_text: str, *, keywords: list[str] | None = None) -> dict:
    """Derive only facts that are explicitly present in the submitted description.

    The raw source fragments remain attached so every allowed claim can be traced.
    Generated summaries, logo interpretations and visual guesses are intentionally
    excluded from this authority.
    """
    fragments = _fragments(source_text)
    normalized_source = _normalize(source_text)
    differentiating_terms = []
    for keyword in keywords or []:
        term = str(keyword).strip()
        if term and _normalize(term) in normalized_source and term not in differentiating_terms:
            differentiating_terms.append(term)

    return {
        'version': 1,
        'confirmed_offerings': fragments[1:] or fragments,
        'confirmed_materials': [
            term for term in differentiating_terms
            if any(token in _normalize(term) for token in ('material', 'tela', 'mezclilla', 'madera', 'algodon'))
        ],
        'confirmed_capabilities': _matching(fragments, _CAPABILITY),
        'confirmed_commercial_terms': _matching(fragments, _COMMERCIAL),
        'confirmed_service_area': _matching(fragments, _SERVICE_AREA),
        'confirmed_certifications': _matching(fragments, _CERTIFICATION),
        'allowed_moderate_claims': _matching(fragments, _MODERATE),
        'differentiating_terms': differentiating_terms,
        'unknowns_requiring_confirmation': [
            'precios_y_descuentos', 'vigencia_de_promociones', 'cobertura_de_envios',
            'certificaciones_y_garantias', 'cifras_ambientales',
        ],
        'source_fragments': [
            {'text': fragment, 'source': 'business_description'} for fragment in fragments
        ],
    }
