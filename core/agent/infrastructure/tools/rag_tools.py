import io
import logging
import fitz  # PyMuPDF
from django.conf import settings
from core.agent.domain.tools import BaseTool, ToolResult
from core.agent.infrastructure.gemini_adapter import GeminiAdapter, FALLBACK_MESSAGE
from core.agent.infrastructure.embedding_service import get_embedding, get_query_embedding
from core.agent.infrastructure.models import AgentDocument, AgentDocumentChunk

logger = logging.getLogger(__name__)

CHUNK_SIZE = 500   # tokens aproximados (chars / 4)
CHUNK_OVERLAP = 50
TOP_K = 4


def _split_text(text: str) -> list[str]:
    """Divide texto en chunks con solapamiento."""
    chars_per_chunk = CHUNK_SIZE * 4
    overlap_chars = CHUNK_OVERLAP * 4
    chunks = []
    start = 0
    while start < len(text):
        end = start + chars_per_chunk
        chunks.append(text[start:end])
        start += chars_per_chunk - overlap_chars
    return [c for c in chunks if c.strip()]


class RAGUploadTool(BaseTool):
    name = 'rag_upload'

    def execute(self, filename: str, file_bytes: bytes, doc_type: str = 'general') -> ToolResult:
        if not file_bytes:
            return self._error('El archivo está vacío.')
        ext = filename.lower().rsplit('.', 1)[-1]
        try:
            if ext == 'docx':
                from docx import Document as DocxDocument
                doc_obj = DocxDocument(io.BytesIO(file_bytes))
                full_text = ' '.join(p.text for p in doc_obj.paragraphs if p.text.strip())
            else:
                with fitz.open(stream=file_bytes, filetype='pdf') as pdf:
                    full_text = ' '.join(page.get_text() for page in pdf)
        except Exception as e:
            logger.error(f'Error leyendo {filename}: {e}', exc_info=True)
            return self._error(f'No pude leer el archivo: {e}')

        chunks = _split_text(full_text)
        if not chunks:
            return self._error('El documento no tiene texto extraíble.')

        doc = AgentDocument.objects.create(
            filename=filename, doc_type=doc_type, num_chunks=len(chunks)
        )
        for i, chunk_text in enumerate(chunks):
            embedding = get_embedding(chunk_text)
            AgentDocumentChunk.objects.create(
                document=doc, chunk_index=i, content=chunk_text, embedding=embedding
            )

        return ToolResult(
            content=f'Documento "{filename}" guardado con {len(chunks)} fragmentos.',
            tool_name=self.name,
            success=True,
            metadata={'doc_id': doc.id, 'num_chunks': len(chunks)},
        )


class RAGQueryTool(BaseTool):
    name = 'rag_query'

    def __init__(self):
        self._gemini = GeminiAdapter()

    def execute(self, query: str) -> ToolResult:
        query_embedding = get_query_embedding(query)
        if query_embedding is None:
            return self._error('No se pudo generar el embedding de la consulta.')

        api_key = settings.GEMINI_API_KEY
        model = getattr(settings, 'AI_MODEL', 'gemini-2.5-flash')

        total_docs = AgentDocumentChunk.objects.count()
        if total_docs == 0:
            return ToolResult(
                content='No tienes documentos cargados. Usa /subir para agregar tu catálogo o contratos.',
                tool_name=self.name,
                success=True,
                metadata={},
            )

        from pgvector.django import CosineDistance
        similar_chunks = list(
            AgentDocumentChunk.objects
            .annotate(distance=CosineDistance('embedding', query_embedding))
            .order_by('distance')[:TOP_K]
        )
        context = '\n\n'.join(
            f'[Fragmento {i+1} de {chunk.document.filename}]\n{chunk.content}'
            for i, chunk in enumerate(similar_chunks)
        )
        prompt = (
            f'Responde la siguiente pregunta usando SOLO la información de los documentos proporcionados.\n'
            f'Si la información no está en los documentos, di que no la tienes.\n\n'
            f'Pregunta: {query}\n\n'
            f'Documentos:\n{context}'
        )
        answer = self._gemini.generate_response(
            prompt=prompt, api_key=api_key, model_name=model
        )
        if answer == FALLBACK_MESSAGE or not answer.strip():
            return self._error('El servicio de IA no está disponible temporalmente.')

        return ToolResult(
            content=answer, tool_name=self.name, success=True,
            metadata={'chunks_used': len(similar_chunks)},
        )
