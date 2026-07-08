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
