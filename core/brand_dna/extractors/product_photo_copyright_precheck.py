import json
import logging
import google.genai as genai
from google.genai import types
from django.conf import settings
from pydantic import BaseModel
from core.shared.metrics_utils import track_external_api, record_tokens, vertex_labels

logger = logging.getLogger(__name__)

_REJECTED_REASON = (
    "Detectamos una marca, logo o personaje con derechos de terceros en esta foto. "
    "Prueba con otra foto de tu producto."
)

_PROMPT = (
    "Analiza esta foto de un producto real subida por un negocio. Solo te interesa "
    "detectar contenido de MARCA/COPYRIGHT DE TERCEROS -- no evalues ningun otro "
    "aspecto de la imagen.\n\n"
    "El producto real del negocio es aceptable aunque tenga texto o diseño propio "
    "impreso en su empaque/etiqueta (esto NO cuenta como riesgo).\n\n"
    "has_recognizable_brand_logo: true si aparece un logo o marca reconocible de UN "
    "TERCERO (no el producto propio del negocio) -- ej. una marca de refresco, ropa, "
    "o tecnologia conocida en el fondo o en otro objeto de la foto.\n"
    "has_licensed_character_or_ip: true si aparece un personaje con licencia "
    "(caricatura, superheroe, marca de entretenimiento) impreso en cualquier "
    "superficie de la foto.\n"
    "has_third_party_packaging_design: true si el empaque/etiqueta visible en la foto "
    "pertenece claramente a una marca comercial reconocible DISTINTA del producto "
    "propio del negocio (ej. una bolsa de una cadena de comida rapida usada como "
    "fondo, no como el producto que se vende).\n"
    "ok: true solo si los 3 flags anteriores son false."
)


class CopyrightPrecheckSchema(BaseModel):
    has_recognizable_brand_logo: bool
    has_licensed_character_or_ip: bool
    has_third_party_packaging_design: bool
    ok: bool


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION_TEXT,
    )


class ProductPhotoCopyrightPrecheck:
    def check(self, image_bytes: bytes, mime_type: str) -> dict:
        """Retorna {'ok': bool, 'reason': str} si la llamada se completo, o
        {'ok': True, 'skipped': True} si fallo (fail-open)."""
        try:
            client = _vertex_client()
            image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
            with track_external_api('gemini', operation='product_photo_copyright_precheck'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[_PROMPT, image_part],
                    config=types.GenerateContentConfig(
                        labels=vertex_labels(),
                        response_mime_type="application/json",
                        response_schema=CopyrightPrecheckSchema,
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    ),
                )
            record_tokens(resp, operation='product_photo_copyright_precheck',
                          prompt_preview=_PROMPT[:500],
                          response_preview=resp.text[:500] if resp.text else '')
            data = json.loads(resp.text)
            # Veredicto re-derivado en Python, no se confia en el `ok` del LLM --
            # mismo patron que ProductPhotoQCSchema en image_generator.py.
            parsed = CopyrightPrecheckSchema(**data)
            ok = not (
                parsed.has_recognizable_brand_logo
                or parsed.has_licensed_character_or_ip
                or parsed.has_third_party_packaging_design
            )
            if ok:
                return {'ok': True}
            logger.info(f"Copyright precheck REJECTED: {data}")
            return {'ok': False, 'reason': _REJECTED_REASON}
        except Exception as e:
            logger.error(f"ProductPhotoCopyrightPrecheck error (fail-open): {e}")
            return {'ok': True, 'skipped': True}
