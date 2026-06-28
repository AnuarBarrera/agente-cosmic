import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any

class PlanName(Enum):
    FREE = "User"
    USER = "User"
    PREMIUM = "Premium"

class TenantStatus(Enum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"

@dataclass
class Plan:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: PlanName = PlanName.FREE
    max_daily_interactions: int = 0
    max_monthly_interactions: int = 0
    price: float = 0.0

@dataclass
class Subscription:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    plan: Plan = field(default_factory=Plan)
    start_date: datetime = field(default_factory=datetime.now)
    end_date: Optional[datetime] = None
    status: str = "active"

@dataclass
class TenantConfiguration:
    config_id: uuid.UUID = field(default_factory=uuid.uuid4)
    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    ai_settings: Dict[str, Any] = field(default_factory=dict)
    channel_settings: Dict[str, Any] = field(default_factory=dict)
    routing_rules: list = field(default_factory=list)

    def validate(self):
        if not isinstance(self.ai_settings, dict):
            raise ValueError("AI settings must be a dictionary.")
        if not isinstance(self.channel_settings, dict):
            raise ValueError("Channel settings must be a dictionary.")
        if not isinstance(self.routing_rules, list):
            raise ValueError("Routing rules must be a list.")

    def to_dict(self):
        return {
            "config_id": str(self.config_id),
            "tenant_id": str(self.tenant_id),
            "ai_settings": self.ai_settings,
            "channel_settings": self.channel_settings,
            "routing_rules": self.routing_rules,
        }

@dataclass
class Tenant:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    name: str = ""
    status: TenantStatus = TenantStatus.ACTIVE
    subscription: Optional[Subscription] = None
    configuration: TenantConfiguration = field(default_factory=TenantConfiguration)
    created_at: datetime = field(default_factory=datetime.now)

    @staticmethod
    def register(name: str, subscription: Subscription) -> "Tenant":
        tenant = Tenant(name=name, subscription=subscription)
        tenant.configuration = TenantConfiguration(tenant_id=tenant.id)
        return tenant

    def change_subscription(self, new_plan: Plan):
        # Lógica de negocio: al cambiar de plan, se actualiza el plan de la suscripción existente.
        if self.subscription:
            self.subscription.plan = new_plan
            self.subscription.status = "active"
        else:
            # Si por alguna razón no existe una suscripción, se crea una nueva.
            self.subscription = Subscription(plan=new_plan)

    def suspend(self):
        if self.status == TenantStatus.ACTIVE:
            self.status = TenantStatus.SUSPENDED

    def activate(self):
        if self.status == TenantStatus.SUSPENDED:
            self.status = TenantStatus.ACTIVE

    def update_configuration(self, new_config: TenantConfiguration):
        new_config.validate()
        self.configuration = new_config

    def to_dict(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "status": self.status.value,
            "subscription": {
                "id": str(self.subscription.id),
                "plan": {
                    "id": str(self.subscription.plan.id),
                    "name": self.subscription.plan.name.value,
                    "max_daily_interactions": self.subscription.plan.max_daily_interactions,
                    "max_monthly_interactions": self.subscription.plan.max_monthly_interactions,
                    "price": float(self.subscription.plan.price),
                },
                "start_date": self.subscription.start_date.isoformat(),
                "end_date": self.subscription.end_date.isoformat() if self.subscription.end_date else None,
                "status": self.subscription.status,
            } if self.subscription else None,
            "configuration": self.configuration.to_dict(),
            "created_at": self.created_at.isoformat(),
        }

@dataclass
class UsageRecord:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    timestamp: datetime = field(default_factory=datetime.now)
    interaction_count: int = 1

@dataclass
class ErrorInteraction:
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    tenant_id: uuid.UUID = field(default_factory=uuid.uuid4)
    error_type: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)