from typing import List
from django.conf import settings
from django.utils import timezone
from ..domain.entities import AgentSession, AgentMemory, AgentRequest
from ..domain.ports import SessionRepository, MemoryRepository, RequestRepository
from . import models


class DjangoSessionRepository(SessionRepository):
    def get_or_create(self, chat_id: int, username: str, full_name: str) -> AgentSession:
        admin_ids = getattr(settings, 'TELEGRAM_ADMIN_CHAT_IDS', [])
        authorized_ids = getattr(settings, 'TELEGRAM_AUTHORIZED_CHAT_IDS', [])
        all_authorized = set(admin_ids) | set(authorized_ids)
        expected_role = 'admin' if chat_id in admin_ids else 'viewer'
        expected_auth = chat_id in all_authorized

        obj, created = models.AgentSession.objects.get_or_create(
            chat_id=chat_id,
            defaults={
                'username': username or '',
                'full_name': full_name or '',
                'is_authorized': expected_auth,
                'role': expected_role,
            }
        )
        if not created:
            update_fields = []
            if obj.is_authorized != expected_auth:
                obj.is_authorized = expected_auth
                update_fields.append('is_authorized')
            if obj.role != expected_role:
                obj.role = expected_role
                update_fields.append('role')
            if username and obj.username != username:
                obj.username = username
                update_fields.append('username')
            if full_name and obj.full_name != full_name:
                obj.full_name = full_name
                update_fields.append('full_name')
            if update_fields:
                obj.save(update_fields=update_fields)

        return AgentSession(
            id=obj.id,
            chat_id=obj.chat_id,
            username=obj.username,
            full_name=obj.full_name,
            is_authorized=obj.is_authorized,
            created_at=obj.created_at,
            last_active_at=obj.last_active_at,
            role=obj.role,
        )

    def update_last_active(self, session_id: int) -> None:
        models.AgentSession.objects.filter(id=session_id).update(last_active_at=timezone.now())


class DjangoMemoryRepository(MemoryRepository):
    RECENT_COUNT = 6    # últimos mensajes siempre incluidos
    SEMANTIC_COUNT = 4  # mensajes relevantes por similitud semántica

    def get_recent(self, session_id: int, limit: int = 10) -> List[AgentMemory]:
        qs = models.AgentMemory.objects.filter(session_id=session_id).order_by('-timestamp')[:limit]
        return [self._to_entity(m) for m in reversed(list(qs))]

    def get_context(self, session_id: int, query: str) -> List[AgentMemory]:
        """
        Contexto híbrido: últimos N mensajes + top K semánticamente relevantes del historial.
        Si no hay embeddings disponibles, cae de vuelta a get_recent.
        """
        # Siempre incluir los mensajes más recientes
        recent_qs = models.AgentMemory.objects.filter(
            session_id=session_id
        ).order_by('-timestamp')[:self.RECENT_COUNT]
        recent = list(reversed(list(recent_qs)))
        recent_ids = {m.id for m in recent}

        # Búsqueda semántica en el historial más antiguo
        semantic = []
        try:
            from core.agent.infrastructure.embedding_service import get_query_embedding
            from pgvector.django import CosineDistance

            query_embedding = get_query_embedding(query)
            if query_embedding:
                semantic_qs = (
                    models.AgentMemory.objects
                    .filter(session_id=session_id, embedding__isnull=False)
                    .exclude(id__in=recent_ids)
                    .annotate(distance=CosineDistance('embedding', query_embedding))
                    .order_by('distance')[:self.SEMANTIC_COUNT]
                )
                semantic = sorted(list(semantic_qs), key=lambda m: m.timestamp)
        except Exception as e:
            pass  # embeddings no disponibles, solo usamos recientes

        # Combinar: semánticos primero (contexto histórico) + recientes
        combined_ids = set()
        result = []
        for m in semantic + recent:
            if m.id not in combined_ids:
                combined_ids.add(m.id)
                result.append(self._to_entity(m))

        return sorted(result, key=lambda m: m.timestamp)

    def save(self, memory: AgentMemory) -> AgentMemory:
        # Generar embedding en background (no bloqueante si falla)
        embedding = None
        try:
            from core.agent.infrastructure.embedding_service import get_embedding
            embedding = get_embedding(memory.content)
        except Exception:
            pass

        obj = models.AgentMemory.objects.create(
            session_id=memory.session_id,
            role=memory.role,
            content=memory.content,
            metadata=memory.metadata,
            embedding=embedding,
        )
        memory.id = obj.id
        memory.timestamp = obj.timestamp
        return memory

    def _to_entity(self, m) -> AgentMemory:
        return AgentMemory(
            id=m.id,
            session_id=m.session_id,
            role=m.role,
            content=m.content,
            timestamp=m.timestamp,
            metadata=m.metadata,
        )


class DjangoRequestRepository(RequestRepository):
    def log(self, request: AgentRequest) -> None:
        models.AgentRequest.objects.create(
            session_id=request.session_id,
            user_message=request.user_message,
            ai_response=request.ai_response,
            model_used=request.model_used,
            tool_used=request.tool_used,
            duration_ms=request.duration_ms,
            estimated_tokens=request.estimated_tokens,
            success=request.success,
            error_message=request.error_message,
        )
