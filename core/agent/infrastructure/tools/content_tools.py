import logging
from django.conf import settings
from core.agent.infrastructure.gemini_adapter import GeminiAdapter
from core.agent.infrastructure.rag_utils import get_rag_context
from core.agent.domain.tools import BaseTool, ToolResult

logger = logging.getLogger(__name__)

PLATFORMS = {'instagram', 'facebook', 'linkedin', 'twitter', 'x'}
TONES = {'profesional', 'casual', 'motivador', 'humoristico', 'informativo'}

PLATFORM_RULES = {
    'instagram': '150-300 palabras. Incluye emojis estratégicos y hasta 20 hashtags relevantes al final.',
    'facebook': '100-200 palabras. Tono conversacional, 3-5 hashtags, invita a comentar.',
    'linkedin': '200-400 palabras. Tono profesional, incluye aprendizaje o insight del tema, 3-5 hashtags.',
    'twitter': 'Máximo 280 caracteres. Impactante, directo, 2-3 hashtags.',
    'x': 'Máximo 280 caracteres. Impactante, directo, 2-3 hashtags.',
}


class GeneratePostTool(BaseTool):
    name = 'generate_post'

    def __init__(self):
        self._gemini = GeminiAdapter()
        self._api_key = settings.GEMINI_API_KEY
        self._model = getattr(settings, 'AI_MODEL', 'gemini-2.5-flash')

    def execute(self, topic: str, platform: str = 'instagram', tone: str = 'profesional') -> ToolResult:
        platform = platform.lower().strip()
        tone = tone.lower().strip()

        if platform not in PLATFORMS:
            platform = 'instagram'
        if tone not in TONES:
            tone = 'profesional'

        rules = PLATFORM_RULES.get(platform, PLATFORM_RULES['instagram'])
        rag_context = get_rag_context(topic)
        rag_section = (
            f'\n\nContexto de tus documentos de negocio (úsalo si es relevante):\n{rag_context}'
        ) if rag_context else ''

        prompt = (
            f"Eres un experto en marketing digital y redes sociales.\n"
            f"Crea un post para *{platform.upper()}* con tono *{tone}* sobre:\n"
            f"\"{topic}\"\n\n"
            f"Reglas para {platform}:\n{rules}"
            f"{rag_section}\n\n"
            f"Devuelve SOLO el texto del post, listo para copiar y publicar. Sin explicaciones previas."
        )

        try:
            content = self._gemini.generate_response(prompt=prompt, api_key=self._api_key, model_name=self._model)
            return ToolResult(
                content=content,
                tool_name=self.name,
                success=True,
                metadata={'platform': platform, 'tone': tone, 'topic': topic},
            )
        except Exception as e:
            logger.error(f"Error en GeneratePostTool: {e}", exc_info=True)
            return self._error(f"No pude generar el post: {e}")


class WriteTextTool(BaseTool):
    name = 'write_text'

    TEXT_TYPES = {
        'email': 'un correo electrónico profesional',
        'descripcion': 'una descripción atractiva',
        'bio': 'una biografía profesional',
        'anuncio': 'un anuncio publicitario',
        'mensaje': 'un mensaje directo',
        'propuesta': 'una propuesta de negocio',
        'guion': 'un guión de presentación',
    }

    def __init__(self):
        self._gemini = GeminiAdapter()
        self._api_key = settings.GEMINI_API_KEY
        self._model = getattr(settings, 'AI_MODEL', 'gemini-2.5-flash')

    def execute(self, text_context: str, text_type: str = 'email') -> ToolResult:
        text_type = text_type.lower().strip()
        type_label = self.TEXT_TYPES.get(text_type, f'un texto de tipo {text_type}')

        rag_context = get_rag_context(text_context)
        rag_section = (
            f'\n\nContexto adicional de tus documentos:\n{rag_context}'
        ) if rag_context else ''

        prompt = (
            f"Eres un redactor profesional experto en comunicación de negocios en español.\n"
            f"Escribe {type_label} con el siguiente contexto e instrucciones:\n\n"
            f"{text_context}"
            f"{rag_section}\n\n"
            f"El texto debe ser claro, natural y efectivo para su propósito.\n"
            f"Devuelve SOLO el texto redactado, sin explicaciones adicionales."
        )

        try:
            content = self._gemini.generate_response(prompt=prompt, api_key=self._api_key, model_name=self._model)
            return ToolResult(
                content=content,
                tool_name=self.name,
                success=True,
                metadata={'text_type': text_type},
            )
        except Exception as e:
            logger.error(f"Error en WriteTextTool: {e}", exc_info=True)
            return self._error(f"No pude redactar el texto: {e}")


class GenerateShortScriptTool(BaseTool):
    name = 'generate_short_script'

    def __init__(self):
        self._gemini = GeminiAdapter()
        self._api_key = settings.GEMINI_API_KEY
        self._model = getattr(settings, 'AI_MODEL', 'gemini-2.5-flash')

    def execute(self, topic: str, duration: str = '60') -> ToolResult:
        prompt = (
            f"Eres un creador de contenido experto en videos cortos (Reels, TikTok, YouTube Shorts).\n"
            f"Crea un guión completo para un short de {duration} segundos sobre:\n"
            f"\"{topic}\"\n\n"
            f"Estructura obligatoria:\n"
            f"🎯 HOOK (0-3 seg): Frase de apertura que capture atención inmediata\n"
            f"📝 CONTENIDO ({3}-{int(duration)-8} seg): Mensaje principal, dinámico y concreto\n"
            f"💡 PUNTOS CLAVE: Máximo 3 puntos, accionables\n"
            f"🔚 CTA (últimos 5 seg): Call to action claro\n\n"
            f"Incluye indicaciones de dirección entre corchetes [ej: mostrar pantalla, primer plano].\n"
            f"El guión debe sonar natural y hablado, no como un texto leído."
        )

        try:
            content = self._gemini.generate_response(prompt=prompt, api_key=self._api_key, model_name=self._model)
            return ToolResult(
                content=content,
                tool_name=self.name,
                success=True,
                metadata={'topic': topic, 'duration': duration},
            )
        except Exception as e:
            logger.error(f"Error en GenerateShortScriptTool: {e}", exc_info=True)
            return self._error(f"No pude generar el guión: {e}")
