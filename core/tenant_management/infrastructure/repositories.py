import uuid
from typing import Optional
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

from ..domain.entities import Tenant, TenantConfiguration, TenantStatus, Plan, Subscription, UsageRecord, PlanName, ErrorInteraction
from ..domain.repositories import TenantRepository, UsageRecordRepository, ErrorInteractionRepository
from ..models import TenantModel, TenantConfigurationModel, Plan as PlanModel, Subscription as SubscriptionModel, UsageRecord as UsageRecordModel
from .models import ErrorInteractionModel

class DjangoTenantRepository(TenantRepository):
    def save(self, tenant: Tenant) -> None:
        # Guardar o actualizar el Plan
        if tenant.subscription and tenant.subscription.plan:
            plan_model, _ = PlanModel.objects.update_or_create(
                name=tenant.subscription.plan.name.value,
                defaults={
                    'max_daily_interactions': tenant.subscription.plan.max_daily_interactions,
                    'max_monthly_interactions': tenant.subscription.plan.max_monthly_interactions,
                    'price': tenant.subscription.plan.price,
                }
            )
        
        # Guardar o actualizar el Tenant
        tenant_model, _ = TenantModel.objects.update_or_create(
            id=tenant.id,
            defaults={
                "name": tenant.name,
                "status": tenant.status.value,
            },
        )

        # Guardar o actualizar la Suscripción
        if tenant.subscription:
            SubscriptionModel.objects.update_or_create(
                tenant=tenant_model,
                defaults={
                    'id': tenant.subscription.id,
                    'plan': plan_model,
                    'start_date': tenant.subscription.start_date,
                    'end_date': tenant.subscription.end_date,
                    'status': tenant.subscription.status,
                }
            )

        # Guardar o actualizar la Configuración del Tenant
        TenantConfigurationModel.objects.update_or_create(
            tenant=tenant_model,
            defaults={
                "id": tenant.configuration.config_id,
                "ai_settings": tenant.configuration.ai_settings,
                "channel_settings": tenant.configuration.channel_settings,
                "routing_rules": tenant.configuration.routing_rules,
            },
        )

    def get_by_id(self, tenant_id: uuid.UUID) -> Optional[Tenant]:
        try:
            tenant_model = TenantModel.objects.select_related('subscription__plan', 'configuration').get(id=tenant_id)
            return self._to_domain(tenant_model)
        except TenantModel.DoesNotExist:
            return None

    # Alias for compatibility with AI processing context
    find_by_id = get_by_id

    def list(self) -> list[Tenant]:
        tenant_models = TenantModel.objects.select_related('subscription__plan', 'configuration').all()
        return [self._to_domain(model) for model in tenant_models]

    def get_plan_by_name(self, plan_name: str) -> Optional[Plan]:
        try:
            # Try exact match first
            try:
                plan_model = PlanModel.objects.get(name=plan_name)
            except PlanModel.DoesNotExist:
                # Try case-insensitive search for common variations
                if plan_name.lower() in ['free', 'user']:
                    # Try different capitalizations of "user" (formerly "free")
                    for variation in ['User', 'user', 'USER']:
                        try:
                            plan_model = PlanModel.objects.get(name=variation)
                            break
                        except PlanModel.DoesNotExist:
                            continue
                    else:
                        return None
                elif plan_name.lower() in ['premium', 'pro']:
                    # Try different capitalizations of "premium"
                    for variation in ['PREMIUM', 'Premium', 'premium', 'PRO', 'Pro', 'pro']:
                        try:
                            plan_model = PlanModel.objects.get(name=variation)
                            break
                        except PlanModel.DoesNotExist:
                            continue
                    else:
                        return None
                else:
                    return None
            
            return Plan(
                id=plan_model.id,
                name=self._map_plan_name(plan_model.name),
                max_daily_interactions=plan_model.max_daily_interactions,
                max_monthly_interactions=plan_model.max_monthly_interactions,
                price=plan_model.price
            )
        except Exception:
            return None

    def _to_domain(self, tenant_model: TenantModel) -> Tenant:
        try:
            config_model = tenant_model.configuration
            configuration = TenantConfiguration(
                config_id=config_model.id,
                tenant_id=tenant_model.id,
                ai_settings=config_model.ai_settings,
                channel_settings=config_model.channel_settings,
                routing_rules=config_model.routing_rules,
            )
        except TenantModel.configuration.RelatedObjectDoesNotExist:
            # Create default configuration if none exists
            from ..models import TenantConfigurationModel
            config_model = TenantConfigurationModel.objects.create(
                tenant=tenant_model,
                ai_settings={},
                channel_settings={},
                routing_rules={}
            )
            configuration = TenantConfiguration(
                config_id=config_model.id,
                tenant_id=tenant_model.id,
                ai_settings={},
                channel_settings={},
                routing_rules={},
            )

        subscription = None
        if hasattr(tenant_model, 'subscription'):
            sub_model = tenant_model.subscription
            plan_model = sub_model.plan
            plan = Plan(
                id=plan_model.id,
                name=self._map_plan_name(plan_model.name),
                max_daily_interactions=plan_model.max_daily_interactions,
                max_monthly_interactions=plan_model.max_monthly_interactions,
                price=plan_model.price
            )
            subscription = Subscription(
                id=sub_model.id,
                plan=plan,
                start_date=sub_model.start_date,
                end_date=sub_model.end_date,
                status=sub_model.status
            )

        return Tenant(
            id=tenant_model.id,
            name=tenant_model.name,
            status=TenantStatus(tenant_model.status),
            configuration=configuration,
            subscription=subscription,
            created_at=tenant_model.created_at,
        )
    
    def _map_plan_name(self, plan_name_str: str) -> PlanName:
        """Map plan name string to PlanName enum, handling different capitalizations"""
        # Normalize the string to handle different cases
        normalized = plan_name_str.lower()
        
        if normalized in ['free', 'user']:
            return PlanName.USER
        elif normalized in ['premium', 'pro']:
            return PlanName.PREMIUM
        else:
            # Try direct mapping as fallback
            try:
                return PlanName(plan_name_str)
            except ValueError:
                # Default to USER if unknown
                return PlanName.USER

