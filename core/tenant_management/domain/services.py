from django.utils import timezone
from .repositories import TenantRepository, UsageRecordRepository
from .entities import Tenant
import uuid
from datetime import timedelta, datetime
from typing import Optional
from core.shared.event_bus import EventBus
from core.shared.events import UsageLimitExceeded
from core.shared.rq_utils import cancel_jobs_for_tenant
import logging

logger = logging.getLogger(__name__)

class UsageTrackingService:
    def __init__(self, tenant_repository: TenantRepository, usage_record_repository: UsageRecordRepository):
        self.tenant_repository = tenant_repository
        self.usage_record_repository = usage_record_repository

    def record_interaction(self, tenant_id: uuid.UUID):
        logger.info(f"Recording interaction for tenant {tenant_id}")
        tenant = self.tenant_repository.get_by_id(tenant_id)
        if not tenant:
            logger.error(f"Tenant not found: {tenant_id}")
            raise ValueError("Tenant not found")

        self.usage_record_repository.add_record(tenant_id=tenant.id, count=1)
        logger.info(f"Successfully recorded interaction for tenant {tenant_id}")

        now = timezone.now()
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

        daily_usage = self.usage_record_repository.get_usage_since(tenant.id, start_of_day)
        monthly_usage = self.usage_record_repository.get_monthly_usage_count(tenant.id, now)
        
        logger.info(f"Current usage for tenant {tenant_id} - Daily: {daily_usage}, Monthly: {monthly_usage}")

        plan = tenant.subscription.plan
        logger.info(f"Plan limits for tenant {tenant_id} - Daily: {plan.max_daily_interactions}, Monthly: {plan.max_monthly_interactions}")

        # Verificar si se ha alcanzado o excedido el límite diario
        if daily_usage >= plan.max_daily_interactions:
            logger.warning(f"Daily limit reached/exceeded for tenant {tenant_id}. Current: {daily_usage}, Limit: {plan.max_daily_interactions}")
            EventBus.publish(UsageLimitExceeded(
                tenant_id=tenant.id,
                limit_type="daily",
                current_usage=daily_usage,
                limit=plan.max_daily_interactions
            ))
            cancel_jobs_for_tenant(tenant.id)
        
        # Verificar si se ha alcanzado o excedido el límite mensual
        if monthly_usage >= plan.max_monthly_interactions:
            logger.warning(f"Monthly limit reached/exceeded for tenant {tenant_id}. Current: {monthly_usage}, Limit: {plan.max_monthly_interactions}")
            EventBus.publish(UsageLimitExceeded(
                tenant_id=tenant.id,
                limit_type="monthly",
                current_usage=monthly_usage,
                limit=plan.max_monthly_interactions
            ))
            cancel_jobs_for_tenant(tenant.id)

    def can_perform_interaction(self, tenant_id: uuid.UUID, now: Optional[datetime] = None) -> bool:
        logger.info(f"Checking if tenant {tenant_id} can perform interaction")
        tenant = self.tenant_repository.get_by_id(tenant_id)
        if not tenant:
            logger.error(f"Tenant not found: {tenant_id}")
            raise ValueError("Tenant not found")

        if now is None:
            now = timezone.now()
        
        start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)

        daily_usage = self.usage_record_repository.get_usage_since(tenant.id, start_of_day)
        monthly_usage = self.usage_record_repository.get_monthly_usage_count(tenant.id, now)
        
        logger.info(f"Current usage for tenant {tenant_id} - Daily: {daily_usage}, Monthly: {monthly_usage}")

        plan = tenant.subscription.plan
        logger.info(f"Plan limits for tenant {tenant_id} - Daily: {plan.max_daily_interactions}, Monthly: {plan.max_monthly_interactions}")

        # Verificar si se ha alcanzado o excedido el límite diario o mensual
        if daily_usage >= plan.max_daily_interactions:
            logger.warning(f"Daily limit reached/exceeded for tenant {tenant_id}. Current: {daily_usage}, Limit: {plan.max_daily_interactions}")
            return False
        
        if monthly_usage >= plan.max_monthly_interactions:
            logger.warning(f"Monthly limit reached/exceeded for tenant {tenant_id}. Current: {monthly_usage}, Limit: {plan.max_monthly_interactions}")
            return False
            
        logger.info(f"Tenant {tenant_id} can perform interaction. Limits not reached.")
        return True
