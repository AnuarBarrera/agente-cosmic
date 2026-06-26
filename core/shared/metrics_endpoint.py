import os

from django.http import HttpResponse
from prometheus_client import REGISTRY, CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST


def metrics_view(request):
    """
    Endpoint /metrics con soporte multiprocess para Gunicorn + agregación Redis para rqworkers.

    - En modo multiprocess (PROMETHEUS_MULTIPROC_DIR set): usa MultiProcessCollector para
      agregar todos los workers Gunicorn del mismo contenedor, más colectores de BD y Redis.
    - En modo single-process (dev): usa el REGISTRY global directamente.
    """
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
