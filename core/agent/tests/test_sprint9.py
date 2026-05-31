"""Tests del Sprint 9 — RAG de documentos."""
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings

pytestmark = pytest.mark.django_db


class TestRAGUploadTool:
    def test_upload_pdf_creates_document_and_chunks(self):
        from core.agent.infrastructure.tools.rag_tools import RAGUploadTool
        fake_pdf_bytes = b'%PDF-1.4 fake content for testing'
        mock_pages = [MagicMock(get_text=MagicMock(return_value='Texto de página uno. ' * 30))]
        with patch('core.agent.infrastructure.tools.rag_tools.fitz.open') as mock_fitz, \
             patch('core.agent.infrastructure.tools.rag_tools.get_embedding', return_value=[0.1] * 768), \
             override_settings(GEMINI_API_KEY='test-key'):
            mock_doc = MagicMock()
            mock_doc.__iter__ = MagicMock(return_value=iter(mock_pages))
            mock_fitz.return_value.__enter__ = MagicMock(return_value=mock_doc)
            mock_fitz.return_value.__exit__ = MagicMock(return_value=False)
            tool = RAGUploadTool()
            result = tool.execute(filename='catalogo.pdf', file_bytes=fake_pdf_bytes, doc_type='catalogo')
        assert result.success is True
        assert result.tool_name == 'rag_upload'
        from core.agent.infrastructure.models import AgentDocument
        assert AgentDocument.objects.filter(filename='catalogo.pdf').exists()

    def test_upload_creates_chunks_for_long_text(self):
        from core.agent.infrastructure.tools.rag_tools import RAGUploadTool, CHUNK_SIZE
        long_text = 'palabra ' * (CHUNK_SIZE * 3)
        mock_pages = [MagicMock(get_text=MagicMock(return_value=long_text))]
        with patch('core.agent.infrastructure.tools.rag_tools.fitz.open') as mock_fitz, \
             patch('core.agent.infrastructure.tools.rag_tools.get_embedding', return_value=[0.1] * 768), \
             override_settings(GEMINI_API_KEY='test-key'):
            mock_doc = MagicMock()
            mock_doc.__iter__ = MagicMock(return_value=iter(mock_pages))
            mock_fitz.return_value.__enter__ = MagicMock(return_value=mock_doc)
            mock_fitz.return_value.__exit__ = MagicMock(return_value=False)
            tool = RAGUploadTool()
            result = tool.execute(filename='largo.pdf', file_bytes=b'%PDF', doc_type='general')
        assert result.success is True
        from core.agent.infrastructure.models import AgentDocument
        doc = AgentDocument.objects.get(filename='largo.pdf')
        assert doc.num_chunks >= 2

    def test_upload_returns_error_for_empty_file(self):
        from core.agent.infrastructure.tools.rag_tools import RAGUploadTool
        with override_settings(GEMINI_API_KEY='test-key'):
            tool = RAGUploadTool()
            result = tool.execute(filename='vacio.pdf', file_bytes=b'', doc_type='general')
        assert result.success is False


class TestRAGQueryTool:
    def test_query_returns_answer_from_stored_chunks(self):
        from core.agent.infrastructure.tools.rag_tools import RAGQueryTool
        from core.agent.infrastructure.models import AgentDocument, AgentDocumentChunk
        doc = AgentDocument.objects.create(filename='test.pdf', num_chunks=1)
        AgentDocumentChunk.objects.create(
            document=doc, chunk_index=0,
            content='Nuestro servicio de diseño cuesta $5,000 MXN.',
            embedding=[0.1] * 768,
        )
        mock_gemini = MagicMock()
        mock_gemini.generate_response.return_value = 'El servicio de diseño cuesta $5,000 MXN.'
        with patch('core.agent.infrastructure.tools.rag_tools.GeminiAdapter', return_value=mock_gemini), \
             patch('core.agent.infrastructure.tools.rag_tools.get_query_embedding', return_value=[0.1] * 768), \
             override_settings(GEMINI_API_KEY='test-key'):
            tool = RAGQueryTool()
            result = tool.execute(query='¿Cuánto cuesta el diseño?')
        assert result.success is True
        assert result.tool_name == 'rag_query'

    def test_query_returns_message_when_no_documents(self):
        from core.agent.infrastructure.tools.rag_tools import RAGQueryTool
        # La BD de tests está limpia por @pytest.mark.django_db
        with patch('core.agent.infrastructure.tools.rag_tools.get_query_embedding', return_value=[0.1] * 768), \
             override_settings(GEMINI_API_KEY='test-key'):
            tool = RAGQueryTool()
            result = tool.execute(query='algo')
        assert result.success is True
        assert 'documento' in result.content.lower() or 'subir' in result.content.lower()

    def test_query_returns_error_without_embedding(self):
        from core.agent.infrastructure.tools.rag_tools import RAGQueryTool
        with patch('core.agent.infrastructure.tools.rag_tools.get_query_embedding', return_value=None), \
             override_settings(GEMINI_API_KEY='test-key'):
            tool = RAGQueryTool()
            result = tool.execute(query='algo')
        assert result.success is False
