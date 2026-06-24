import pytest
from core.shared.metrics import (
    ANALYSIS_JOBS_TOTAL,
    ANALYSIS_DURATION,
    EXTERNAL_API_REQUESTS,
    GEMINI_TOKENS,
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
    def test_records_tokens_from_response(self):
        before_in = GEMINI_TOKENS.labels(direction='input')._value.get()
        before_out = GEMINI_TOKENS.labels(direction='output')._value.get()

        class FakeUsage:
            prompt_token_count = 100
            candidates_token_count = 50

        class FakeResp:
            usage_metadata = FakeUsage()

        record_tokens(FakeResp())
        assert GEMINI_TOKENS.labels(direction='input')._value.get() == before_in + 100
        assert GEMINI_TOKENS.labels(direction='output')._value.get() == before_out + 50

    def test_handles_missing_usage(self):
        class FakeResp:
            pass
        record_tokens(FakeResp())
