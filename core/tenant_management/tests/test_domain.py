import pytest
import uuid
from datetime import datetime, timedelta, UTC

from ..domain.entities import Tenant, Subscription, Plan, PlanName, TenantStatus
from ..domain.services import UsageTrackingService
from .mocks import InMemoryTenantRepository, InMemoryUsageRecordRepository

# Pruebas de Dominio
def test_register_tenant():
    plan = Plan(name=PlanName.FREE)
    subscription = Subscription(plan=plan)
    tenant = Tenant.register(name="Test Corp", subscription=subscription)

    assert tenant.name == "Test Corp"
    assert tenant.subscription.plan.name == PlanName.FREE
    assert tenant.id is not None
    assert tenant.status == TenantStatus.ACTIVE

def test_suspend_and_activate_tenant():
    plan = Plan(name=PlanName.FREE)
    subscription = Subscription(plan=plan)
    tenant = Tenant.register(name="Test Corp", subscription=subscription)
    assert tenant.status == TenantStatus.ACTIVE

    tenant.suspend()
    assert tenant.status == TenantStatus.SUSPENDED

    tenant.activate()
    assert tenant.status == TenantStatus.ACTIVE

# Pruebas del Servicio de Dominio
@pytest.fixture
def usage_service():
    tenant_repo = InMemoryTenantRepository()
    usage_repo = InMemoryUsageRecordRepository()
    
    # Crear un tenant de prueba
    plan = tenant_repo.get_plan_by_name("Free")
    subscription = Subscription(plan=plan)
    tenant = Tenant.register(name="Test Tenant", subscription=subscription)
    tenant_repo.save(tenant)
    
    return UsageTrackingService(tenant_repo, usage_repo), tenant.id

def test_record_interaction(usage_service):
    service, tenant_id = usage_service
    service.record_interaction(tenant_id)
    
    usage_repo = service.usage_record_repository
    assert usage_repo.get_usage_since(tenant_id, datetime.now(UTC) - timedelta(days=1)) == 1

def test_has_exceeded_daily_limits(usage_service):
    service, tenant_id = usage_service
    
    # El plan Free tiene 10 interacciones diarias. Hacemos 10.
    for _ in range(10):
        service.record_interaction(tenant_id)
        
    # Ahora ya no debería poder interactuar
    assert not service.can_perform_interaction(tenant_id)

from unittest.mock import patch
from django.utils import timezone

def test_has_exceeded_monthly_limits():
    # Setup
    tenant_repo = InMemoryTenantRepository()
    plan = tenant_repo.get_plan_by_name("Free")
    subscription = Subscription(plan=plan)
    tenant = Tenant.register(name="Test Tenant", subscription=subscription)
    tenant_repo.save(tenant)
    tenant_id = tenant.id

    usage_repo = InMemoryUsageRecordRepository()
    service = UsageTrackingService(tenant_repo, usage_repo)

    # Simular 100 interacciones en el mismo mes, con diferentes horas
    base_date = datetime(2025, 7, 1, 0, 0, 0, tzinfo=UTC) # Inicio del mes
    for i in range(100):
        with patch('django.utils.timezone.now', return_value=base_date + timedelta(hours=i)):
            service.record_interaction(tenant_id)
    
    # Ahora, después de 100 interacciones (que es el límite), ya no debería poder interactuar
    with patch('django.utils.timezone.now', return_value=base_date + timedelta(hours=99)):
        assert not service.can_perform_interaction(tenant_id)
