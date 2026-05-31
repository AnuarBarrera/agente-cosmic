"""Tests del Sprint 6 — Búsqueda web y documentos Word."""
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings

pytestmark = pytest.mark.django_db


# ─── WebSearchTool ────────────────────────────────────────────────────────────

class TestWebSearchTool:
    def _brave_response(self, results=None):
        if results is None:
            results = [
                {'title': 'Marketing 2026', 'url': 'https://example.com', 'description': 'Tendencias clave del año'},
                {'title': 'Guía digital', 'url': 'https://example2.com', 'description': 'Contenido de video lidera'},
            ]
        return {'web': {'results': results}}

    def test_execute_returns_tool_result_with_summary(self):
        from core.agent.infrastructure.tools.search_tools import WebSearchTool
        mock_mcp = MagicMock()
        mock_mcp.call.return_value = self._brave_response()
        mock_gemini = MagicMock()
        mock_gemini.generate_response.return_value = 'Resumen: el video lidera en 2026.'
        with patch('core.agent.infrastructure.tools.search_tools.McpClient', return_value=mock_mcp), \
             patch('core.agent.infrastructure.tools.search_tools.GeminiAdapter', return_value=mock_gemini), \
             override_settings(GEMINI_API_KEY='test-key'):
            tool = WebSearchTool()
            result = tool.execute(query='tendencias marketing 2026')
        assert result.success is True
        assert result.tool_name == 'web_search'
        assert 'Resumen' in result.content

    def test_execute_returns_error_without_api_key(self):
        from core.agent.infrastructure.tools.search_tools import WebSearchTool
        with override_settings(GEMINI_API_KEY=''):
            tool = WebSearchTool()
            result = tool.execute(query='algo')
        assert result.success is False

    def test_execute_returns_error_on_search_failure(self):
        from core.agent.infrastructure.tools.search_tools import WebSearchTool
        mock_mcp = MagicMock()
        mock_mcp.call.side_effect = Exception('Connection error')
        with patch('core.agent.infrastructure.tools.search_tools.McpClient', return_value=mock_mcp), \
             override_settings(GEMINI_API_KEY='test-key'):
            tool = WebSearchTool()
            result = tool.execute(query='algo')
        assert result.success is False

    def test_execute_passes_content_through_scrape_guard(self):
        from core.agent.infrastructure.tools.search_tools import WebSearchTool
        mock_mcp = MagicMock()
        mock_mcp.call.return_value = self._brave_response([
            {'title': 'Test', 'url': 'https://example.com', 'description': 'Contenido normal'},
        ])
        mock_gemini = MagicMock()
        mock_gemini.generate_response.return_value = 'Resumen'
        with patch('core.agent.infrastructure.tools.search_tools.McpClient', return_value=mock_mcp), \
             patch('core.agent.infrastructure.tools.search_tools.GeminiAdapter', return_value=mock_gemini), \
             patch('core.agent.infrastructure.tools.search_tools.scrape_guard') as mock_guard, \
             override_settings(GEMINI_API_KEY='test-key'):
            mock_guard.safe_external_content.return_value = 'contenido seguro'
            tool = WebSearchTool()
            result = tool.execute(query='algo')
        mock_guard.safe_external_content.assert_called()
        assert result.success is True

    def test_execute_returns_no_results_message_when_empty(self):
        from core.agent.infrastructure.tools.search_tools import WebSearchTool
        mock_mcp = MagicMock()
        mock_mcp.call.return_value = {'web': {'results': []}}
        with patch('core.agent.infrastructure.tools.search_tools.McpClient', return_value=mock_mcp), \
             override_settings(GEMINI_API_KEY='test-key'):
            tool = WebSearchTool()
            result = tool.execute(query='xyzzy12345')
        assert result.success is False
        assert 'resultado' in result.content.lower() or 'encontr' in result.content.lower()


# ─── GenerateDocumentTool ─────────────────────────────────────────────────────

class TestGenerateDocumentTool:
    def test_execute_returns_docx_bytes(self):
        from core.agent.infrastructure.tools.document_tools import GenerateDocumentTool
        mock_gemini = MagicMock()
        mock_gemini.generate_response.return_value = (
            '# Propuesta de Identidad de Marca\n\n'
            '## Introducción\nTexto de introducción.\n\n'
            '## Servicios\nLista de servicios.\n\n'
            '## Precio\n$15,000 MXN'
        )
        with patch('core.agent.infrastructure.tools.document_tools.GeminiAdapter', return_value=mock_gemini), \
             override_settings(GEMINI_API_KEY='test-key'):
            tool = GenerateDocumentTool()
            result = tool.execute(doc_type='propuesta', description='Identidad de marca para empresa X')
        assert result.success is True
        assert result.tool_name == 'generate_document'
        assert isinstance(result.metadata.get('docx_bytes'), bytes)
        assert len(result.metadata['docx_bytes']) > 0

    def test_execute_returns_error_for_invalid_type(self):
        from core.agent.infrastructure.tools.document_tools import GenerateDocumentTool
        with override_settings(GEMINI_API_KEY='test-key'):
            tool = GenerateDocumentTool()
            result = tool.execute(doc_type='tipo_invalido', description='algo')
        assert result.success is False
        assert 'tipo' in result.content.lower() or 'invalid' in result.content.lower() or 'válido' in result.content.lower()

    def test_execute_returns_error_without_api_key(self):
        from core.agent.infrastructure.tools.document_tools import GenerateDocumentTool
        with override_settings(GEMINI_API_KEY=''):
            tool = GenerateDocumentTool()
            result = tool.execute(doc_type='propuesta', description='algo')
        assert result.success is False

    def test_execute_includes_all_doc_types(self):
        from core.agent.infrastructure.tools.document_tools import GenerateDocumentTool, DOC_TYPES
        expected = {'propuesta', 'contrato', 'informe', 'brief', 'presupuesto'}
        assert expected.issubset(set(DOC_TYPES.keys()))

    def test_execute_metadata_includes_filename(self):
        from core.agent.infrastructure.tools.document_tools import GenerateDocumentTool
        mock_gemini = MagicMock()
        mock_gemini.generate_response.return_value = '# Contrato\n\n## Cláusula 1\nTexto.'
        with patch('core.agent.infrastructure.tools.document_tools.GeminiAdapter', return_value=mock_gemini), \
             override_settings(GEMINI_API_KEY='test-key'):
            tool = GenerateDocumentTool()
            result = tool.execute(doc_type='contrato', description='Servicio de diseño')
        assert 'filename' in result.metadata
        assert result.metadata['filename'].endswith('.docx')
