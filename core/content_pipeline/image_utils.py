import io
import logging
from PIL import Image

logger = logging.getLogger(__name__)

_MAX_DIMENSION = 1024   # px — suficiente para Gemini Vision y Imagen 3
_WEBP_QUALITY = 85      # balance calidad/tamaño


def normalize_image(image_bytes: bytes, max_dimension: int = _MAX_DIMENSION) -> bytes:
    """Convierte a WebP y redimensiona si excede max_dimension en cualquier eje.

    Reduce tokens de Gemini Vision y tamaño de payload para Imagen 3.
    Retorna los bytes originales si algo falla, para no bloquear el pipeline.
    """
    if not image_bytes:
        return image_bytes
    try:
        original_size = len(image_bytes)
        img = Image.open(io.BytesIO(image_bytes)).convert('RGB')

        if img.width > max_dimension or img.height > max_dimension:
            img.thumbnail((max_dimension, max_dimension), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format='WEBP', quality=_WEBP_QUALITY, method=4)
        result = buf.getvalue()

        reduction = (1 - len(result) / original_size) * 100
        logger.info(
            f"normalize_image: {original_size // 1024}KB → {len(result) // 1024}KB "
            f"({reduction:.0f}% reducción, {img.width}×{img.height}px, WebP)"
        )
        return result
    except Exception as e:
        logger.warning(f"normalize_image falló (usando original): {e}")
        return image_bytes
