import pytest
from unittest.mock import patch
from core.shared.metrics import (
    ANALYSIS_JOBS_TOTAL,
    ANALYSIS_DURATION,
    EXTERNAL_API_REQUESTS,
    CALENDARS_CREATED,
)
from core.shared.metrics_utils import track_external_api, record_tokens


@pytest.mark.django_db
class TestMetricsDefinitions:
    def test_counter_increments(self):
        before = CALENDARS_CREATED._value.get()
        CALENDARS_CREATED.inc()
        assert CALENDARS_CREATED._value.get() == before + 1

    def test_counter_with_labels(self):
        ANALYSIS_JOBS_TOTAL.labels(status='completed').inc()
        val = ANALYSIS_JOBS_TOTAL.labels(status='completed')._value.get()
        assert val >= 1

    def test_histogram_observes(self):
        ANALYSIS_DURATION.observe(5.0)
        assert ANALYSIS_DURATION._sum.get() >= 5.0


class TestTrackExternalApi:
    def test_success_increments_counter(self):
        before = EXTERNAL_API_REQUESTS.labels(service='test_svc', status='success')._value.get()
        with track_external_api('test_svc'):
            pass
        after = EXTERNAL_API_REQUESTS.labels(service='test_svc', status='success')._value.get()
        assert after == before + 1

    def test_error_increments_error_counter(self):
        before = EXTERNAL_API_REQUESTS.labels(service='test_err', status='error')._value.get()
        with pytest.raises(ValueError):
            with track_external_api('test_err'):
                raise ValueError('test error')
        after = EXTERNAL_API_REQUESTS.labels(service='test_err', status='error')._value.get()
        assert after == before + 1

    def test_timeout_classified(self):
        with pytest.raises(TimeoutError):
            with track_external_api('test_timeout'):
                raise TimeoutError('connection timed out')


class TestRecordTokens:
    def test_records_tokens_via_redis(self):
        """record_tokens escribe en Redis — verifica que la clave correcta se incrementa."""
        class FakeUsage:
            prompt_token_count = 100
            candidates_token_count = 50

        class FakeResp:
            usage_metadata = FakeUsage()

        increments = {}

        def fake_redis_inc(key, amount=1.0):
            increments[key] = increments.get(key, 0) + amount

        with patch('core.shared.metrics_utils._redis_inc', side_effect=fake_redis_inc):
            record_tokens(FakeResp(), operation='text_gen')

        assert increments.get('cosmic:prom:G:input:text_gen', 0) == 100
        assert increments.get('cosmic:prom:G:output:text_gen', 0) == 50

    def test_handles_missing_usage(self):
        class FakeResp:
            pass
        record_tokens(FakeResp())


class TestRecordPlaywrightOverlayFallback:
    def test_records_fallback_for_hook(self):
        from core.shared.metrics_utils import record_playwright_overlay_fallback
        increments = {}

        def fake_redis_inc(key, amount=1.0):
            increments[key] = increments.get(key, 0) + amount

        with patch('core.shared.metrics_utils._redis_inc', side_effect=fake_redis_inc):
            record_playwright_overlay_fallback('hook')

        assert increments.get('reel_playwright_fallback_hook_total', 0) == 1

    def test_records_fallback_for_cta(self):
        from core.shared.metrics_utils import record_playwright_overlay_fallback
        increments = {}

        def fake_redis_inc(key, amount=1.0):
            increments[key] = increments.get(key, 0) + amount

        with patch('core.shared.metrics_utils._redis_inc', side_effect=fake_redis_inc):
            record_playwright_overlay_fallback('cta')

        assert increments.get('reel_playwright_fallback_cta_total', 0) == 1


class TestRecordHyperframesGeneration:
    def test_records_generation_for_portada(self):
        from core.shared.metrics_utils import record_hyperframes_generation
        increments = {}

        def fake_redis_inc(key, amount=1.0):
            increments[key] = increments.get(key, 0) + amount

        with patch('core.shared.metrics_utils._redis_inc', side_effect=fake_redis_inc):
            record_hyperframes_generation('portada')

        assert increments.get('reel_hyperframes_portada_total', 0) == 1

    def test_records_generation_for_contraportada(self):
        from core.shared.metrics_utils import record_hyperframes_generation
        increments = {}

        def fake_redis_inc(key, amount=1.0):
            increments[key] = increments.get(key, 0) + amount

        with patch('core.shared.metrics_utils._redis_inc', side_effect=fake_redis_inc):
            record_hyperframes_generation('contraportada')

        assert increments.get('reel_hyperframes_contraportada_total', 0) == 1


class TestRecordHyperframesFallback:
    def test_records_fallback(self):
        from core.shared.metrics_utils import record_hyperframes_fallback
        increments = {}

        def fake_redis_inc(key, amount=1.0):
            increments[key] = increments.get(key, 0) + amount

        with patch('core.shared.metrics_utils._redis_inc', side_effect=fake_redis_inc):
            record_hyperframes_fallback()

        assert increments.get('reel_hyperframes_fallback_total', 0) == 1


