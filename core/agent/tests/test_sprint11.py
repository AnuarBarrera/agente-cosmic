"""Tests del Sprint 11 — RAG integrado, McpClient, Brave Search MCP, /ayuda completo."""
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings

pytestmark = pytest.mark.django_db


# ─── get_rag_context ────────────────────────────────────────────────────────

class TestGetRagContext:
    def test_returns_empty_string_when_no_chunks_in_db(self):
        """Con BD vacía (sin chunks), retorna cadena vacía sin intentar embedding."""
        from core.agent.infrastructure.rag_utils import get_rag_context
        assert get_rag_context('cualquier consulta') == ''

    def test_returns_empty_string_when_embedding_fails(self):
        """Cuando get_query_embedding retorna None, retorna cadena vacía."""
        from core.agent.infrastructure.rag_utils import get_rag_context
        with patch('core.agent.infrastructure.rag_utils.AgentDocumentChunk') as MockChunk, \
             patch('core.agent.infrastructure.rag_utils.get_query_embedding', return_value=None):
            MockChunk.objects.count.return_value = 5
            assert get_rag_context('mi consulta') == ''

    def test_returns_formatted_chunk_content_when_docs_exist(self):
        """Cuando hay chunks, retorna texto con filename y contenido."""
        from core.agent.infrastructure.rag_utils import get_rag_context
        mock_chunk = MagicMock()
        mock_chunk.content = 'Nuestro servicio premium cuesta $1,500 al mes.'
        mock_chunk.document.filename = 'precios.pdf'

        with patch('core.agent.infrastructure.rag_utils.AgentDocumentChunk') as MockChunk, \
             patch('core.agent.infrastructure.rag_utils.get_query_embedding', return_value=[0.1] * 768), \
             patch('core.agent.infrastructure.rag_utils._query_chunks', return_value=[mock_chunk]):
            MockChunk.objects.count.return_value = 1
            result = get_rag_context('precio del servicio')

        assert 'precios.pdf' in result
        assert 'Nuestro servicio premium' in result

    def test_returns_empty_string_when_query_chunks_raises(self):
        """Si _query_chunks lanza excepción (ej. pgvector no disponible), retorna ''."""
        from core.agent.infrastructure.rag_utils import get_rag_context
        with patch('core.agent.infrastructure.rag_utils.AgentDocumentChunk') as MockChunk, \
             patch('core.agent.infrastructure.rag_utils.get_query_embedding', return_value=[0.1] * 768), \
             patch('core.agent.infrastructure.rag_utils._query_chunks', side_effect=Exception('pgvector error')):
            MockChunk.objects.count.return_value = 1
            assert get_rag_context('consulta') == ''


# ─── RAG en herramientas de contenido ───────────────────────────────────────

class TestGeneratePostToolWithRag:
    def test_prompt_includes_rag_context_when_docs_exist(self):
        """GeneratePostTool incluye contexto RAG en el prompt cuando hay documentos."""
        from core.agent.infrastructure.tools.content_tools import GeneratePostTool
        prompts_captured = []

        def capture(prompt, api_key, model_name):
            prompts_captured.append(prompt)
            return 'Post generado de prueba.'

        with patch('core.agent.infrastructure.tools.content_tools.GeminiAdapter') as MockGemini, \
             patch('core.agent.infrastructure.tools.content_tools.get_rag_context',
                   return_value='Precio catálogo: $500 al mes.'), \
             override_settings(GEMINI_API_KEY='test-key'):
            MockGemini.return_value.generate_response.side_effect = capture
            tool = GeneratePostTool()
            result = tool.execute(topic='Nuestros precios', platform='instagram', tone='profesional')

        assert result.success is True
        assert 'Precio catálogo: $500 al mes.' in prompts_captured[0]

    def test_works_without_rag_context_when_no_docs(self):
        """GeneratePostTool funciona igual cuando get_rag_context retorna ''."""
        from core.agent.infrastructure.tools.content_tools import GeneratePostTool
        with patch('core.agent.infrastructure.tools.content_tools.GeminiAdapter') as MockGemini, \
             patch('core.agent.infrastructure.tools.content_tools.get_rag_context', return_value=''), \
             override_settings(GEMINI_API_KEY='test-key'):
            MockGemini.return_value.generate_response.return_value = 'Post generado.'
            tool = GeneratePostTool()
            result = tool.execute(topic='Apertura nueva sucursal')
        assert result.success is True


class TestWriteTextToolWithRag:
    def test_prompt_includes_rag_context_when_docs_exist(self):
        """WriteTextTool incluye contexto RAG en el prompt cuando hay documentos."""
        from core.agent.infrastructure.tools.content_tools import WriteTextTool
        prompts_captured = []

        def capture(prompt, api_key, model_name):
            prompts_captured.append(prompt)
            return 'Texto redactado.'

        with patch('core.agent.infrastructure.tools.content_tools.GeminiAdapter') as MockGemini, \
             patch('core.agent.infrastructure.tools.content_tools.get_rag_context',
                   return_value='Política de devoluciones: 30 días.'), \
             override_settings(GEMINI_API_KEY='test-key'):
            MockGemini.return_value.generate_response.side_effect = capture
            tool = WriteTextTool()
            result = tool.execute(context='Email a cliente sobre devolución', text_type='email')

        assert result.success is True
        assert 'Política de devoluciones: 30 días.' in prompts_captured[0]


