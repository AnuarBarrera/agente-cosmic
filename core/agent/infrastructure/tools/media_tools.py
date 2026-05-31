import io
import logging
import django_rq
from datetime import date
from core.agent.domain.tools import BaseTool, ToolResult

logger = logging.getLogger(__name__)


def _tts(text: str, lang: str = 'es') -> bytes:
    """Genera audio MP3 con gTTS (Google Text-to-Speech). Funciona desde servidores."""
    from gtts import gTTS
    tts = gTTS(text=text, lang=lang, slow=False)
    buf = io.BytesIO()
    tts.write_to_fp(buf)
    return buf.getvalue()


class GenerateAudioTool(BaseTool):
    name = 'generate_audio'

    def execute(self, text: str, voice: str = 'es') -> ToolResult:
        if len(text) > 2000:
            return self._error('El texto es demasiado largo (máximo 2000 caracteres).')
        try:
            audio_bytes = _tts(text)
            filename = f'audio_{date.today().isoformat()}.mp3'
            return ToolResult(
                content='Audio generado correctamente.',
                tool_name=self.name,
                success=True,
                metadata={'audio_bytes': audio_bytes, 'filename': filename},
            )
        except Exception as e:
            logger.error(f'Error en GenerateAudioTool (gTTS): {e}', exc_info=True)
            return self._error(f'No pude generar el audio: {e}')


class GenerateVideoTool(BaseTool):
    name = 'generate_video'

    def execute(self, prompt: str, chat_id: int = None) -> ToolResult:
        try:
            queue = django_rq.get_queue('default')
            queue.enqueue(
                'core.agent.infrastructure.jobs.video_pexels_job',
                prompt=prompt,
                chat_id=chat_id,
                job_timeout=600,
            )
        except Exception as e:
            logger.error(f'Error encolando video job: {e}', exc_info=True)
            return self._error(f'No pude iniciar la generación del video: {e}')
        return ToolResult(
            content='⏳ Generando video... Te aviso cuando esté listo (puede tardar 2-4 minutos).',
            tool_name=self.name,
            success=True,
            metadata={'prompt': prompt},
        )
