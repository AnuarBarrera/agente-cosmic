"""Tests de repositorios contra la base de datos de prueba (SQLite en memoria)."""
import pytest
from django.test import override_settings
from core.agent.infrastructure.repositories import (
    DjangoSessionRepository,
    DjangoMemoryRepository,
    DjangoRequestRepository,
)
from core.agent.domain.entities import AgentMemory, AgentRequest

pytestmark = pytest.mark.django_db

AUTHORIZED_CHAT_ID = 111111111
UNAUTHORIZED_CHAT_ID = 999999999


@pytest.fixture
def session_repo():
    return DjangoSessionRepository()


@pytest.fixture
def memory_repo():
    return DjangoMemoryRepository()


@pytest.fixture
def request_repo():
    return DjangoRequestRepository()


@pytest.fixture
def authorized_session(session_repo):
    with override_settings(TELEGRAM_AUTHORIZED_CHAT_IDS=[AUTHORIZED_CHAT_ID]):
        return session_repo.get_or_create(AUTHORIZED_CHAT_ID, 'anuar', 'Anuar Barrera')


# --- SessionRepository ---

def test_session_created_on_first_call(session_repo):
    with override_settings(TELEGRAM_AUTHORIZED_CHAT_IDS=[AUTHORIZED_CHAT_ID]):
        session = session_repo.get_or_create(AUTHORIZED_CHAT_ID, 'anuar', 'Anuar')
    assert session.id is not None
    assert session.chat_id == AUTHORIZED_CHAT_ID


def test_session_is_authorized_when_in_whitelist(session_repo):
    with override_settings(TELEGRAM_AUTHORIZED_CHAT_IDS=[AUTHORIZED_CHAT_ID]):
        session = session_repo.get_or_create(AUTHORIZED_CHAT_ID, 'anuar', 'Anuar')
    assert session.is_authorized is True


def test_session_not_authorized_when_not_in_whitelist(session_repo):
    with override_settings(TELEGRAM_AUTHORIZED_CHAT_IDS=[]):
        session = session_repo.get_or_create(UNAUTHORIZED_CHAT_ID, 'stranger', 'Unknown')
    assert session.is_authorized is False


def test_session_returns_same_record_on_second_call(session_repo):
    with override_settings(TELEGRAM_AUTHORIZED_CHAT_IDS=[AUTHORIZED_CHAT_ID]):
        s1 = session_repo.get_or_create(AUTHORIZED_CHAT_ID, 'anuar', 'Anuar')
        s2 = session_repo.get_or_create(AUTHORIZED_CHAT_ID, 'anuar', 'Anuar')
    assert s1.id == s2.id


def test_session_authorization_updates_when_whitelist_changes(session_repo):
    with override_settings(TELEGRAM_AUTHORIZED_CHAT_IDS=[]):
        s1 = session_repo.get_or_create(AUTHORIZED_CHAT_ID, 'anuar', 'Anuar')
    assert s1.is_authorized is False

    with override_settings(TELEGRAM_AUTHORIZED_CHAT_IDS=[AUTHORIZED_CHAT_ID]):
        s2 = session_repo.get_or_create(AUTHORIZED_CHAT_ID, 'anuar', 'Anuar')
    assert s2.is_authorized is True
    assert s1.id == s2.id


def test_update_last_active(session_repo):
    with override_settings(TELEGRAM_AUTHORIZED_CHAT_IDS=[AUTHORIZED_CHAT_ID]):
        session = session_repo.get_or_create(AUTHORIZED_CHAT_ID, 'anuar', 'Anuar')
    # No debe lanzar excepción
    session_repo.update_last_active(session.id)


# --- MemoryRepository ---

def test_memory_save_and_retrieve(authorized_session, memory_repo):
    memory_repo.save(AgentMemory(session_id=authorized_session.id, role='user', content='Hola'))
    memory_repo.save(AgentMemory(session_id=authorized_session.id, role='assistant', content='Hola, ¿en qué te ayudo?'))

    history = memory_repo.get_recent(authorized_session.id)
    assert len(history) == 2
    assert history[0].role == 'user'
    assert history[1].role == 'assistant'


def test_memory_returns_chronological_order(authorized_session, memory_repo):
    for i in range(5):
        memory_repo.save(AgentMemory(session_id=authorized_session.id, role='user', content=f'Mensaje {i}'))

    history = memory_repo.get_recent(authorized_session.id, limit=5)
    contents = [m.content for m in history]
    assert contents == [f'Mensaje {i}' for i in range(5)]


def test_memory_respects_limit(authorized_session, memory_repo):
    for i in range(15):
        memory_repo.save(AgentMemory(session_id=authorized_session.id, role='user', content=f'msg {i}'))

    history = memory_repo.get_recent(authorized_session.id, limit=10)
    assert len(history) == 10


def test_memory_returns_most_recent_when_limited(authorized_session, memory_repo):
    for i in range(5):
        memory_repo.save(AgentMemory(session_id=authorized_session.id, role='user', content=f'msg {i}'))

    history = memory_repo.get_recent(authorized_session.id, limit=3)
    # Debe devolver los últimos 3 en orden cronológico
    assert history[0].content == 'msg 2'
    assert history[2].content == 'msg 4'


def test_memory_empty_for_new_session(authorized_session, memory_repo):
    history = memory_repo.get_recent(authorized_session.id)
    assert history == []


# --- RequestRepository ---

def test_request_log_success(authorized_session, request_repo):
    request_repo.log(AgentRequest(
        session_id=authorized_session.id,
        user_message='¿Qué hora es?',
        ai_response='No tengo acceso al tiempo real.',
        model_used='gemini-2.5-flash',
        duration_ms=320,
        estimated_tokens=45,
        success=True,
    ))
    from core.agent.infrastructure.models import AgentRequest as AgentRequestModel
    assert AgentRequestModel.objects.filter(session_id=authorized_session.id).count() == 1


def test_request_log_failure(authorized_session, request_repo):
    request_repo.log(AgentRequest(
        session_id=authorized_session.id,
        user_message='Pregunta',
        ai_response='Error procesando',
        model_used='gemini-2.5-flash',
        duration_ms=50,
        estimated_tokens=10,
        success=False,
        error_message='API timeout',
    ))
    from core.agent.infrastructure.models import AgentRequest as AgentRequestModel
    req = AgentRequestModel.objects.get(session_id=authorized_session.id)
    assert req.success is False
    assert req.error_message == 'API timeout'


def test_request_log_with_tool(authorized_session, request_repo):
    request_repo.log(AgentRequest(
        session_id=authorized_session.id,
        user_message='/post sobre apertura',
        ai_response='Post generado...',
        model_used='gemini-2.5-flash',
        duration_ms=800,
        estimated_tokens=200,
        success=True,
        tool_used='generate_post',
    ))
    from core.agent.infrastructure.models import AgentRequest as AgentRequestModel
    req = AgentRequestModel.objects.get(session_id=authorized_session.id)
    assert req.tool_used == 'generate_post'
