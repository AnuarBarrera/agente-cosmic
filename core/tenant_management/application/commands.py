
import uuid
from dataclasses import dataclass

from ..domain.value_objects import SubscriptionPlan


@dataclass(frozen=True)
class RegisterTenantCommand:
    name: str
    plan: SubscriptionPlan


@dataclass(frozen=True)
class UpdateConfigurationCommand:
    tenant_id: uuid.UUID
    ai_settings: dict
    channel_settings: dict
    routing_rules: list


@dataclass(frozen=True)
class UpdateAIConfigurationCommand:
    tenant_id: uuid.UUID
    ai_settings: dict
