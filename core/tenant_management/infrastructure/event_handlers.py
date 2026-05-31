import logging
from core.shared.event_bus import register_handler
from core.shared.events import UsageLimitExceeded

logger = logging.getLogger(__name__)

def handle_usage_limit_exceeded(event: UsageLimitExceeded):
    """
    Manejador para el evento UsageLimitExceeded.
    """
    logger.info(
        f"Usage limit exceeded for tenant {event.tenant_id}: "
        f"{event.current_usage}/{event.limit} ({event.limit_type})"
    )
    print(f"[USAGE_LIMIT] Tenant {event.tenant_id} has exceeded {event.limit_type} limit: "
          f"{event.current_usage}/{event.limit}")

# Registrar el manejador
register_handler(UsageLimitExceeded, handle_usage_limit_exceeded)