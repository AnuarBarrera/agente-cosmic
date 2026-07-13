import unicodedata
from datetime import datetime, timedelta, timezone as dt_timezone, date

MEXICO_TZ = dt_timezone(timedelta(hours=-6))


def _strip_accents(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', text) if unicodedata.category(c) != 'Mn')

# Benchmarks de engagement: (weekday, hour) — weekday: 0=lunes, 6=domingo
# Cada industria tiene exactamente 7 slots, uno por día de la semana (sin repetir weekday)
_INDUSTRY_SCHEDULE = {
    'food': {
        4: 11,  # viernes 11am
        5: 11,  # sábado 11am
        3: 18,  # jueves 6pm
        0: 12,  # lunes 12pm
        2: 11,  # miércoles 11am
        1: 19,  # martes 7pm
        6: 10,  # domingo 10am
    },
    'fitness': {
        0: 6,   # lunes 6am
        2: 6,   # miércoles 6am
        4: 6,   # viernes 6am
        6: 9,   # domingo 9am
        1: 7,   # martes 7am
        3: 6,   # jueves 6am
        5: 9,   # sábado 9am
    },
    'retail': {
        5: 10,  # sábado 10am
        4: 15,  # viernes 3pm
        3: 12,  # jueves 12pm
        2: 11,  # miércoles 11am
        0: 10,  # lunes 10am
        6: 11,  # domingo 11am
        1: 12,  # martes 12pm
    },
    'beauty': {
        4: 10,  # viernes 10am
        5: 10,  # sábado 10am
        2: 11,  # miércoles 11am
        1: 10,  # martes 10am
        0: 9,   # lunes 9am
        3: 10,  # jueves 10am
        6: 11,  # domingo 11am
    },
    'tech': {
        1: 9,   # martes 9am
        2: 9,   # miércoles 9am
        3: 10,  # jueves 10am
        0: 10,  # lunes 10am
        4: 9,   # viernes 9am
        5: 11,  # sábado 11am
        6: 10,  # domingo 10am
    },
    'health': {
        0: 9,   # lunes 9am
        1: 12,  # martes 12pm
        2: 9,   # miércoles 9am
        3: 17,  # jueves 5pm
        4: 12,  # viernes 12pm
        5: 10,  # sábado 10am
        6: 11,  # domingo 11am
    },
    'pets': {
        0: 18,  # lunes 6pm
        1: 12,  # martes 12pm
        2: 18,  # miércoles 6pm
        3: 12,  # jueves 12pm
        4: 17,  # viernes 5pm
        5: 11,  # sábado 11am
        6: 12,  # domingo 12pm
    },
    'professional': {
        0: 9,   # lunes 9am
        1: 10,  # martes 10am
        2: 14,  # miércoles 2pm
        3: 9,   # jueves 9am
        4: 15,  # viernes 3pm
        5: 10,  # sábado 10am
        6: 11,  # domingo 11am
    },
    # Catch-all para negocios que no matchean ninguna keyword de arriba — el
    # ICP incluye muchos rubros que no encajan en las 8 categorías de arriba
    # (ver H51). Antes tenia solo 3 horas distintas (9/10/11am) en toda la
    # semana, lo que se sentia repetitivo aunque tecnicamente "variara por dia".
    'default': {
        0: 9,   # lunes 9am
        1: 13,  # martes 1pm
        2: 10,  # miércoles 10am
        3: 17,  # jueves 5pm
        4: 12,  # viernes 12pm
        5: 11,  # sábado 11am
        6: 10,  # domingo 10am
    },
}

# Keywords en minusculas y SIN acentos — detect_industry() normaliza el texto del
# usuario antes de comparar (H51: "pediátricas" no matcheaba "pediatra" por el acento).
_INDUSTRY_KEYWORDS = {
    'food': ['restaurant', 'restaurante', 'comida', 'food', 'cocina', 'chef', 'menu',
             'cafeteria', 'cafe', 'pizz', 'taco', 'sushi', 'bakery', 'panaderia'],
    'fitness': ['gym', 'gimnasio', 'fitness', 'entrenamiento', 'workout', 'crossfit',
                'yoga', 'pilates', 'deporte', 'atleta', 'nutricion'],
    'retail': ['tienda', 'store', 'ropa', 'moda', 'fashion', 'boutique', 'accesorios',
               'calzado', 'zapatos', 'joyeria', 'retail', 'shop'],
    # "spa" suelto se quito — coincidia por substring dentro de "despacho", "espacio", etc.
    'beauty': ['salon', 'belleza', 'beauty', 'peluqueria', 'estetica', 'masaje',
               'cosmetica', 'makeup', 'skincare', 'nail'],
    # "ia"/"ai" sueltos se quitaron — coincidian por substring dentro de palabras
    # comunes en español (ej. "veterinaria" contiene "ia"), dando falsos positivos.
    'tech': ['software', 'tecnologia', 'tech', 'digital', 'app', 'desarrollo', 'startup',
             'saas', 'programacion', 'inteligencia artificial'],
    'health': ['salud', 'medico', 'medica', 'clinica', 'doctor', 'doctora', 'dentista',
               'pediatra', 'pediatric', 'psicolog', 'terapia', 'nutriolog', 'consultorio',
               'hospital', 'enfermeria'],
    'pets': ['mascota', 'mascotas', 'perro', 'perros', 'gato', 'gatos', 'veterinaria',
             'veterinario', 'pet shop', 'canino', 'felino', 'petshop'],
    'professional': ['consultoria', 'abogado', 'abogada', 'contador', 'contadora',
                      'asesoria', 'despacho', 'notario', 'contable', 'bufete'],
}


def detect_industry(brand_dna) -> str:
    text = _strip_accents(f"{brand_dna.tone} {brand_dna.description}".lower())
    for industry, keywords in _INDUSTRY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return industry
    return 'default'


def smart_schedule_dates(brand_dna, base_date: date, count: int = 7) -> list[datetime]:
    industry = detect_industry(brand_dna)
    schedule = _INDUSTRY_SCHEDULE[industry]

    seen_dates = set()
    result = []

    # Día 1: siempre base_date con la hora óptima para ese día de la semana
    base_weekday = base_date.weekday()
    base_hour = schedule.get(base_weekday, 9)
    dt0 = datetime(base_date.year, base_date.month, base_date.day, base_hour, 0, 0, tzinfo=MEXICO_TZ)
    result.append(dt0)
    seen_dates.add(base_date)

    # Días 2-N: avanzar día a día, tomar el slot óptimo para ese weekday
    days_ahead = 1
    while len(result) < count:
        candidate = base_date + timedelta(days=days_ahead)
        if candidate not in seen_dates:
            weekday = candidate.weekday()
            hour = schedule.get(weekday, 9)
            result.append(
                datetime(candidate.year, candidate.month, candidate.day, hour, 0, 0, tzinfo=MEXICO_TZ)
            )
            seen_dates.add(candidate)
        days_ahead += 1

    return result[:count]
