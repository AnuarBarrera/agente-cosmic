import logging
import re
import requests
from datetime import date
from urllib.parse import quote
from django.conf import settings
from core.agent.domain.tools import BaseTool, ToolResult
from core.agent.infrastructure.gemini_adapter import GeminiAdapter, FALLBACK_MESSAGE
from core.agent.infrastructure.rag_utils import get_rag_context

logger = logging.getLogger(__name__)

PLATFORM_SIZE = {
    'instagram': (1024, 1024),
    'story': (576, 1024),
    'linkedin': (1024, 576),
}


class GeneratePostImageTool(BaseTool):
    name = 'generate_post_image'

    def __init__(self):
        self._gemini = GeminiAdapter()

    def execute(self, topic: str, platform: str = 'instagram') -> ToolResult:
        api_key = settings.GEMINI_API_KEY
        if not api_key:
            return self._error('API key de Gemini no configurada.')
        model = getattr(settings, 'AI_MODEL', 'gemini-2.5-flash')
        platform = platform.lower().strip()
        if platform not in PLATFORM_SIZE:
            platform = 'instagram'

        rag_context = get_rag_context(topic)
        rag_section = (
            f'\n\nContexto adicional (úsalo si es relevante):\n{rag_context}'
        ) if rag_context else ''

        gemini_prompt = (
            f'Write an image generation prompt in English for a {platform} marketing post '
            f'about: {topic}.{rag_section}\n'
            f'Output a SINGLE LINE of plain text, no markdown, no asterisks, no bullets. '
            f'Include: scene description, colors, style. Under 40 words total.'
        )
        image_prompt = self._gemini.generate_response(
            prompt=gemini_prompt, api_key=api_key, model_name=model, thinking_budget=0
        )
        if image_prompt == FALLBACK_MESSAGE or not image_prompt.strip():
            return self._error('El servicio de IA no está disponible temporalmente.')

        # Eliminar cualquier markdown, saltos de línea o carácter especial que rompa la URL
        image_prompt = image_prompt.replace('\n', ' ').replace('\r', ' ')
        image_prompt = re.sub(r'[*#`_\[\]{}()|\\]', '', image_prompt)
        image_prompt = re.sub(r'\s+', ' ', image_prompt).strip()[:400]

        w, h = PLATFORM_SIZE[platform]
        url = (
            f'https://image.pollinations.ai/prompt/{quote(image_prompt)}'
            f'?model=flux&width={w}&height={h}&nologo=true&enhance=true'
        )
        try:
            resp = requests.get(url, timeout=90)
            resp.raise_for_status()
            image_bytes = resp.content
            if not image_bytes:
                return self._error('No se recibió imagen de Pollinations.')
            filename = f'post_{platform}_{date.today().isoformat()}.jpg'
            return ToolResult(
                content=f'Imagen para {platform} generada.',
                tool_name=self.name,
                success=True,
                metadata={'image_bytes': image_bytes, 'filename': filename, 'platform': platform},
            )
        except Exception as e:
            logger.error(f'Error en GeneratePostImageTool (Pollinations): {e}', exc_info=True)
            return self._error(f'No pude generar la imagen: {e}')
