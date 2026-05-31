import uuid
from datetime import datetime
from typing import Dict, Optional, List
from django.utils import timezone

from ..domain.entities import Tenant, Plan, PlanName
from ..domain.repositories import TenantRepository, UsageRecordRepository

class InMemoryTenantRepository(TenantRepository):
    def __init__(self, plans: Dict[str, Plan] = None):
        self._tenants: Dict[uuid.UUID, Tenant] = {}
        self._plans: Dict[str, Plan] = plans or {
            "Free": Plan(name=PlanName.FREE, max_daily_interactions=10, max_monthly_interactions=100),
            "Premium": Plan(name=PlanName.PREMIUM, max_daily_interactions=1000, max_monthly_interactions=10000)
        }

    def save(self, tenant: Tenant):
        self._tenants[tenant.id] = tenant

    def get_by_id(self, tenant_id: uuid.UUID) -> Optional[Tenant]:
        return self._tenants.get(tenant_id)

    def list(self) -> List[Tenant]:
        return list(self._tenants.values())

    def get_plan_by_name(self, plan_name: str) -> Optional[Plan]:
        return self._plans.get(plan_name)

class InMemoryUsageRecordRepository(UsageRecordRepository):
    def __init__(self, timestamps: Optional[List[datetime]] = None):
        self._records: List[Dict] = []
        self._timestamps = timestamps if timestamps is not None else []

    def add_record(self, tenant_id: uuid.UUID, count: int):
        timestamp = self._timestamps.pop(0) if self._timestamps else timezone.now()
        self._records.append({'tenant_id': tenant_id, 'timestamp': timestamp, 'count': count})

    def get_usage_since(self, tenant_id: uuid.UUID, start_date: datetime) -> int:
        # Asegurarse de que start_date sea consciente de la zona horaria si no lo es
        if timezone.is_naive(start_date):
            start_date = timezone.make_aware(start_date, timezone.get_current_timezone())
            
        return sum(
            r['count'] for r in self._records 
            if r['tenant_id'] == tenant_id and r['timestamp'] >= start_date
        )

    def get_monthly_usage_count(self, tenant_id: uuid.UUID, start_date: datetime) -> int:
        return sum(
            r['count'] for r in self._records
            if r['tenant_id'] == tenant_id and
               r['timestamp'].year == start_date.year and
               r['timestamp'].month == start_date.month
        )

MOCK_PLANS = {
    "Free": Plan(name=PlanName.FREE, max_daily_interactions=10, max_monthly_interactions=100),
    "Premium": Plan(name=PlanName.PREMIUM, max_daily_interactions=1000, max_monthly_interactions=10000)
}
