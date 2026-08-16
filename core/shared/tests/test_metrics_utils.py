from unittest.mock import patch
import pytest
from core.shared import metrics_utils


class FakeRedis:
    """Mismo patron de doble de Redis que test_rate_limiter.py — aqui solo hace
    falta incrbyfloat, que es lo unico que usa _redis_inc."""

    def __init__(self):
        self.store = {}

    def incrbyfloat(self, key, amount):
        self.store[key] = self.store.get(key, 0) + amount
        return self.store[key]


@pytest.fixture
def fake_redis():
    conn = FakeRedis()
    with patch('django_rq.get_connection', return_value=conn):
        yield conn


def test_record_gemini_image_generation_uses_explicit_cost(fake_redis):
    metrics_utils.record_gemini_image_generation('x', cost_per_image=100)
    assert fake_redis.store['cosmic:prom:IC:x'] == 100
    assert fake_redis.store['cosmic:prom:IC:x'] != metrics_utils._GEMINI_IMAGE_COST_PER_IMAGE
    assert fake_redis.store['cosmic:prom:I:x'] == 1


def test_record_gemini_image_generation_defaults_to_normal_model_price(fake_redis):
    metrics_utils.record_gemini_image_generation('generate')
    assert fake_redis.store['cosmic:prom:IC:generate'] == metrics_utils._GEMINI_IMAGE_COST_PER_IMAGE
    assert fake_redis.store['cosmic:prom:I:generate'] == 1


def test_lite_image_cost_is_cheaper_than_normal_model():
    """El modelo lite existe justamente para medir costo antes de escalar —
    contabilizarlo a la tarifa del modelo normal anula el proposito."""
    assert metrics_utils._GEMINI_LITE_IMAGE_COST_PER_IMAGE < metrics_utils._GEMINI_IMAGE_COST_PER_IMAGE
