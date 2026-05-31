"""Tests Sprint 15A — Generación de PDF en document_tools."""
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings

pytestmark = pytest.mark.django_db


FAKE_SETTINGS = {
    'GEMINI_API_KEY': 'test-key',
    'AI_MODEL': 'gemini-3.1-flash-lite',
}


class TestMarkdownToPdf:
    def test_returns_bytes(self):
        """_markdown_to_pdf devuelve bytes (PDF válido)."""
        from core.agent.infrastructure.tools.document_tools import _markdown_to_pdf
        result = _markdown_to_pdf('# Título\n\nContenido de prueba.')
        assert isinstance(result, bytes)
        assert result[:4] == b'%PDF'  # Magic bytes de PDF

    def test_handles_headings(self):
        """_markdown_to_pdf procesa H1, H2, H3 sin error."""
        from core.agent.infrastructure.tools.document_tools import _markdown_to_pdf
        text = '# H1\n## H2\n### H3\nPárrafo normal.'
        result = _markdown_to_pdf(text)
        assert isinstance(result, bytes)
        assert len(result) > 100

    def test_handles_bold_text(self):
        """_markdown_to_pdf procesa **texto en negrita** sin error."""
        from core.agent.infrastructure.tools.document_tools import _markdown_to_pdf
        result = _markdown_to_pdf('**Importante:** este texto está en negrita.')
        assert isinstance(result, bytes)

    def test_handles_spanish_chars(self):
        """_markdown_to_pdf maneja caracteres españoles (á, é, ñ, ¿, ¡)."""
        from core.agent.infrastructure.tools.document_tools import _markdown_to_pdf
        result = _markdown_to_pdf('# Propuesta de negocio\n\nBienvenido. Análisis específico para México.')
        assert isinstance(result, bytes)
        assert result[:4] == b'%PDF'

    def test_handles_empty_lines(self):
        """_markdown_to_pdf no falla con líneas vacías."""
        from core.agent.infrastructure.tools.document_tools import _markdown_to_pdf
        result = _markdown_to_pdf('\n\n# Título\n\n\nContenido.\n\n')
        assert isinstance(result, bytes)


class TestGenerateDocumentToolPdf:
    @override_settings(**FAKE_SETTINGS)
    def test_execute_returns_pdf_bytes_in_metadata(self):
        """GenerateDocumentTool.execute() incluye pdf_bytes en metadata."""
        from core.agent.infrastructure.tools.document_tools import GenerateDocumentTool
        tool = GenerateDocumentTool()
        fake_content = '# Propuesta\n\n## Introducción\n\nContenido de la propuesta.'
        with patch.object(tool._gemini, 'generate_response', return_value=fake_content):
            result = tool.execute(doc_type='propuesta', description='Identidad de marca')
        assert result.success is True
        assert 'pdf_bytes' in result.metadata
        assert isinstance(result.metadata['pdf_bytes'], bytes)
        assert result.metadata['pdf_bytes'][:4] == b'%PDF'

    @override_settings(**FAKE_SETTINGS)
    def test_execute_returns_pdf_filename(self):
        """GenerateDocumentTool.execute() incluye pdf_filename en metadata."""
        from core.agent.infrastructure.tools.document_tools import GenerateDocumentTool
        tool = GenerateDocumentTool()
        with patch.object(tool._gemini, 'generate_response', return_value='# Propuesta\n\nTexto.'):
            result = tool.execute(doc_type='propuesta', description='Test')
        assert 'pdf_filename' in result.metadata
        assert result.metadata['pdf_filename'].endswith('.pdf')
        assert 'propuesta' in result.metadata['pdf_filename']

    @override_settings(**FAKE_SETTINGS)
    def test_execute_still_returns_docx_bytes(self):
        """GenerateDocumentTool.execute() sigue retornando docx_bytes (no regresión)."""
        from core.agent.infrastructure.tools.document_tools import GenerateDocumentTool
        tool = GenerateDocumentTool()
        with patch.object(tool._gemini, 'generate_response', return_value='# Doc\n\nTexto.'):
            result = tool.execute(doc_type='informe', description='Test')
        assert 'docx_bytes' in result.metadata
        assert isinstance(result.metadata['docx_bytes'], bytes)
