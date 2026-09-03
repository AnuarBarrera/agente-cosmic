"""Deterministic policy helpers for media generation and regeneration."""

import re
from dataclasses import dataclass
from typing import Literal


FeedbackKind = Literal['visual', 'text', 'both']
SceneComplexity = Literal['low', 'medium', 'high']


_TEXT_TERMS = re.compile(
    r'\b(texto|caption|copy|descripci[oó]n|redacci[oó]n|frase|palabra|cta|t[ií]tulo|ortograf[ií]a)\b',
    re.IGNORECASE,
)
_VISUAL_TERMS = re.compile(
    r'\b(imagen|foto|visual|fondo|color(?:es|ido|ida)?|dise[nñ]o|producto|persona|escena|slide|carrusel)\b',
    re.IGNORECASE,
)


def classify_regeneration_feedback(feedback: str) -> FeedbackKind:
    """Classify user intent conservatively; ambiguous feedback changes both."""
    value = feedback or ''
    wants_text = bool(_TEXT_TERMS.search(value))
    wants_visual = bool(_VISUAL_TERMS.search(value))
    if wants_visual and not wants_text:
        return 'visual'
    if wants_text and not wants_visual:
        return 'text'
    return 'both'


@dataclass(frozen=True)
class ComplexityResult:
    level: SceneComplexity
    reasons: tuple[str, ...]


def classify_scene_complexity(direction: str, product_count: int = 1) -> ComplexityResult:
    """Small deterministic v1 classifier, intentionally independent from metrics."""
    text = (direction or '').lower()
    reasons = []
    signals = {
        'people': r'\b(persona|personas|hombre|mujer|niñ[oa]s?|modelo)\b',
        'hands': r'\b(mano|manos|sosteniendo|agarrando|interactuando)\b',
        'required_text': r'\b(texto|letrero|etiqueta|precio|logo)\b',
        'physical_interaction': r'\b(usando|aplicando|operando|tratamiento|cirug[ií]a)\b',
    }
    for name, pattern in signals.items():
        if re.search(pattern, text, re.IGNORECASE):
            reasons.append(name)
    if product_count > 1:
        reasons.append('multiple_products')
    if len(reasons) >= 2 or 'hands' in reasons or 'physical_interaction' in reasons:
        return ComplexityResult('high', tuple(reasons))
    if reasons:
        return ComplexityResult('medium', tuple(reasons))
    return ComplexityResult('low', ())


def simplify_scene_direction(direction: str, complexity: ComplexityResult) -> str:
    if complexity.level != 'high':
        return direction
    return (
        f"{direction}\nSIMPLIFIED SAFETY DIRECTION: one product only; no people, "
        "no hands, no physical interaction, no generated text or labels. "
        "Use a stable tabletop or environmental composition."
    )
