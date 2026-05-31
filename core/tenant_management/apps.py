from django.apps import AppConfig
from core.shared.event_bus import EventBus, register_handler
from core.shared.events import MessageSentToChannel, UsageLimitExceeded


class TenantManagementConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core.tenant_management"

    def ready(self):
        from .application.event_handlers import TenantEventHandlers
        handlers = TenantEventHandlers()
        register_handler(MessageSentToChannel, handlers.handle_message_sent)
        
        # Registrar manejador para eventos de límite de uso excedido
        from .infrastructure.event_handlers import handle_usage_limit_exceeded
        register_handler(UsageLimitExceeded, handle_usage_limit_exceeded)
