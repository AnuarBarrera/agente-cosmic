import abc
import uuid
from typing import Optional
from datetime import datetime

from .entities import Tenant, UsageRecord, Plan, Subscription, ErrorInteraction


class TenantRepository(abc.ABC):
    @abc.abstractmethod
    def save(self, tenant: Tenant) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def get_by_id(self, tenant_id: uuid.UUID) -> Optional[Tenant]:
        raise NotImplementedError

    @abc.abstractmethod
    def list(self) -> list[Tenant]:
        raise NotImplementedError

    @abc.abstractmethod
    def get_plan_by_name(self, plan_name: str) -> Optional[Plan]:
        raise NotImplementedError

class UsageRecordRepository(abc.ABC):
    @abc.abstractmethod
    def add_record(self, tenant_id: uuid.UUID, count: int) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def get_usage_since(self, tenant_id: uuid.UUID, start_date: datetime) -> int:
        raise NotImplementedError

    @abc.abstractmethod
    def get_monthly_usage_count(self, tenant_id: uuid.UUID, start_date: datetime) -> int:
        raise NotImplementedError

class ErrorInteractionRepository(abc.ABC):
    @abc.abstractmethod
    def save(self, error_interaction: ErrorInteraction) -> None:
        raise NotImplementedError

    @abc.abstractmethod
    def get_error_count_since(self, tenant_id: uuid.UUID, start_date: datetime) -> int:
        raise NotImplementedError