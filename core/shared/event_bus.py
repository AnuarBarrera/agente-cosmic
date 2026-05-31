
import django_rq
from typing import Dict, Type, Callable, List
from .events import DomainEvent
import logging

logger = logging.getLogger(__name__)

# Registro de manejadores de eventos
_handlers: Dict[Type[DomainEvent], List[Callable]] = {}

def register_handler(event_type: Type[DomainEvent], handler: Callable):
    """Registra un manejador para un tipo de evento específico."""
    if event_type not in _handlers:
        _handlers[event_type] = []
    _handlers[event_type].append(handler)
    logger.info(f"Handler {handler.__name__} registered for event {event_type.__name__}")

def get_handlers(event_type: Type[DomainEvent]) -> List[Callable]:
    """Obtiene los manejadores para un tipo de evento."""
    return _handlers.get(event_type, [])

class _EventBus:
    def publish(self, event: DomainEvent):
        """
        Publica un evento, despachándolo a todos los manejadores registrados
        de forma síncrona.
        """
        event_type = type(event)
        handlers = get_handlers(event_type)
        print(f"[DEBUG] EventBus.publish: Handlers found for {event_type.__name__}: {[h.__name__ for h in handlers]}")
        logger.info(f"EventBus.publish: Processing event of type {event_type.__name__}. Found handlers: {[h.__name__ for h in handlers]}")
        if not handlers:
            logger.warning(f"No handlers registered for event {event_type.__name__}")
            return

        logger.info(f"Publishing event {event_type.__name__} with ID {event.event_id}")
        for handler in handlers:
            try:
                logger.info(f"Calling handler {handler.__name__} for event {event_type.__name__}")
                handler(event)
                logger.info(f"Handler {handler.__name__} executed successfully.")
            except Exception as e:
                logger.error(f"Error executing handler {handler.__name__} for event {event_type.__name__}: {e}", exc_info=True)

class EventBus:
    _instance = _EventBus()

    @classmethod
    def publish(cls, event: DomainEvent):
        cls._instance.publish(event)

    @classmethod
    def set_instance(cls, instance):
        """Establece una instancia de bus de eventos (para pruebas)."""
        cls._instance = instance

