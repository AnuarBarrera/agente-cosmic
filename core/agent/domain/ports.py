from abc import ABC, abstractmethod
from typing import List, Optional
from .entities import AgentSession, AgentMemory, AgentRequest


class SessionRepository(ABC):
    @abstractmethod
    def get_or_create(self, chat_id: int, username: str, full_name: str) -> AgentSession:
        pass

    @abstractmethod
    def update_last_active(self, session_id: int) -> None:
        pass


class MemoryRepository(ABC):
    @abstractmethod
    def get_recent(self, session_id: int, limit: int = 10) -> List[AgentMemory]:
        pass

    @abstractmethod
    def save(self, memory: AgentMemory) -> AgentMemory:
        pass


class RequestRepository(ABC):
    @abstractmethod
    def log(self, request: AgentRequest) -> None:
        pass