class TestGenerateDocumentToolWithRag:
    def test_prompt_includes_rag_context_when_docs_exist(self):
        """GenerateDocumentTool incluye contexto RAG en el prompt."""
        from core.agent.infrastructure.tools.document_tools import GenerateDocumentTool
        prompts_captured = []

        def capture(prompt, api_key, model_name):
            prompts_captured.append(prompt)
            return '# Propuesta\n## Servicios\nTexto de propuesta.'

        with patch('core.agent.infrastructure.tools.document_tools.GeminiAdapter') as MockGemini, \
             patch('core.agent.infrastructure.tools.document_tools.get_rag_context',
                   return_value='Tarifa diseño web: $3,000.'), \
             override_settings(GEMINI_API_KEY='test-key'):
            MockGemini.return_value.generate_response.side_effect = capture
            tool = GenerateDocumentTool()
            result = tool.execute(doc_type='propuesta', description='Identidad de marca')

        assert result.success is True
        assert 'Tarifa diseño web: $3,000.' in prompts_captured[0]


class TestGeneratePostImageToolWithRag:
    def test_prompt_includes_rag_context_when_docs_exist(self):
        """GeneratePostImageTool incluye contexto RAG en el prompt de copy."""
        from core.agent.infrastructure.tools.image_tools import GeneratePostImageTool
        prompts_captured = []

        def capture(prompt, api_key, model_name, **kwargs):
            prompts_captured.append(prompt)
            return 'a professional marketing photo for instagram'

        mock_resp = MagicMock()
        mock_resp.content = b'fake_png'
        mock_resp.raise_for_status = MagicMock()
        with patch('core.agent.infrastructure.tools.image_tools.GeminiAdapter') as MockGemini, \
             patch('core.agent.infrastructure.tools.image_tools.requests.get',
                   return_value=mock_resp), \
             patch('core.agent.infrastructure.tools.image_tools.get_rag_context',
                   return_value='Promoción del mes: 20% descuento.'), \
             override_settings(GEMINI_API_KEY='test-key'):
            MockGemini.return_value.generate_response.side_effect = capture
            tool = GeneratePostImageTool()
            result = tool.execute(topic='Apertura nueva sucursal', platform='instagram')

        assert result.success is True
        assert 'Promoción del mes: 20% descuento.' in prompts_captured[0]


# ─── McpClient ──────────────────────────────────────────────────────────────

class TestMcpClient:
    def test_call_makes_post_to_server_url(self):
        """call() hace POST a {base_url}/call con tool y params en el body."""
        from core.agent.infrastructure.mcp_client import McpClient
        mock_response = MagicMock()
        mock_response.json.return_value = {'web': {'results': []}}
        mock_response.raise_for_status = MagicMock()

        with patch('core.agent.infrastructure.mcp_client.requests.post',
                   return_value=mock_response) as mock_post, \
             override_settings(MCP_SERVERS={'brave_search': 'http://brave-search-mcp:8080'}):
            client = McpClient()
            result = client.call('brave_search', 'web_search', {'query': 'test'})

        mock_post.assert_called_once_with(
            'http://brave-search-mcp:8080/call',
            json={'tool': 'web_search', 'params': {'query': 'test'}},
            timeout=20,
        )
        assert result == {'web': {'results': []}}

    def test_call_raises_value_error_when_server_not_configured(self):
        """call() lanza ValueError cuando el server no está en MCP_SERVERS."""
        from core.agent.infrastructure.mcp_client import McpClient
        with override_settings(MCP_SERVERS={}):
            client = McpClient()
            with pytest.raises(ValueError, match="not configured"):
                client.call('brave_search', 'web_search', {'query': 'test'})

    def test_call_raises_on_http_error(self):
        """call() propaga la excepción cuando el servidor responde con error HTTP."""
        import requests as req
        from core.agent.infrastructure.mcp_client import McpClient
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = req.HTTPError('503 Service Unavailable')

        with patch('core.agent.infrastructure.mcp_client.requests.post',
                   return_value=mock_response), \
             override_settings(MCP_SERVERS={'brave_search': 'http://brave-search-mcp:8080'}):
            client = McpClient()
            with pytest.raises(req.HTTPError):
                client.call('brave_search', 'web_search', {'query': 'test'})


# ─── /ayuda completo ────────────────────────────────────────────────────────

class TestAyudaText:
    def test_ayuda_text_includes_all_active_commands(self):
        """AYUDA_TEXT contiene todos los comandos de Sprints 1-10."""
        from core.agent.management.commands.run_telegram_bot import AYUDA_TEXT
        expected_commands = [
            '/post', '/texto', '/short', '/documento',
            '/imagen', '/buscar', '/prospecto', '/consultar',
            '/estadisticas', '/prospectar', '/reporte', '/consumo',
        ]
        for cmd in expected_commands:
            assert cmd in AYUDA_TEXT, f"'{cmd}' no está en AYUDA_TEXT"

    def test_ayuda_text_is_string(self):
        from core.agent.management.commands.run_telegram_bot import AYUDA_TEXT
        assert isinstance(AYUDA_TEXT, str)
        assert len(AYUDA_TEXT) > 200


