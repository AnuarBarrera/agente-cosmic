import io
import logging
from PIL import Image, ImageOps, ImageFilter

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
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)  # fotos de celular traen la orientacion real en EXIF, no en los pixeles
        img = img.convert('RGB')

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


def enhance_photo_classic(image_bytes: bytes) -> bytes:
    """Nitidez suave + autocontraste — sin IA generativa. Preserva el aspect ratio
    real de la foto (no recorta a cuadrado): el template 3D de ProductShowcaseGenerator
    lee ese aspect ratio (variable `photo_aspect`) y dimensiona su propio plano para
    encajarlo sin perder contenido. Un recorte 1:1 ciego aquí perdía contenido real en
    fotos portrait/landscape (HALLAZGO 87: recortaba el texto superior de un globo y
    parte del producto en la parte inferior de la foto).

    Usado por ProductShowcaseGenerator para preparar la foto real del producto
    antes de componerla dentro de la escena 3D (HyperFrames/Three.js): la foto
    original ya es válida, solo necesita quedar lista para publicarse.
    """
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img = ImageOps.exif_transpose(img)
        img = img.convert('RGB')

        img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=3))
        img = ImageOps.autocontrast(img, cutoff=1)

        buf = io.BytesIO()
        img.save(buf, format='PNG')
        return buf.getvalue()
    except Exception as e:
        logger.warning(f"enhance_photo_classic falló (usando original): {e}")
        return image_bytes
