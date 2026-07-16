import hashlib
import random

# Tipografías reales via Google Fonts, compartidas entre las imagenes/carrusel
# (image_generator.py) y la portada/contraportada de reels (reel_generator.py)
# para que ambos usen exactamente el mismo catalogo y el mismo seed semanal.
FONT_PRESETS = [
    {'font_family': "'Poppins', sans-serif", 'font_import': 'Poppins:wght@400;600;700;900'},
    {'font_family': "'Playfair Display', serif", 'font_import': 'Playfair+Display:wght@400;600;700;900'},
    {'font_family': "'Space Grotesk', sans-serif", 'font_import': 'Space+Grotesk:wght@400;500;600;700'},
    {'font_family': "'Bebas Neue', sans-serif", 'font_import': 'Bebas+Neue'},
    {'font_family': "'DM Sans', sans-serif", 'font_import': 'DM+Sans:wght@400;500;700'},
]


def choose_font_preset(seed: str) -> dict:
    """Elige una fuente de forma determinista a partir de `seed` (el job_id del
    calendario) en vez de puramente al azar — asi las 7 imagenes de una misma
    semana Y la portada/contraportada del reel de esa semana usan la MISMA
    fuente (consistencia de marca), incluso si el usuario regenera un solo
    post despues (mismo seed => mismo preset)."""
    if not seed:
        return random.choice(FONT_PRESETS)
    digest = hashlib.sha256(seed.encode()).hexdigest()
    idx = int(digest, 16) % len(FONT_PRESETS)
    return FONT_PRESETS[idx]
