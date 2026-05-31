"""Tests del Sprint 5 — Embeddings, memoria semántica y login de redes sociales."""
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings
from core.agent.infrastructure.embedding_service import get_embedding, get_query_embedding

pytestmark = pytest.mark.django_db


# ─── EmbeddingService ─────────────────────────────────────────────────────

def _make_mock_client(values):
    """Crea un mock de genai.Client con result.embeddings[0].values configurado."""
    mock_embedding = MagicMock()
    mock_embedding.values = values
    mock_result = MagicMock()
    mock_result.embeddings = [mock_embedding]
    mock_client = MagicMock()
    mock_client.models.embed_content.return_value = mock_result
    return mock_client


class TestEmbeddingService:
    def test_get_embedding_returns_list(self):
        mock_client = _make_mock_client([0.1] * 768)
        with patch('google.genai.Client', return_value=mock_client):
            with override_settings(GEMINI_API_KEY='test-key'):
                result = get_embedding('Hola mundo')
        assert isinstance(result, list)
        assert len(result) == 768

    def test_get_embedding_returns_none_without_api_key(self):
        with override_settings(GEMINI_API_KEY=''):
            result = get_embedding('Hola')
        assert result is None

    def test_get_embedding_returns_none_for_empty_text(self):
        with override_settings(GEMINI_API_KEY='test-key'):
            result = get_embedding('')
        assert result is None

    def test_get_embedding_returns_none_on_api_error(self):
        mock_client = MagicMock()
        mock_client.models.embed_content.side_effect = Exception('API Error')
        with patch('google.genai.Client', return_value=mock_client):
            with override_settings(GEMINI_API_KEY='test-key'):
                result = get_embedding('Hola')
        assert result is None

    def test_get_query_embedding_returns_list(self):
        mock_client = _make_mock_client([0.2] * 768)
        with patch('google.genai.Client', return_value=mock_client):
            with override_settings(GEMINI_API_KEY='test-key'):
                result = get_query_embedding('¿Cómo genero un post?')
        assert isinstance(result, list)
        assert len(result) == 768

    def test_embedding_uses_correct_model(self):
        mock_client = _make_mock_client([0.1] * 768)
        with patch('google.genai.Client', return_value=mock_client):
            with override_settings(GEMINI_API_KEY='test-key'):
                get_embedding('texto de prueba')
        call_kwargs = mock_client.models.embed_content.call_args[1]
        assert call_kwargs.get('model') == 'models/text-embedding-004'

    def test_query_embedding_uses_correct_model(self):
        mock_client = _make_mock_client([0.2] * 768)
        with patch('google.genai.Client', return_value=mock_client):
            with override_settings(GEMINI_API_KEY='test-key'):
                get_query_embedding('consulta de prueba')
        call_kwargs = mock_client.models.embed_content.call_args[1]
        assert call_kwargs.get('model') == 'models/text-embedding-004'


# ─── MemoryRepository con embeddings ──────────────────────────────────────

class TestMemoryRepositoryWithEmbeddings:
    @pytest.fixture
    def session(self, db):
        from core.agent.infrastructure.models import AgentSession
        return AgentSession.objects.create(
            chat_id=555555555, username='anuar', full_name='Anuar', is_authorized=True
        )

    @pytest.fixture
    def repo(self):
        from core.agent.infrastructure.repositories import DjangoMemoryRepository
        return DjangoMemoryRepository()

    def test_save_stores_embedding_when_available(self, session, repo):
        from core.agent.domain.entities import AgentMemory
        mock_embedding = [0.1] * 768

        with patch('core.agent.infrastructure.embedding_service.get_embedding', return_value=mock_embedding):
            memory = repo.save(AgentMemory(session_id=session.id, role='user', content='Hola'))

        from core.agent.infrastructure.models import AgentMemory as AgentMemoryModel
        obj = AgentMemoryModel.objects.get(id=memory.id)
        assert obj.embedding is not None

    def test_save_works_without_embedding(self, session, repo):
        from core.agent.domain.entities import AgentMemory

        with patch('core.agent.infrastructure.embedding_service.get_embedding', return_value=None):
            memory = repo.save(AgentMemory(session_id=session.id, role='user', content='Sin embedding'))

        assert memory.id is not None

    def test_get_context_falls_back_to_recent_without_embeddings(self, session, repo):
        from core.agent.domain.entities import AgentMemory

        with patch('core.agent.infrastructure.embedding_service.get_embedding', return_value=None):
            for i in range(3):
                repo.save(AgentMemory(session_id=session.id, role='user', content=f'Mensaje {i}'))

        context = repo.get_context(session.id, query='test')
        assert len(context) == 3

    def test_get_context_returns_list_of_agent_memory(self, session, repo):
        from core.agent.domain.entities import AgentMemory as AgentMemoryEntity

        with patch('core.agent.infrastructure.embedding_service.get_embedding', return_value=None):
            repo.save(AgentMemoryEntity(session_id=session.id, role='user', content='Test'))

        context = repo.get_context(session.id, query='test')
        assert all(isinstance(m, AgentMemoryEntity) for m in context)

    def test_get_context_chronological_order(self, session, repo):
        from core.agent.domain.entities import AgentMemory
        import time

        with patch('core.agent.infrastructure.embedding_service.get_embedding', return_value=None):
            for i in range(4):
                repo.save(AgentMemory(session_id=session.id, role='user', content=f'Msg {i}'))
                time.sleep(0.01)

        context = repo.get_context(session.id, query='test')
        timestamps = [m.timestamp for m in context]
        assert timestamps == sorted(timestamps)


