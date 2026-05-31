from core.tenant_management.domain.entities import Tenant, TenantConfiguration
from core.tenant_management.domain.value_objects import SubscriptionPlan, UsageLimits
from typing import Dict, Any

class TenantAggregate:
    def __init__(self, tenant: Tenant):
        self._tenant = tenant

    @property
    def tenant(self) -> Tenant:
        return self._tenant

    def register_tenant(self, name: str, initial_plan: SubscriptionPlan, initial_config: Dict[str, Any]):
        print(f"Registrando nuevo tenant: {name} con plan {initial_plan.name}")
        self._tenant.name = name
        self._tenant.plan = initial_plan.name
        self._tenant.usage_limits = initial_plan.usage_limits
        self._tenant.configuration = initial_config
        # Regla de negocio: Tenant debe tener plan de suscripción válido

    def update_configuration(self, new_config: Dict[str, Any]):
        print(f"Actualizando configuración para tenant {self._tenant.tenant_id}")
        self._tenant.configure(new_config)
        # Regla de negocio: Configuración debe ser válida para el plan contratado

    def upgrade_plan(self, new_plan: SubscriptionPlan):
        print(f"Actualizando plan de tenant {self._tenant.tenant_id} a {new_plan.name}")
        self._tenant.upgrade_plan(new_plan.name, new_plan.usage_limits)

    def enforce_usage_limits(self, current_usage: Dict[str, Any]) -> bool:
        # Regla de negocio: Aplicar límites según plan de suscripción
        return self._tenant.monitor_usage(current_usage)

    def auto_upgrade_suggestions(self, current_usage: Dict[str, Any]):
        # Regla de negocio: Sugerir upgrade cuando se acerca a límites
        for limit_type, limit_value in self._tenant.usage_limits.items():
            if current_usage.get(limit_type, 0) > limit_value * 0.8: # Uso > 80% del límite
                print(f"[Sugerencia] Tenant {self._tenant.tenant_id} se acerca al límite de {limit_type}.")
                # Aquí se publicaría un evento para notificar al tenant

    def graceful_degradation(self, current_usage: Dict[str, Any]):
        # Regla de negocio: Degradar servicio antes de cortar completamente
        if not self.enforce_usage_limits(current_usage):
            print(f"[Degradación] Tenant {self._tenant.tenant_id} ha excedido límites. Degradando servicio.")
            # Lógica para reducir features premium

class TenantConfigurationAggregate:
    def __init__(self, tenant_config: TenantConfiguration):
        self._tenant_config = tenant_config

    @property
    def tenant_config(self) -> TenantConfiguration:
        return self._tenant_config

    def validate_changes(self) -> bool:
        # Regla de negocio: Validar cambios de configuración contra plan
        return self._tenant_config.validate()

    def apply_configuration(self):
        self._tenant_config.apply()

    def rollback_configuration(self):
        self._tenant_config.rollback()
