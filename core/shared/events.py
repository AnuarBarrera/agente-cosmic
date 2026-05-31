import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict

@dataclass
class DomainEvent:
    """
    Clase base para todos los eventos de dominio.
    """
    event_id: uuid.UUID = field(default_factory=uuid.uuid4, init=False)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc), init=False)
    metadata: Dict[str, Any] = field(default_factory=dict, init=False)

# --- Conversation Management Events ---
@dataclass
class ConversationStarted(DomainEvent):
    conversation_id: uuid.UUID
    tenant_id: uuid.UUID
    customer_id: uuid.UUID
    channel_id: uuid.UUID
    message_id: uuid.UUID
    initial_message_content: str


@dataclass
class MessageAdded(DomainEvent):
    """
    Evento que se dispara cuando un nuevo mensaje es añadido a una conversación existente.
    """
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    tenant_id: uuid.UUID
    customer_id: uuid.UUID
    channel_id: uuid.UUID
    content: str
    sender: str
    timestamp: datetime

# --- AI Processing Events ---
@dataclass
class MessageAnalysisCompleted(DomainEvent):
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    tenant_id: uuid.UUID
    analysis_result: Dict[str, Any]
    confidence_score: float
    customer_id: uuid.UUID
    channel_id: uuid.UUID
    original_message_content: str = ""

@dataclass
class ResponseGenerated(DomainEvent):
    conversation_id: uuid.UUID
    tenant_id: uuid.UUID
    response_content: str
    ai_model_used: str
    customer_id: uuid.UUID
    channel_id: uuid.UUID

# --- Channel Integration Events ---
@dataclass
class MessageSentToChannel(DomainEvent):
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    channel_id: uuid.UUID
    tenant_id: uuid.UUID
    delivery_status: str

# --- Tenant Management Events ---
@dataclass
class UsageLimitExceeded(DomainEvent):
    tenant_id: uuid.UUID
    limit_type: str  # e.g., "daily", "monthly"
    current_usage: int
    limit: int

@dataclass
class PlanChanged(DomainEvent):
    tenant_id: uuid.UUID
    old_plan_name: str
    new_plan_name: str

@dataclass
class GeminiApiRateLimitExceeded(DomainEvent):
    tenant_id: uuid.UUID
    error_details: str

# --- Escalation Events ---
@dataclass
class EscalationMessage(DomainEvent):
    tenant_id: uuid.UUID
    recipient: str
    content: str
    subject: str
    escalation_rule_id: uuid.UUID
    original_message_id: uuid.UUID

# --- Saga Control Events ---
@dataclass
class SagaStepCompleted(DomainEvent):
    saga_id: uuid.UUID
    step_name: str
    result: Any

@dataclass
class SagaStepFailed(DomainEvent):
    saga_id: uuid.UUID
    step_name: str
    error: str