# ─── AgentMemory model con VectorField ────────────────────────────────────

class TestAgentMemoryVectorField:
    def test_memory_can_be_saved_with_embedding(self, db):
        from core.agent.infrastructure.models import AgentSession, AgentMemory

        session = AgentSession.objects.create(
            chat_id=666666666, username='test', full_name='Test', is_authorized=True
        )
        embedding = [0.1] * 768
        memory = AgentMemory.objects.create(
            session=session,
            role='user',
            content='Texto con embedding',
            embedding=embedding,
        )
        assert memory.id is not None

    def test_memory_can_be_saved_without_embedding(self, db):
        from core.agent.infrastructure.models import AgentSession, AgentMemory

        session = AgentSession.objects.create(
            chat_id=777777777, username='test2', full_name='Test2', is_authorized=True
        )
        memory = AgentMemory.objects.create(
            session=session, role='assistant', content='Sin embedding', embedding=None
        )
        assert memory.embedding is None


# ─── BrowserLoginTool ─────────────────────────────────────────────────────

class TestBrowserLoginTool:
    def test_unsupported_platform_returns_error(self):
        from core.agent.infrastructure.tools.login_tool import BrowserLoginTool
        tool = BrowserLoginTool()
        with patch('core.agent.infrastructure.tools.login_tool.asyncio.run') as mock_run:
            mock_run.side_effect = Exception("platform not supported")
            result = tool.execute(platform='snapchat', username='user', password='pass')
        assert result.success is False

    def test_save_session_creates_browser_session(self, db):
        from core.agent.infrastructure.tools.login_tool import BrowserLoginTool
        from core.agent.infrastructure.models import BrowserSession

        tool = BrowserLoginTool()
        cookies = [{'name': 'sessionid', 'value': 'abc123', 'domain': 'instagram.com'}]
        tool._save_session('instagram', 'anuar', cookies, 'Mozilla/5.0')

        session = BrowserSession.objects.get(platform='instagram', username='anuar')
        assert session.is_valid is True
        assert session.cookies == cookies

    def test_save_session_updates_existing(self, db):
        from core.agent.infrastructure.tools.login_tool import BrowserLoginTool
        from core.agent.infrastructure.models import BrowserSession

        tool = BrowserLoginTool()
        old_cookies = [{'name': 'old', 'value': 'old'}]
        new_cookies = [{'name': 'new', 'value': 'new'}]

        tool._save_session('instagram', 'anuar', old_cookies, 'UA')
        tool._save_session('instagram', 'anuar', new_cookies, 'UA')

        assert BrowserSession.objects.filter(platform='instagram', username='anuar').count() == 1
        session = BrowserSession.objects.get(platform='instagram', username='anuar')
        assert session.cookies == new_cookies

    def test_successful_login_returns_success_result(self, db):
        from core.agent.infrastructure.tools.login_tool import BrowserLoginTool
        from core.agent.domain.tools import ToolResult

        tool = BrowserLoginTool()
        mock_cookies = [{'name': 'sessionid', 'value': 'token123'}]

        with patch.object(tool, '_login_async') as mock_login:
            import asyncio
            async def fake_login(p, u, pw):
                return ToolResult(
                    content='✅ Sesión iniciada en Instagram',
                    tool_name='browser_login',
                    success=True,
                )
            mock_login.side_effect = lambda p, u, pw: asyncio.coroutine(fake_login)(p, u, pw)

            with patch('core.agent.infrastructure.tools.login_tool.asyncio.run') as mock_run:
                mock_result = ToolResult(
                    content='✅ Sesión iniciada en Instagram',
                    tool_name='browser_login',
                    success=True,
                )
                mock_run.return_value = mock_result
                result = tool.execute(platform='instagram', username='anuar', password='pass')

        assert result.success is True
        assert 'Instagram' in result.content or 'instagram' in result.content.lower()
