"""Tests de entidades de dominio — sin base de datos."""
from core.agent.domain.entities import AgentSession, AgentMemory, AgentRequest


def test_agent_session_authorized():
    session = AgentSession(chat_id=123456, username='anuar', full_name='Anuar Barrera', is_authorized=True)
    assert session.chat_id == 123456
    assert session.is_authorized is True
    assert session.id is None


def test_agent_session_not_authorized():
    session = AgentSession(chat_id=999, username='', full_name='Unknown', is_authorized=False)
    assert session.is_authorized is False


def test_agent_memory_default_metadata():
    memory = AgentMemory(session_id=1, role='user', content='Hola')
    assert memory.metadata == {}
    assert memory.id is None
    assert memory.timestamp is None


def test_agent_memory_roles():
    user_msg = AgentMemory(session_id=1, role='user', content='Pregunta')
    assistant_msg = AgentMemory(session_id=1, role='assistant', content='Respuesta')
    assert user_msg.role == 'user'
    assert assistant_msg.role == 'assistant'


def test_agent_request_defaults():
    req = AgentRequest(
        session_id=1,
        user_message='Hola',
        ai_response='Hola también',
        model_used='gemini-2.5-flash',
        duration_ms=250,
        estimated_tokens=40,
        success=True,
    )
    assert req.tool_used is None
    assert req.error_message is None


def test_agent_request_failed():
    req = AgentRequest(
        session_id=1,
        user_message='msg',
        ai_response='Error',
        model_used='gemini-2.5-flash',
        duration_ms=100,
        estimated_tokens=10,
        success=False,
        error_message='Timeout',
    )
    assert req.success is False
    assert req.error_message == 'Timeout'
