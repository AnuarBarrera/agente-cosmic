import logging
import os
import tempfile
from core.agent.domain.tools import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class TranscribeAudioTool(BaseTool):
    name = 'transcribe_audio'
    _model = None

    def _get_model(self):
        if TranscribeAudioTool._model is None:
            try:
                from faster_whisper import WhisperModel
                TranscribeAudioTool._model = WhisperModel('small', device='cpu', compute_type='int8')
                logger.info("Modelo Whisper cargado correctamente.")
            except ImportError:
                logger.error("faster-whisper no está instalado. Ejecuta: pip install faster-whisper")
                return None
        return TranscribeAudioTool._model

    def execute(self, audio_path: str) -> ToolResult:
        if not os.path.isfile(audio_path):
            return self._error(f"Archivo de audio no encontrado: {audio_path}")

        model = self._get_model()
        if model is None:
            return self._error("El módulo de transcripción no está disponible.")

        try:
            segments, info = model.transcribe(audio_path, language='es', beam_size=5)
            text = ' '.join(seg.text.strip() for seg in segments)

            if not text.strip():
                return self._error("No se detectó voz en el audio.")

            return ToolResult(
                content=text.strip(),
                tool_name=self.name,
                success=True,
                metadata={'language': info.language, 'duration': round(info.duration, 1)},
            )
        except Exception as e:
            logger.error(f"Error en TranscribeAudioTool: {e}", exc_info=True)
            return self._error(f"Error al transcribir: {e}")

    def transcribe_bytes(self, audio_bytes: bytes, suffix: str = '.ogg') -> ToolResult:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name
        try:
            return self.execute(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
