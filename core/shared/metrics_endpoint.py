import os

from django.http import HttpResponse
from prometheus_client import REGISTRY, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST


def metrics_view(request):
    """
    Endpoint /metrics con autenticación Bearer + soporte multiprocess Gunicorn + Redis.

    Requiere header: Authorization: Bearer <PROMETHEUS_METRICS_TOKEN>
    Si la variable de entorno no está definida (dev local), el acceso es libre.
    """
    expected_token = os.environ.get('PROMETHEUS_METRICS_TOKEN', '')
    if expected_token:
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if auth_header != f'Bearer {expected_token}':
            return HttpResponse('Unauthorized', status=401,
                                content_type='text/plain')

    if 'PROMETHEUS_MULTIPROC_DIR' in os.environ:
        from prometheus_client import multiprocess
        from core.shared.metrics import (
            RQJobsCollector, ActiveUsersCollector, OperationalCollector, RedisMetricsCollector,
        )
        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        registry.register(RQJobsCollector())
        registry.register(ActiveUsersCollector())
        registry.register(OperationalCollector())
        registry.register(RedisMetricsCollector())
    else:
        registry = REGISTRY

    return HttpResponse(generate_latest(registry), content_type=CONTENT_TYPE_LATEST)
