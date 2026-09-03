"""Memoria editorial mensual estructurada, acotada y serializable."""
from __future__ import annotations

import re


MAX_MEMORY_POSTS = 28


def empty_editorial_memory() -> dict:
    return {
        'post_summaries': [],
        'used_products': [],
        'used_phrases': [],
        'used_ctas': [],
        'used_scenarios': [],
        'used_emotional_angles': [],
        'reserved_topics': [],
    }


def _unique(values: list[str], limit: int = MAX_MEMORY_POSTS) -> list[str]:
    result = []
    seen = set()
    for value in values:
        value = re.sub(r'\s+', ' ', str(value or '')).strip()
        key = value.casefold()
        if value and key not in seen:
            result.append(value[:180])
            seen.add(key)
    return result[-limit:]


def update_editorial_memory(memory: dict | None, posts: list[dict]) -> dict:
    result = empty_editorial_memory()
    for key in result:
        result[key] = list((memory or {}).get(key) or [])
    for post in posts:
        caption = str(post.get('caption') or '').strip()
        first_sentence = re.split(r'(?<=[.!?])\s+', caption)[0] if caption else ''
        result['post_summaries'].append(
            f"{post.get('pillar', 'Sin pilar')}: {first_sentence[:120]}"
        )
        if first_sentence:
            result['used_phrases'].append(' '.join(first_sentence.split()[:8]))
        for key, post_key in (
            ('used_products', 'product_reference'),
            ('used_ctas', 'cta'),
            ('used_scenarios', 'scenario'),
            ('used_emotional_angles', 'emotional_angle'),
        ):
            value = post.get(post_key)
            if value:
                result[key].append(str(value))
    return {key: _unique(values) for key, values in result.items()}


def memory_prompt_block(memory: dict | None, week_number: int | None) -> str:
    memory = memory or empty_editorial_memory()
    if not any(memory.values()) and not week_number:
        return ''
    lines = [f"SEMANA DEL MES: {week_number or 1}.", "MEMORIA EDITORIAL (no repetir mecánicamente):"]
    labels = (
        ('post_summaries', 'Ideas ya usadas'),
        ('used_products', 'Productos/referencias ya usados'),
        ('used_phrases', 'Frases principales ya usadas'),
        ('used_ctas', 'CTA ya usados'),
        ('used_scenarios', 'Escenarios ya usados'),
        ('used_emotional_angles', 'Ángulos emocionales ya usados'),
        ('reserved_topics', 'Temas reservados'),
    )
    for key, label in labels:
        values = _unique(list(memory.get(key) or []), limit=12)
        if values:
            lines.append(f"- {label}: {' | '.join(values)}")
    lines.append(
        "Puedes repetir el nombre y términos propios de la marca, pero cambia en conjunto "
        "el propósito, beneficio, estructura y frase principal respecto a ideas anteriores."
    )
    return '\n'.join(lines)


def mechanically_similar(caption: str, previous_summary: str) -> bool:
    """Señala solo coincidencias conjuntas; una palabra aislada no basta."""
    def tokens(value):
        return {w for w in re.findall(r'\w+', value.casefold()) if len(w) > 3}

    current, previous = tokens(caption), tokens(previous_summary)
    if not current or not previous:
        return False
    overlap = len(current & previous) / max(1, min(len(current), len(previous)))
    same_opening = ' '.join(caption.casefold().split()[:4]) in previous_summary.casefold()
    same_structure = caption.count('!') == previous_summary.count('!') and caption.count('?') == previous_summary.count('?')
    return overlap >= .75 and same_opening and same_structure
