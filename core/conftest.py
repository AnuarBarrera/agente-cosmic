import logging
import pytest


@pytest.fixture(scope='session', autouse=True)
def _silence_llm_audit_log():
    # Evita que las corridas de pytest escriban en el llm_audit.jsonl de produccion
    # (record_tokens() se ejecuta con mocks reales en varios tests y contaminaba
    # el log de auditoria compartido con trafico real).
    llm_audit_logger = logging.getLogger('cosmic.llm_audit')
    llm_audit_logger.handlers = [logging.NullHandler()]
    llm_audit_logger.propagate = False


@pytest.fixture(autouse=True)
def _clear_cache_between_tests():
    # HALLAZGO 79 (hallazgos.txt): TenantRateLimitingMiddleware usa django.core.cache
    # (en memoria, compartido por TODO el proceso de pytest) para contar requests
    # anonimas por IP. Sin este fixture, el contador de rate-limit se acumula entre
    # archivos de test sin relacion entre si a lo largo de las ~600 pruebas de la
    # suite completa, hasta que tests anonimos ajenos empiezan a recibir 429 por
    # trafico acumulado que no es suyo. Limpiar antes de CADA test aisla esa cuenta
    # sin desactivar el rate-limit en los tests que si lo prueban a proposito
    # (ej. test_rate_limiting_for_free_plan espera un 429 real en la request 61).
    from django.core.cache import cache
    cache.clear()
