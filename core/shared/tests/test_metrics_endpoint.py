import pytest
from django.test import Client

_TEST_TOKEN = 'test-metrics-token'


@pytest.fixture
def auth_headers(monkeypatch):
    monkeypatch.setenv('PROMETHEUS_METRICS_TOKEN', _TEST_TOKEN)
    return {'HTTP_AUTHORIZATION': f'Bearer {_TEST_TOKEN}'}


@pytest.mark.django_db
class TestMetricsEndpoint:
    def test_metrics_returns_200(self, auth_headers):
        client = Client()
        resp = client.get('/metrics', **auth_headers)
        assert resp.status_code == 200

    def test_metrics_contains_django_prometheus(self, auth_headers):
        client = Client()
        resp = client.get('/metrics', **auth_headers)
        body = resp.content.decode()
        assert 'django_http_requests_total' in body

    def test_metrics_contains_custom_metrics(self, auth_headers):
        client = Client()
        resp = client.get('/metrics', **auth_headers)
        body = resp.content.decode()
        assert 'cosmic_analysis_jobs_total' in body
        assert 'cosmic_external_api_requests_total' in body
        assert 'cosmic_calendars_created_total' in body

    def test_metrics_requires_auth_when_token_set(self, monkeypatch):
        monkeypatch.setenv('PROMETHEUS_METRICS_TOKEN', _TEST_TOKEN)
        client = Client()
        resp = client.get('/metrics')
        assert resp.status_code == 401
