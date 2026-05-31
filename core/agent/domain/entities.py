from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class AgentSession:
    chat_id: int
    username: str
    full_name: str
    is_authorized: bool
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    last_active_at: Optional[datetime] = None
    role: str = 'viewer'


@dataclass
class AgentMemory:
    session_id: int
    role: str  # 'user' | 'assistant'
    content: str
    id: Optional[int] = None
    timestamp: Optional[datetime] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class AgentRequest:
    session_id: int
    user_message: str
    ai_response: str
    model_used: str
    duration_ms: int
    estimated_tokens: int
    success: bool
    tool_used: Optional[str] = None
    error_message: Optional[str] = None
