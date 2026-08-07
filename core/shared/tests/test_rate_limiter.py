from unittest.mock import patch
import pytest
from core.shared import rate_limiter


class FakeRedis:
    def __init__(self):
        self.store = {}

    def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def decr(self, key):
        self.store[key] = self.store.get(key, 0) - 1
        return self.store[key]

    def expire(self, key, seconds):
        pass

    def get(self, key):
        val = self.store.get(key)
        return str(val).encode() if val is not None else None


@pytest.fixture
def fake_redis():
    conn = FakeRedis()
    with patch('django_rq.get_connection', return_value=conn):
        yield conn


def test_base_model_strips_version_suffix():
    assert rate_limiter._base_model('imagen-3.0-generate-001') == 'imagen-3.0-generate'
    assert rate_limiter._base_model('imagen-3.0-capability-001') == 'imagen-3.0-capability'


def test_throttle_noop_for_model_without_known_limit(fake_redis):
    # gemini-2.5-flash usa DSQ — no debe tocar Redis en absoluto
    rate_limiter.throttle('publishers/google/models/gemini-2.5-flash')
    assert fake_redis.store == {}


def test_throttle_allows_calls_within_limit(fake_redis):
    with patch.dict(rate_limiter.RPM_LIMITS, {'test-model-generate': 20}):
        for _ in range(20):
            rate_limiter.throttle('test-model-generate-001')
        key = rate_limiter._minute_key('test-model-generate')
        assert fake_redis.store[key] == 20


@patch('core.shared.rate_limiter.time.sleep')
def test_throttle_waits_when_over_limit(mock_sleep, fake_redis):
    with patch.dict(rate_limiter.RPM_LIMITS, {'test-model-generate': 20}):
        key = rate_limiter._minute_key('test-model-generate')
        for _ in range(20):
            rate_limiter.throttle('test-model-generate-001')
        mock_sleep.assert_not_called()

        # Simula que la ventana del minuto expiró mientras "esperábamos" — evita
        # un loop infinito en el test, ya que time.sleep está mockeado (no avanza el reloj real).
        mock_sleep.side_effect = lambda *a, **k: fake_redis.store.pop(key, None)

        # La petición 21 excede el límite de 20/min — debe esperar antes de continuar
        rate_limiter.throttle('test-model-generate-001')
        mock_sleep.assert_called_once()
        assert fake_redis.store[key] == 1  # la ventana se reinició, este es el primer conteo


def test_diagnose_429_confirms_when_over_limit(fake_redis):
    with patch.dict(rate_limiter.RPM_LIMITS, {'test-model-generate': 20}):
        key = rate_limiter._minute_key('test-model-generate')
        fake_redis.store[key] = 20
        msg = rate_limiter.diagnose_429('test-model-generate-001')
        assert 'CONFIRMADO' in msg


def test_diagnose_429_rules_out_when_under_limit(fake_redis):
    with patch.dict(rate_limiter.RPM_LIMITS, {'test-model-generate': 20}):
        key = rate_limiter._minute_key('test-model-generate')
        fake_redis.store[key] = 3
        msg = rate_limiter.diagnose_429('test-model-generate-001')
        assert 'CONFIRMADO' not in msg
        assert 'no se explica' in msg


def test_diagnose_429_unknown_model_reports_dsq(fake_redis):
    msg = rate_limiter.diagnose_429('publishers/google/models/gemini-2.5-flash')
    assert 'DSQ' in msg


@patch('core.shared.rate_limiter.time.sleep')
def test_call_with_429_retry_succeeds_after_transient_429s(mock_sleep, fake_redis):
    calls = {'n': 0}

    def flaky():
        calls['n'] += 1
        if calls['n'] < 3:
            raise Exception('429 RESOURCE_EXHAUSTED')
        return 'ok'

    result = rate_limiter.call_with_429_retry(flaky, 'imagen-3.0-generate-001', max_retries=3)
    assert result == 'ok'
    assert calls['n'] == 3
    assert mock_sleep.call_count == 2


@patch('core.shared.rate_limiter.time.sleep')
def test_call_with_429_retry_raises_after_exhausting_retries(mock_sleep, fake_redis):
    def always_429():
        raise Exception('429 RESOURCE_EXHAUSTED')

    with pytest.raises(Exception, match='429'):
        rate_limiter.call_with_429_retry(always_429, 'imagen-3.0-generate-001', max_retries=2)
    assert mock_sleep.call_count == 1


def test_call_with_429_retry_does_not_retry_other_errors(fake_redis):
    calls = {'n': 0}

    def broken():
        calls['n'] += 1
        raise ValueError('algo distinto a rate limit')

    with pytest.raises(ValueError):
        rate_limiter.call_with_429_retry(broken, 'imagen-3.0-generate-001', max_retries=3)
    assert calls['n'] == 1
