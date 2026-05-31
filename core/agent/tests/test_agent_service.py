"""Tests del AgentService con GeminiAdapter mockeado."""
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings
from core.agent.application.agent_service import AgentService, UNAUTHORIZED_MSG, ERROR_MSG
from core.agent.infrastructure.models import AgentMemory as AgentMemoryModel, AgentRequest as AgentRequestModel

pytestmark = pytest.mark.django_db

AUTHORIZED_CHAT_ID = 222222222
GEMINI_RESPONSE = 'Esta es la respuesta del agente.'


@pytest.fixture
def mock_gemini():
    with patch('core.agent.application.agent_service.GeminiAdapter') as MockAdapter:
        instance = MockAdapter.return_value
        instance.generate_response.return_value = GEMINI_RESPONSE
        yield instance


@pytest.fixture
def service(mock_gemini):
    with override_settings(
        TELEGRAM_AUTHORIZED_CHAT_IDS=[AUTHORIZED_CHAT_ID],
        GEMINI_API_KEY='test-key',
        AI_MODEL='gemini-2.5-flash',
    ):
        return AgentService()


# --- Flujo principal ---

def test_authorized_user_gets_response(service, mock_gemini):
    with override_settings(TELEGRAM_AUTHORIZED_CHAT_IDS=[AUTHORIZED_CHAT_ID]):
        response = service.process_message(AUTHORIZED_CHAT_ID, 'anuar', 'Anuar Barrera', 'Hola')
    assert response == GEMINI_RESPONSE
    mock_gemini.generate_response.assert_called_once()


def test_unauthorized_user_gets_denied(service, mock_gemini):
    with override_settings(TELEGRAM_AUTHORIZED_CHAT_IDS=[]):
        response = service.process_message(999999, 'stranger', 'Unknown', 'Hola')
    assert response == UNAUTHORIZED_MSG
    mock_gemini.generate_response.assert_not_called()


def test_message_saved_to_memory(service, mock_gemini):
    with override_settings(TELEGRAM_AUTHORIZED_CHAT_IDS=[AUTHORIZED_CHAT_ID]):
        service.process_message(AUTHORIZED_CHAT_ID, 'anuar', 'Anuar', 'Mi mensaje de prueba')

    memories = AgentMemoryModel.objects.filter(session__chat_id=AUTHORIZED_CHAT_ID)
    assert memories.count() == 2
    assert memories.filter(role='user').exists()
    assert memories.filter(role='assistant').exists()


def test_user_message_content_saved(service, mock_gemini):
    with override_settings(TELEGRAM_AUTHORIZED_CHAT_IDS=[AUTHORIZED_CHAT_ID]):
        service.process_message(AUTHORIZED_CHAT_ID, 'anuar', 'Anuar', 'Texto del usuario')

    user_memory = AgentMemoryModel.objects.get(session__chat_id=AUTHORIZED_CHAT_ID, role='user')
    assert user_memory.content == 'Texto del usuario'


def test_assistant_response_saved(service, mock_gemini):
    with override_settings(TELEGRAM_AUTHORIZED_CHAT_IDS=[AUTHORIZED_CHAT_ID]):
        service.process_message(AUTHORIZED_CHAT_ID, 'anuar', 'Anuar', 'Hola')

    assistant_memory = AgentMemoryModel.objects.get(session__chat_id=AUTHORIZED_CHAT_ID, role='assistant')
    assert assistant_memory.content == GEMINI_RESPONSE


def test_request_metrics_logged(service, mock_gemini):
    with override_settings(TELEGRAM_AUTHORIZED_CHAT_IDS=[AUTHORIZED_CHAT_ID]):
        service.process_message(AUTHORIZED_CHAT_ID, 'anuar', 'Anuar', 'Hola')

    req = AgentRequestModel.objects.get(session__chat_id=AUTHORIZED_CHAT_ID)
    assert req.success is True
    assert req.duration_ms >= 0
    assert req.estimated_tokens > 0
    assert req.model_used == 'gemini-2.5-flash'


# --- Manejo de errores ---

def test_gemini_error_returns_error_message(service, mock_gemini):
    mock_gemini.generate_response.side_effect = Exception('API Error')
    with override_settings(TELEGRAM_AUTHORIZED_CHAT_IDS=[AUTHORIZED_CHAT_ID]):
        response = service.process_message(AUTHORIZED_CHAT_ID, 'anuar', 'Anuar', 'Hola')
    assert response == ERROR_MSG


def test_gemini_error_logs_failure(service, mock_gemini):
    mock_gemini.generate_response.side_effect = Exception('Timeout')
    with override_settings(TELEGRAM_AUTHORIZED_CHAT_IDS=[AUTHORIZED_CHAT_ID]):
        service.process_message(AUTHORIZED_CHAT_ID, 'anuar', 'Anuar', 'Hola')

    req = AgentRequestModel.objects.get(session__chat_id=AUTHORIZED_CHAT_ID)
    assert req.success is False
    assert 'Timeout' in req.error_message


# --- Construcción del prompt ---

def test_prompt_includes_system_prompt(service, mock_gemini):
    with override_settings(
        TELEGRAM_AUTHORIZED_CHAT_IDS=[AUTHORIZED_CHAT_ID],
        AGENT_SYSTEM_PROMPT='Eres un asistente de prueba.',
    ):
        svc = AgentService()
        prompt = svc._build_prompt([], 'Mensaje del usuario')
    assert 'Eres un asistente de prueba.' in prompt


def test_prompt_includes_user_message(service):
    prompt = service._build_prompt([], 'Mi pregunta específica')
    assert 'Mi pregunta específica' in prompt


def test_prompt_includes_history(service):
    from core.agent.domain.entities import AgentMemory
    history = [
        AgentMemory(session_id=1, role='user', content='Primera pregunta'),
        AgentMemory(session_id=1, role='assistant', content='Primera respuesta'),
    ]
    prompt = service._build_prompt(history, 'Segunda pregunta')
    assert 'Primera pregunta' in prompt
    assert 'Primera respuesta' in prompt
    assert 'Segunda pregunta' in prompt


def test_prompt_without_history_has_no_history_section(service):
    prompt = service._build_prompt([], 'Hola')
    assert 'Historial' not in prompt


# --- Memoria contextual entre mensajes ---

def test_history_is_included_in_subsequent_messages(service, mock_gemini):
    with override_settings(TELEGRAM_AUTHORIZED_CHAT_IDS=[AUTHORIZED_CHAT_ID]):
        service.process_message(AUTHORIZED_CHAT_ID, 'anuar', 'Anuar', 'Me llamo Anuar')
        service.process_message(AUTHORIZED_CHAT_ID, 'anuar', 'Anuar', '¿Cómo me llamo?')

    calls = mock_gemini.generate_response.call_args_list
    second_call_prompt = calls[1][1]['prompt'] if 'prompt' in calls[1][1] else calls[1][0][0]
    assert 'Me llamo Anuar' in second_call_prompt
