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
def _pin_free_tier_gemini_flag(settings):
    # HALLAZGO 2026-08-30: settings.FREE_TIER_USES_GEMINI_API (ventana temporal
    # IME, ver saas_chatbot/settings.py) se lee directo del entorno del proceso.
    # Si el contenedor donde corre pytest ya tiene esa env var en True (como en
    # produccion durante la ventana), ~20 tests que instancian ImageGenerator/
    # ReelGenerator sin pasar use_gemini_api y solo mockean _vertex_client
    # empiezan a golpear la API real de Gemini (sin mock), con 400 reales o
    # 200 reales que no matchean los bytes/asserts esperados -- 22 tests caidos
    # en produccion (reportado por Anuar) que en dev pasaban limpio porque ahi
    # la env var seguia en False. Los tests no deben depender del entorno
    # ambiente del proceso; se fija aqui a False por default para que el
    # comportamiento sea deterministico sin importar la env var real del
    # contenedor. Los tests que SI quieren probar el camino en True lo activan
    # explicito con override_settings/settings fixture en su propio scope.
    settings.FREE_TIER_USES_GEMINI_API = False


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
