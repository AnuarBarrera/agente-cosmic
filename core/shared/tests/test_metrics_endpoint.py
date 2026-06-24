import pytest
from django.test import Client


@pytest.mark.django_db
class TestMetricsEndpoint:
    def test_metrics_returns_200(self):
        client = Client()
        resp = client.get('/metrics')
        assert resp.status_code == 200

    def test_metrics_contains_django_prometheus(self):
        client = Client()
        resp = client.get('/metrics')
        body = resp.content.decode()
        assert 'django_http_requests_total' in body

    def test_metrics_contains_custom_metrics(self):
        client = Client()
        resp = client.get('/metrics')
        body = resp.content.decode()
        assert 'cosmic_analysis_jobs_total' in body
        assert 'cosmic_external_api_requests_total' in body
        assert 'cosmic_calendars_created_total' in body
