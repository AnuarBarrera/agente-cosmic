# core/tenant_management/infrastructure/__init__.py
# Este archivo asegura que los manejadores de eventos se registren al iniciar la aplicación

from . import event_handlers

# Mantener una referencia para evitar que el módulo sea recolectado por el garbage collector
_event_handlers = event_handlers

# Asegurarse de que el manejador se registre
from .event_handlers import handle_usage_limit_exceeded