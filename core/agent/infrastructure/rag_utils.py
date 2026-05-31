import logging
from core.agent.infrastructure.embedding_service import get_query_embedding
from core.agent.infrastructure.models import AgentDocumentChunk

logger = logging.getLogger(__name__)

RAG_TOP_K = 3


def _query_chunks(query_embedding: list, top_k: int) -> list:
    from pgvector.django import CosineDistance
    return list(
        AgentDocumentChunk.objects
        .annotate(distance=CosineDistance('embedding', query_embedding))
        .order_by('distance')[:top_k]
    )


def get_rag_context(query: str) -> str:
    """Retorna chunks de documentos relevantes como string. Retorna '' si no hay docs o falla."""
    if AgentDocumentChunk.objects.count() == 0:
        return ''
    query_embedding = get_query_embedding(query)
    if query_embedding is None:
        return ''
    try:
        chunks = _query_chunks(query_embedding, RAG_TOP_K)
    except Exception as e:
        logger.warning(f'RAG context query failed: {e}')
        return ''
    if not chunks:
        return ''
    return '\n\n'.join(
        f'[Fragmento de {c.document.filename}]\n{c.content}'
        for c in chunks
    )
