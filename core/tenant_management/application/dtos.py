import uuid
from dataclasses import dataclass

from ..domain.value_objects import SubscriptionPlan


@dataclass(frozen=True)
class TenantDTO:
    tenant_id: uuid.UUID
    name: str
    plan: str
    status: str
    configuration: dict


@dataclass(frozen=True)
class TenantConfigurationDTO:
    ai_settings: dict
    channel_settings: dict
    routing_rules: list

@dataclass(frozen=True)
class UsageRecordDTO:
    total_daily: int
    total_monthly: int


@dataclass(frozen=True)
class DashboardDataDTO:
    total_conversations: int
    resolved_cases: int
    avg_response_time: str
    automation_rate: float
    response_rate: str
    conversations_by_channel: dict
    recent_activity: list

