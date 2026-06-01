from datetime import datetime, timedelta, timezone as dt_timezone, date
from core.brand_dna.models import BrandDNA

MEXICO_TZ = dt_timezone(timedelta(hours=-6))

_INDUSTRY_SCHEDULE = {
    'food': [
        (4, 11), (5, 11), (3, 18), (0, 12), (2, 11), (1, 19), (6, 10),
    ],
    'fitness': [
        (0, 6), (2, 6), (4, 6), (6, 9), (1, 7), (3, 6), (5, 9),
    ],
    'retail': [
        (5, 10), (4, 15), (3, 12), (2, 11), (0, 10), (6, 11), (1, 12),
    ],
    'beauty': [
        (4, 10), (5, 10), (2, 11), (1, 10), (0, 9), (3, 10), (6, 11),
    ],
    'tech': [
        (1, 9), (2, 9), (3, 10), (0, 10), (4, 9), (1, 14), (2, 14),
    ],
    'default': [
        (1, 9), (3, 9), (2, 10), (0, 9), (4, 9), (5, 10), (2, 15),
    ],
}

_INDUSTRY_KEYWORDS = {
    'food': ['restaurant', 'restaurante', 'comida', 'food', 'cocina', 'chef', 'menu',
             'cafeteria', 'cafe', 'café', 'pizz', 'taco', 'sushi', 'bakery', 'panaderia'],
    'fitness': ['gym', 'gimnasio', 'fitness', 'entrenamiento', 'workout', 'crossfit',
                'yoga', 'pilates', 'deporte', 'atleta', 'nutricion'],
    'retail': ['tienda', 'store', 'ropa', 'moda', 'fashion', 'boutique', 'accesorios',
               'calzado', 'zapatos', 'joyeria', 'retail', 'shop'],
    'beauty': ['salon', 'salón', 'spa', 'belleza', 'beauty', 'peluqueria', 'estetica',
               'cosmetica', 'makeup', 'skincare', 'nail'],
    'tech': ['software', 'tecnologia', 'tech', 'digital', 'app', 'desarrollo', 'startup',
             'saas', 'programacion', 'web', 'ia', 'ai'],
}


def detect_industry(brand_dna) -> str:
    text = f"{brand_dna.tone} {brand_dna.description}".lower()
    for industry, keywords in _INDUSTRY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return industry
    return 'default'


def smart_schedule_dates(brand_dna, base_date: date, count: int = 7) -> list[datetime]:
    industry = detect_industry(brand_dna)
    slots = _INDUSTRY_SCHEDULE[industry]

    result = []
    current = base_date

    hour = slots[0][1]
    result.append(datetime(current.year, current.month, current.day, hour, 0, 0, tzinfo=MEXICO_TZ))

    slot_idx = 1
    days_ahead = 1
    while len(result) < count:
        candidate = base_date + timedelta(days=days_ahead)
        candidate_weekday = candidate.weekday()
        for i, (weekday, hour) in enumerate(slots[slot_idx:], start=slot_idx):
            if weekday == candidate_weekday:
                result.append(
                    datetime(candidate.year, candidate.month, candidate.day, hour, 0, 0, tzinfo=MEXICO_TZ)
                )
                slot_idx = i + 1
                break
        days_ahead += 1
        if days_ahead > 30:
            while len(result) < count:
                d = base_date + timedelta(days=len(result))
                result.append(datetime(d.year, d.month, d.day, 9, 0, 0, tzinfo=MEXICO_TZ))
            break

    return result[:count]
