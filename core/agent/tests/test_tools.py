"""Tests de las herramientas (tools) del Sprint 2."""
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings
from core.agent.domain.tools import ToolResult
from core.agent.infrastructure.tools.content_tools import (
    GeneratePostTool, WriteTextTool, GenerateShortScriptTool,
)
from core.agent.infrastructure.tools.report_tools import GenerateMonthlyReportTool
from core.agent.infrastructure.tools.whisper_tool import TranscribeAudioTool

pytestmark = pytest.mark.django_db

GEMINI_RESPONSE = 'Respuesta simulada de Gemini para tests.'


@pytest.fixture
def mock_gemini():
    with patch('core.agent.infrastructure.tools.content_tools.GeminiAdapter') as Mock:
        instance = Mock.return_value
        instance.generate_response.return_value = GEMINI_RESPONSE
        yield instance


# ─── GeneratePostTool ──────────────────────────────────────────────────────

class TestGeneratePostTool:
    def test_returns_tool_result(self, mock_gemini):
        tool = GeneratePostTool()
        result = tool.execute(topic='Apertura nueva sucursal')
        assert isinstance(result, ToolResult)

    def test_success_on_valid_input(self, mock_gemini):
        tool = GeneratePostTool()
        result = tool.execute(topic='Apertura nueva sucursal', platform='instagram', tone='profesional')
        assert result.success is True
        assert result.content == GEMINI_RESPONSE
        assert result.tool_name == 'generate_post'

    def test_defaults_to_instagram_profesional(self, mock_gemini):
        tool = GeneratePostTool()
        result = tool.execute(topic='Mi negocio')
        assert result.metadata['platform'] == 'instagram'
        assert result.metadata['tone'] == 'profesional'

    def test_invalid_platform_falls_back_to_instagram(self, mock_gemini):
        tool = GeneratePostTool()
        result = tool.execute(topic='Mi negocio', platform='snapchat')
        assert result.metadata['platform'] == 'instagram'

    def test_invalid_tone_falls_back_to_profesional(self, mock_gemini):
        tool = GeneratePostTool()
        result = tool.execute(topic='Mi negocio', tone='agresivo')
        assert result.metadata['tone'] == 'profesional'

    def test_all_platforms_accepted(self, mock_gemini):
        tool = GeneratePostTool()
        for platform in ('instagram', 'facebook', 'linkedin', 'twitter', 'x'):
            result = tool.execute(topic='Test', platform=platform)
            assert result.metadata['platform'] == platform

    def test_prompt_contains_platform(self, mock_gemini):
        tool = GeneratePostTool()
        tool.execute(topic='Test', platform='linkedin')
        prompt = mock_gemini.generate_response.call_args[1]['prompt']
        assert 'linkedin' in prompt.lower()

    def test_prompt_contains_topic(self, mock_gemini):
        tool = GeneratePostTool()
        tool.execute(topic='Beneficios del café')
        prompt = mock_gemini.generate_response.call_args[1]['prompt']
        assert 'Beneficios del café' in prompt

    def test_gemini_error_returns_failure(self, mock_gemini):
        mock_gemini.generate_response.side_effect = Exception('API Error')
        tool = GeneratePostTool()
        result = tool.execute(topic='Test')
        assert result.success is False
        assert result.error is not None


# ─── WriteTextTool ─────────────────────────────────────────────────────────

class TestWriteTextTool:
    def test_returns_tool_result(self, mock_gemini):
        tool = WriteTextTool()
        result = tool.execute(context='Bienvenida a nuevos clientes')
        assert isinstance(result, ToolResult)

    def test_success_on_valid_input(self, mock_gemini):
        tool = WriteTextTool()
        result = tool.execute(context='Presentación de empresa', text_type='email')
        assert result.success is True
        assert result.tool_name == 'write_text'

    def test_defaults_to_email(self, mock_gemini):
        tool = WriteTextTool()
        result = tool.execute(context='Texto de prueba')
        assert result.metadata['text_type'] == 'email'

    def test_all_types_accepted(self, mock_gemini):
        tool = WriteTextTool()
        for t in ('email', 'descripcion', 'bio', 'anuncio', 'mensaje', 'propuesta'):
            result = tool.execute(context='Test', text_type=t)
            assert result.success is True

    def test_prompt_contains_context(self, mock_gemini):
        tool = WriteTextTool()
        tool.execute(context='Texto para cliente VIP')
        prompt = mock_gemini.generate_response.call_args[1]['prompt']
        assert 'Texto para cliente VIP' in prompt

    def test_gemini_error_returns_failure(self, mock_gemini):
        mock_gemini.generate_response.side_effect = Exception('Timeout')
        tool = WriteTextTool()
        result = tool.execute(context='Test')
        assert result.success is False


# ─── GenerateShortScriptTool ───────────────────────────────────────────────

