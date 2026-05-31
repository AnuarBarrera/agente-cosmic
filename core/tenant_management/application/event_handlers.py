from core.shared.event_bus import EventBus
from core.shared.events import MessageSentToChannel
from .services import TenantApplicationService
from ..infrastructure.repositories import DjangoTenantRepository

class TenantEventHandlers:
    def __init__(self):
        # For simplicity, we instantiate dependencies directly.
        # In a more complex system, dependency injection would be used.
        self.tenant_repository = DjangoTenantRepository()
        self.tenant_service = TenantApplicationService(self.tenant_repository)

    def handle_message_sent(self, event: MessageSentToChannel):
        """
        Handles the MessageSentToChannel event by recording the interaction.
        """
        self.tenant_service.record_interaction(event.tenant_id)

def register_tenant_event_handlers():
    """
    Registers all tenant-related event handlers to the event bus.
    """
    handlers = TenantEventHandlers()
    EventBus.subscribe(MessageSentToChannel, handlers.handle_message_sent)