class DjangoUsageRecordRepository(UsageRecordRepository):
    def add_record(self, tenant_id: uuid.UUID, count: int) -> None:
        print(f"[DEBUG] add_record called for tenant_id: {tenant_id} with count: {count}")
        logger.info(f"Attempting to add usage record for tenant_id: {tenant_id} with count: {count}")
        logger.info(f"Attempting to retrieve TenantModel with ID: {tenant_id}")
        try:
            tenant_model = TenantModel.objects.get(id=tenant_id)
            print(f"[DEBUG] TenantModel found: {tenant_model.id}. Creating UsageRecord.")
            logger.info(f"TenantModel found: {tenant_model.id}. Creating UsageRecord.")
            from django.db import transaction
            with transaction.atomic():
                UsageRecordModel.objects.create(tenant=tenant_model, interaction_count=count)
            print(f"[DEBUG] Successfully added usage record for tenant_id: {tenant_id}")
            logger.info(f"Successfully added usage record for tenant_id: {tenant_id}")
        except TenantModel.DoesNotExist:
            print(f"[DEBUG] TenantModel with ID {tenant_id} not found when adding usage record.")
            logger.error(f"TenantModel with ID {tenant_id} not found when adding usage record. This tenant ID was passed: {tenant_id}")
        except Exception as e:
            print(f"[DEBUG] Error adding usage record for tenant_id {tenant_id}: {e}")
            logger.error(f"Error adding usage record for tenant_id {tenant_id}: {e}", exc_info=True)

    def get_usage_since(self, tenant_id: uuid.UUID, start_date: datetime) -> int:
        from django.db.models import Sum
        logger.info(f"Getting usage since {start_date} for tenant {tenant_id}")
        usage = UsageRecordModel.objects.filter(
            tenant_id=tenant_id, 
            timestamp__gte=start_date
        ).aggregate(total=Sum('interaction_count'))['total']
        result = usage or 0
        logger.info(f"Usage since {start_date} for tenant {tenant_id}: {result}")
        return result

    def get_monthly_usage_count(self, tenant_id: uuid.UUID, reference_date: datetime) -> int:
        from django.db.models import Sum
        from datetime import datetime
        
        # Calcular el primer día del mes de referencia
        start_of_month = reference_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        logger.info(f"Getting monthly usage for tenant {tenant_id} for month {start_of_month.year}-{start_of_month.month}")
        usage = UsageRecordModel.objects.filter(
            tenant_id=tenant_id,
            timestamp__year=start_of_month.year,
            timestamp__month=start_of_month.month
        ).aggregate(total=Sum('interaction_count'))['total']
        result = usage or 0
        logger.info(f"Monthly usage for tenant {tenant_id} for month {start_of_month.year}-{start_of_month.month}: {result}")
        return result

class DjangoErrorInteractionRepository(ErrorInteractionRepository):
    def save(self, error_interaction: ErrorInteraction) -> None:
        try:
            tenant_model = TenantModel.objects.get(id=error_interaction.tenant_id)
            ErrorInteractionModel.objects.create(
                tenant=tenant_model,
                error_type=error_interaction.error_type,
                details=error_interaction.details
            )
        except TenantModel.DoesNotExist:
            logger.error(f"Tenant with ID {error_interaction.tenant_id} not found when saving error interaction.")
            raise ValueError(f"Tenant with ID {error_interaction.tenant_id} not found.")

    def get_error_count_since(self, tenant_id: uuid.UUID, start_date: datetime) -> int:
        from django.db.models import Sum
        error_count = ErrorInteractionModel.objects.filter(
            tenant_id=tenant_id,
            timestamp__gte=start_date
        ).count()
        return error_count or 0

class DjangoSubscriptionRepository:
    def get_by_tenant_id(self, tenant_id: uuid.UUID) -> Optional[SubscriptionModel]:
        try:
            return SubscriptionModel.objects.select_related('plan').get(tenant_id=tenant_id)
        except SubscriptionModel.DoesNotExist:
            return None

class DjangoPlanRepository:
    def list_all(self) -> list[PlanModel]:
        return list(PlanModel.objects.all())