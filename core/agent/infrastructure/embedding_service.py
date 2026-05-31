"""
Servicio de embeddings usando Gemini text-embedding-004 (768 dimensiones).
Sin dependencias pesadas — usa la misma API key que el agente.
"""
import logging
from typing import Optional
from django.conf import settings

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = 'gemini-embedding-001'
EMBEDDING_DIMENSIONS = 768


def _client():
    from google import genai
    api_key = settings.GEMINI_API_KEY
    if not api_key:
        raise ValueError("GEMINI_API_KEY no configurada")
    return genai.Client(api_key=api_key)


def _embed(text: str) -> Optional[list[float]]:
    from google.genai import types
    try:
        client = _client()
        result = client.models.embed_content(
            model=EMBEDDING_MODEL,
            contents=text,
            config=types.EmbedContentConfig(output_dimensionality=EMBEDDING_DIMENSIONS),
        )
        return result.embeddings[0].values
    except Exception as e:
        logger.warning(f"Error generando embedding: {e}")
        return None


def get_embedding(text: str) -> Optional[list[float]]:
    if not text or not text.strip():
        return None
    return _embed(text[:8000])


def get_query_embedding(text: str) -> Optional[list[float]]:
    if not text or not text.strip():
        return None
    return _embed(text[:2000])