class TestGenerateShortScriptTool:
    def test_returns_tool_result(self, mock_gemini):
        tool = GenerateShortScriptTool()
        result = tool.execute(topic='Marketing digital')
        assert isinstance(result, ToolResult)

    def test_success_on_valid_input(self, mock_gemini):
        tool = GenerateShortScriptTool()
        result = tool.execute(topic='Beneficios del emprendimiento')
        assert result.success is True
        assert result.tool_name == 'generate_short_script'

    def test_default_duration_60s(self, mock_gemini):
        tool = GenerateShortScriptTool()
        result = tool.execute(topic='Test')
        assert result.metadata['duration'] == '60'

    def test_prompt_contains_topic(self, mock_gemini):
        tool = GenerateShortScriptTool()
        tool.execute(topic='Ventas en redes sociales')
        prompt = mock_gemini.generate_response.call_args[1]['prompt']
        assert 'Ventas en redes sociales' in prompt

    def test_prompt_contains_hook_instruction(self, mock_gemini):
        tool = GenerateShortScriptTool()
        tool.execute(topic='Test')
        prompt = mock_gemini.generate_response.call_args[1]['prompt']
        assert 'HOOK' in prompt

    def test_gemini_error_returns_failure(self, mock_gemini):
        mock_gemini.generate_response.side_effect = Exception('Error')
        tool = GenerateShortScriptTool()
        result = tool.execute(topic='Test')
        assert result.success is False


# ─── GenerateMonthlyReportTool ─────────────────────────────────────────────

class TestGenerateMonthlyReportTool:
    @pytest.fixture
    def with_requests(self, db):
        from core.agent.infrastructure.models import AgentSession, AgentRequest
        session = AgentSession.objects.create(
            chat_id=444444444, username='test', full_name='Test', is_authorized=True
        )
        AgentRequest.objects.create(
            session=session, user_message='Hola', ai_response='Hola',
            model_used='gemini-2.5-flash', duration_ms=300, estimated_tokens=50, success=True,
        )
        AgentRequest.objects.create(
            session=session, user_message='Short', ai_response='Guion',
            model_used='gemini-2.5-flash', duration_ms=500, estimated_tokens=150,
            success=True, tool_used='generate_short_script',
        )
        AgentRequest.objects.create(
            session=session, user_message='Error', ai_response='',
            model_used='gemini-2.5-flash', duration_ms=50, estimated_tokens=5,
            success=False, error_message='Timeout',
        )
        return session

    def test_returns_tool_result(self, with_requests):
        tool = GenerateMonthlyReportTool()
        result = tool.execute(month=5, year=2026)
        assert isinstance(result, ToolResult)

    def test_success(self, with_requests):
        tool = GenerateMonthlyReportTool()
        result = tool.execute(month=5, year=2026)
        assert result.success is True
        assert result.tool_name == 'generate_monthly_report'

    def test_report_contains_totals(self, with_requests):
        tool = GenerateMonthlyReportTool()
        result = tool.execute(month=5, year=2026)
        assert 'Total' in result.content or 'total' in result.content

    def test_report_shows_tool_usage(self, with_requests):
        tool = GenerateMonthlyReportTool()
        result = tool.execute(month=5, year=2026)
        assert 'generate_short_script' in result.content

    def test_metadata_has_month_year(self, with_requests):
        tool = GenerateMonthlyReportTool()
        result = tool.execute(month=5, year=2026)
        assert result.metadata['month'] == 5
        assert result.metadata['year'] == 2026

    def test_empty_month_returns_zeros(self, db):
        tool = GenerateMonthlyReportTool()
        result = tool.execute(month=1, year=2000)
        assert result.success is True
        assert result.metadata['total_requests'] == 0


# ─── TranscribeAudioTool ───────────────────────────────────────────────────

class TestTranscribeAudioTool:
    def test_missing_file_returns_error(self):
        tool = TranscribeAudioTool()
        result = tool.execute(audio_path='/tmp/nonexistent_file_abc123.ogg')
        assert result.success is False
        assert result.error is not None

    def test_with_mock_whisper(self, tmp_path):
        audio_file = tmp_path / 'test.ogg'
        audio_file.write_bytes(b'fake audio data')

        mock_segment = MagicMock()
        mock_segment.text = ' Hola mundo esto es una prueba'
        mock_info = MagicMock()
        mock_info.language = 'es'
        mock_info.duration = 5.0

        with patch('core.agent.infrastructure.tools.whisper_tool.TranscribeAudioTool._get_model') as mock_get:
            mock_model = MagicMock()
            mock_model.transcribe.return_value = ([mock_segment], mock_info)
            mock_get.return_value = mock_model

            tool = TranscribeAudioTool()
            result = tool.execute(audio_path=str(audio_file))

        assert result.success is True
        assert 'Hola mundo' in result.content
        assert result.metadata['language'] == 'es'

    def test_empty_transcription_returns_error(self, tmp_path):
        audio_file = tmp_path / 'silent.ogg'
        audio_file.write_bytes(b'fake audio')

        mock_segment = MagicMock()
        mock_segment.text = '   '
        mock_info = MagicMock()
        mock_info.language = 'es'
        mock_info.duration = 2.0

        with patch('core.agent.infrastructure.tools.whisper_tool.TranscribeAudioTool._get_model') as mock_get:
            mock_model = MagicMock()
            mock_model.transcribe.return_value = ([mock_segment], mock_info)
            mock_get.return_value = mock_model

            tool = TranscribeAudioTool()
            result = tool.execute(audio_path=str(audio_file))

        assert result.success is False

    def test_whisper_not_installed_returns_error(self, tmp_path):
        audio_file = tmp_path / 'test.ogg'
        audio_file.write_bytes(b'fake')

        # Resetear modelo cacheado
        TranscribeAudioTool._model = None

        with patch('core.agent.infrastructure.tools.whisper_tool.TranscribeAudioTool._get_model') as mock_get:
            mock_get.return_value = None
            tool = TranscribeAudioTool()
            result = tool.execute(audio_path=str(audio_file))

        assert result.success is False