# ─── WebSearchTool (migrado a McpClient) ────────────────────────────────────

class TestWebSearchToolWithMcp:
    def _make_brave_response(self, results=None):
        if results is None:
            results = [
                {'title': 'Taquería El Rey', 'url': 'https://taqueria.mx', 'description': 'Los mejores tacos de CDMX.'},
                {'title': 'Mercado de Sabores', 'url': 'https://mercado.mx', 'description': 'Gastronomía local.'},
            ]
        return {'web': {'results': results}}

    def test_execute_calls_mcp_brave_search(self):
        """execute() llama McpClient.call con server='brave_search' y tool='web_search'."""
        from core.agent.infrastructure.tools.search_tools import WebSearchTool
        mock_mcp = MagicMock()
        mock_mcp.call.return_value = self._make_brave_response()

        with patch('core.agent.infrastructure.tools.search_tools.McpClient', return_value=mock_mcp), \
             patch('core.agent.infrastructure.tools.search_tools.GeminiAdapter') as MockGemini, \
             override_settings(GEMINI_API_KEY='test-key'):
            MockGemini.return_value.generate_response.return_value = 'Resumen de tacos en CDMX.'
            tool = WebSearchTool()
            result = tool.execute(query='tacos en Ciudad de México')

        mock_mcp.call.assert_called_once()
        call_args = mock_mcp.call.call_args
        assert call_args[0][0] == 'brave_search'
        assert call_args[0][1] == 'web_search'
        assert call_args[0][2]['query'] == 'tacos en Ciudad de México'
        assert call_args[0][2]['country'] == 'MX'

    def test_execute_returns_success_with_gemini_summary(self):
        """execute() retorna ToolResult exitoso con el resumen de Gemini."""
        from core.agent.infrastructure.tools.search_tools import WebSearchTool
        mock_mcp = MagicMock()
        mock_mcp.call.return_value = self._make_brave_response()

        with patch('core.agent.infrastructure.tools.search_tools.McpClient', return_value=mock_mcp), \
             patch('core.agent.infrastructure.tools.search_tools.GeminiAdapter') as MockGemini, \
             override_settings(GEMINI_API_KEY='test-key'):
            MockGemini.return_value.generate_response.return_value = 'Los mejores tacos están en CDMX.'
            tool = WebSearchTool()
            result = tool.execute(query='tacos en Ciudad de México')

        assert result.success is True
        assert 'Los mejores tacos' in result.content
        assert 'taqueria.mx' in result.content
        assert result.tool_name == 'web_search'

    def test_execute_returns_error_when_mcp_raises(self):
        """execute() retorna ToolResult con error cuando McpClient lanza excepción."""
        from core.agent.infrastructure.tools.search_tools import WebSearchTool
        mock_mcp = MagicMock()
        mock_mcp.call.side_effect = Exception('Connection refused')

        with patch('core.agent.infrastructure.tools.search_tools.McpClient', return_value=mock_mcp), \
             override_settings(GEMINI_API_KEY='test-key'):
            tool = WebSearchTool()
            result = tool.execute(query='algo')

        assert result.success is False
        assert 'Error al buscar' in result.content

    def test_strips_conversational_prefix_before_searching(self):
        """execute() limpia prefijos como 'búscame' antes de pasar la query al MCP."""
        from core.agent.infrastructure.tools.search_tools import WebSearchTool
        mock_mcp = MagicMock()
        mock_mcp.call.return_value = self._make_brave_response()

        with patch('core.agent.infrastructure.tools.search_tools.McpClient', return_value=mock_mcp), \
             patch('core.agent.infrastructure.tools.search_tools.GeminiAdapter') as MockGemini, \
             override_settings(GEMINI_API_KEY='test-key'):
            MockGemini.return_value.generate_response.return_value = 'Resultado.'
            tool = WebSearchTool()
            tool.execute(query='búscame restaurantes en Monterrey')

        actual_query = mock_mcp.call.call_args[0][2]['query']
        assert 'búscame' not in actual_query
        assert 'restaurantes en Monterrey' in actual_query

    def test_adds_freshness_param_for_news_queries(self):
        """execute() añade freshness='pw' cuando la query contiene palabras de noticias."""
        from core.agent.infrastructure.tools.search_tools import WebSearchTool
        mock_mcp = MagicMock()
        mock_mcp.call.return_value = self._make_brave_response()

        with patch('core.agent.infrastructure.tools.search_tools.McpClient', return_value=mock_mcp), \
             patch('core.agent.infrastructure.tools.search_tools.GeminiAdapter') as MockGemini, \
             override_settings(GEMINI_API_KEY='test-key'):
            MockGemini.return_value.generate_response.return_value = 'Noticias.'
            tool = WebSearchTool()
            tool.execute(query='noticias de marketing hoy')

        params = mock_mcp.call.call_args[0][2]
        assert params.get('freshness') == 'pw'
