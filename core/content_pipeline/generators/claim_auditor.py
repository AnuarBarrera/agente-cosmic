"""Auditor determinista de afirmaciones contra ``BrandFactProfile``.

Los backstops críticos deliberadamente no dependen de disponibilidad de un
modelo externo. Un fallo de proveedor nunca convierte una promoción inventada
en contenido publicable.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass


SAFE_TEXT_BY_FIELD = {
    'caption': 'Conoce lo que tenemos para ti. Contáctanos para más información.',
    'headline': 'Conoce más',
    'subtitle': 'Descubre una opción pensada para ti.',
    'cta': 'Contáctanos hoy',
    'tag_cta': 'Contáctanos hoy',
    'hook_text': 'Descubre algo nuevo',
    'narration_script': 'Conoce nuestra propuesta y contáctanos para más información.',
}

_PERCENT_OR_PRICE = re.compile(r'(?:\b\d+(?:[.,]\d+)?\s*%|[$€£]\s*\d|\b\d+(?:[.,]\d+)?\s*(?:mxn|usd|pesos?|d[oó]lares?))', re.I)
_DISCOUNT = re.compile(r'\b(descuent\w*|promoci[oó]n\w*|oferta\w*|rebaja\w*|precio especial\w*|gratis)\b', re.I)
_SHIPPING = re.compile(r'\b(env[ií](?:o|os|amos|amos a)?|entreg\w*|cobertura (?:nacional|en)|todo m[eé]xico|a todo el pa[ií]s)\b', re.I)
_CERTIFIED = re.compile(r'\b(certificad\w*|acreditad\w*|avalad\w*|garantiz\w*|garant[ií]a)\b', re.I)
_UNSUPPORTED_OUTCOME = re.compile(
    r'(?:absorci[oó]n\s+superior|protegen?\b.{0,45}\b(?:[oó]ptim\w*|segur\w*)|'
    r'optimiza\w*\b.{0,45}\b(?:tiempo|procedimiento|cirug[ií]a)|'
    r'asegura\w*\b.{0,45}\b(?:eficiencia|seguridad|resultado)|'
    r'compromete\w*\b.{0,30}\bseguridad|favorece\w*\b.{0,30}\bbienestar)', re.I,
)
_ENVIRONMENTAL_NUMBER = re.compile(
    r'(?:\b\d+(?:[.,]\d+)?\s*(?:%|kg|toneladas?|litros?|co2)\b.{0,55}\b(?:recicla|ambient|residuo|emision|sostenib)|'
    r'\b(?:recicla|ambient|residuo|emision|sostenib)\w*.{0,55}\b\d+(?:[.,]\d+)?)', re.I,
)
_MODERATE = re.compile(r'\b(resistente|durader[oa]s?|c[oó]mod[oa]s?|sostenible|ecol[oó]gic[oa]s?)\b', re.I)
_SPECIFIC_QUALIFIERS = (
    'este mes', 'esta semana', 'solo hoy', 'por tiempo limitado',
    'nacional', 'todo mexico', 'todo el pais', 'cualquier ciudad',
)


def _normalize(value: str) -> str:
    value = ''.join(c for c in unicodedata.normalize('NFD', value or '') if unicodedata.category(c) != 'Mn')
    return re.sub(r'\s+', ' ', value.lower()).strip()


def _profile_values(profile: dict, key: str) -> list[str]:
    values = (profile or {}).get(key) or []
    return [str(v.get('text', '')) if isinstance(v, dict) else str(v) for v in values]


def _supported(fragment: str, evidence: list[str], pattern: re.Pattern) -> str:
    """Devuelve evidencia relevante; evita que una fuente irrelevante avale el claim."""
    normalized_fragment = _normalize(fragment)
    tokens = {t for t in re.findall(r'\w+', normalized_fragment) if len(t) >= 3 or t.isdigit()}
    fragment_numbers = set(re.findall(r'\d+(?:[.,]\d+)?', normalized_fragment))
    required_qualifiers = {
        qualifier for qualifier in _SPECIFIC_QUALIFIERS
        if qualifier in normalized_fragment
    }
    for item in evidence:
        normalized_item = _normalize(item)
        if not pattern.search(item):
            continue
        evidence_numbers = set(re.findall(r'\d+(?:[.,]\d+)?', normalized_item))
        if fragment_numbers and not fragment_numbers.issubset(evidence_numbers):
            continue
        if any(qualifier not in normalized_item for qualifier in required_qualifiers):
            continue
        evidence_tokens = {t for t in re.findall(r'\w+', normalized_item) if len(t) >= 3 or t.isdigit()}
        if tokens & evidence_tokens:
            return item
    return ''


@dataclass(frozen=True)
class ClaimFinding:
    fragment: str
    risk_level: str
    support: str
    decision: str
    reason: str
    category: str

    def to_dict(self) -> dict:
        return asdict(self)


def audit_claims(text: str, profile: dict | None) -> list[dict]:
    """Audita texto y retorna una decisión estructurada por afirmación."""
    findings = []
    fragments = [p.strip() for p in re.split(r'(?<=[.!?])\s+|\n+', text or '') if p.strip()]
    checks = (
        ('commercial_terms', 'critical', _PERCENT_OR_PRICE, 'Una cifra o precio requiere un término comercial confirmado.'),
        ('commercial_terms', 'critical', _DISCOUNT, 'Una promoción o descuento requiere vigencia y términos confirmados.'),
        ('service_area', 'critical', _SHIPPING, 'Envíos y cobertura requieren un área de servicio confirmada.'),
        ('certifications', 'critical', _CERTIFIED, 'Certificaciones y garantías requieren respaldo explícito.'),
        ('unsupported_outcome', 'critical', _UNSUPPORTED_OUTCOME, 'Resultados clínicos o de rendimiento requieren respaldo explícito.'),
        ('environmental_metrics', 'critical', _ENVIRONMENTAL_NUMBER, 'Las cifras ambientales requieren una fuente aprobada.'),
    )
    for fragment in fragments:
        for category, risk, pattern, reason in checks:
            if not pattern.search(fragment):
                continue
            profile_key = {
                'commercial_terms': 'confirmed_commercial_terms',
                'service_area': 'confirmed_service_area',
                'certifications': 'confirmed_certifications',
                'unsupported_outcome': 'source_fragments',
                # No hay bucket implícito para métricas: debe estar en un
                # fragmento de fuente aprobado y coincidir con la afirmación.
                'environmental_metrics': 'source_fragments',
            }[category]
            evidence = _profile_values(profile or {}, profile_key)
            support = _supported(fragment, evidence, pattern)
            findings.append(ClaimFinding(
                fragment, risk, support,
                'allow' if support else 'needs_confirmation',
                'Respaldado por una fuente aprobada.' if support else reason,
                category,
            ).to_dict())

        moderate = _MODERATE.search(fragment)
        if moderate:
            evidence = _profile_values(profile or {}, 'allowed_moderate_claims')
            support = _supported(fragment, evidence, _MODERATE)
            findings.append(ClaimFinding(
                moderate.group(0), 'moderate', support,
                'allow' if support else 'soften',
                'Respaldado por una fuente aprobada.' if support else 'La propiedad debe expresarse sin garantía.',
                'moderate_property',
            ).to_dict())
    return findings


def has_blocking_claims(findings: list[dict]) -> bool:
    return any(item.get('risk_level') == 'critical' and item.get('decision') != 'allow' for item in findings)


def ensure_supported_text(text: str, profile: dict | None, *, field_name: str = 'caption') -> tuple[str, list[dict]]:
    """Elimina fragmentos críticos y reaudita una vez; usa fallback si persisten."""
    findings = audit_claims(text, profile)
    rejected = {
        item['fragment'] for item in findings
        if item['risk_level'] == 'critical' and item['decision'] != 'allow'
    }
    if not rejected:
        return text, findings

    fragments = [p.strip() for p in re.split(r'(?<=[.!?])\s+|\n+', text or '') if p.strip()]
    corrected = ' '.join(fragment for fragment in fragments if fragment not in rejected).strip()
    corrected = corrected or SAFE_TEXT_BY_FIELD.get(field_name, SAFE_TEXT_BY_FIELD['caption'])
    second_findings = audit_claims(corrected, profile)
    if has_blocking_claims(second_findings):
        corrected = SAFE_TEXT_BY_FIELD.get(field_name, SAFE_TEXT_BY_FIELD['caption'])
        second_findings = audit_claims(corrected, profile)
    return corrected, findings + second_findings


def audit_text_fields(fields: dict[str, str], profile: dict | None) -> tuple[dict[str, str], dict[str, list[dict]]]:
    corrected, report = {}, {}
    for name, value in fields.items():
        corrected[name], report[name] = ensure_supported_text(str(value or ''), profile, field_name=name)
    return corrected, report
