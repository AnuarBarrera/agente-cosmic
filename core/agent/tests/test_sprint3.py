"""Tests del Sprint 3 — Integración n8n + Google Maps."""
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings
from core.agent.infrastructure.tools.maps_tools import ProspectMapsTool, geocode_location
from core.agent.domain.tools import ToolResult

pytestmark = pytest.mark.django_db


# ─── geocode_location ─────────────────────────────────────────────────────

class TestGeocodeLocation:
    def test_returns_coordinates_for_valid_city(self):
        mock_response = {
            'status': 'OK',
            'results': [{'geometry': {'location': {'lat': 25.6866, 'lng': -100.3161}}}],
        }
        with patch('core.agent.infrastructure.tools.maps_tools.requests.get') as mock_get:
            mock_get.return_value.json.return_value = mock_response
            with override_settings(GOOGLE_PLACES_API_KEY='test-key'):
                result = geocode_location('Monterrey')
        assert result == (25.6866, -100.3161)

    def test_returns_none_when_api_key_missing(self):
        with override_settings(GOOGLE_PLACES_API_KEY=''):
            result = geocode_location('Monterrey')
        assert result is None

    def test_returns_none_on_zero_results(self):
        with patch('core.agent.infrastructure.tools.maps_tools.requests.get') as mock_get:
            mock_get.return_value.json.return_value = {'status': 'ZERO_RESULTS', 'results': []}
            with override_settings(GOOGLE_PLACES_API_KEY='test-key'):
                result = geocode_location('ciudad inexistente xyz')
        assert result is None

    def test_returns_none_on_request_error(self):
        with patch('core.agent.infrastructure.tools.maps_tools.requests.get') as mock_get:
            mock_get.side_effect = Exception('Network error')
            with override_settings(GOOGLE_PLACES_API_KEY='test-key'):
                result = geocode_location('Monterrey')
        assert result is None


# ─── ProspectMapsTool ─────────────────────────────────────────────────────

@pytest.fixture
def mock_enqueue():
    with patch('core.agent.infrastructure.tools.maps_tools.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue
        yield mock_queue


@pytest.fixture
def mock_geocode_ok():
    with patch('core.agent.infrastructure.tools.maps_tools.geocode_location') as mock:
        mock.return_value = (25.6866, -100.3161)
        yield mock


class TestProspectMapsTool:
    def test_returns_tool_result(self, mock_enqueue, mock_geocode_ok):
        tool = ProspectMapsTool()
        with override_settings(GOOGLE_PLACES_API_KEY='key', N8N_WEBHOOK_URL='http://n8n/webhook/prospector'):
            result = tool.execute(giro='plomeros', location='Monterrey', rango_km=5.0, chat_id=123)
        assert isinstance(result, ToolResult)

    def test_success_enqueues_rq_job(self, mock_enqueue, mock_geocode_ok):
        tool = ProspectMapsTool()
        with override_settings(GOOGLE_PLACES_API_KEY='key', N8N_WEBHOOK_URL='http://n8n/webhook/prospector'):
            result = tool.execute(giro='plomeros', location='Monterrey', rango_km=5.0, chat_id=123)
        assert result.success is True
        mock_enqueue.enqueue.assert_called_once()

    def test_enqueued_with_correct_params(self, mock_enqueue, mock_geocode_ok):
        tool = ProspectMapsTool()
        with override_settings(GOOGLE_PLACES_API_KEY='key', N8N_WEBHOOK_URL='http://n8n/webhook/prospector'):
            tool.execute(giro='restaurantes', location='Monterrey', rango_km=3.0, chat_id=456)
        call_kwargs = mock_enqueue.enqueue.call_args[1]
        assert call_kwargs['giro'] == 'restaurantes'
        assert call_kwargs['rango_km'] == 3.0
        assert call_kwargs['chat_id'] == 456

    def test_result_content_mentions_giro(self, mock_enqueue, mock_geocode_ok):
        tool = ProspectMapsTool()
        with override_settings(GOOGLE_PLACES_API_KEY='key', N8N_WEBHOOK_URL='http://n8n/webhook/prospector'):
            result = tool.execute(giro='ferreterías', location='Guadalajara', rango_km=5.0, chat_id=123)
        assert 'ferreterías' in result.content

    def test_accepts_lat_lng_string(self, mock_enqueue):
        from django.core.cache import cache
        cache.clear()
        tool = ProspectMapsTool()
        with override_settings(GOOGLE_PLACES_API_KEY='key', N8N_WEBHOOK_URL='http://n8n/webhook/prospector'):
            result = tool.execute(giro='plomeros', location='25.67,-100.31', rango_km=5.0, chat_id=123)
        assert result.success is True
        assert result.metadata['lat'] == 25.67
        assert result.metadata['lng'] == -100.31
        cache.clear()

    def test_fails_when_city_not_found(self, mock_enqueue):
        with patch('core.agent.infrastructure.tools.maps_tools.geocode_location', return_value=None):
            tool = ProspectMapsTool()
            with override_settings(GOOGLE_PLACES_API_KEY='key'):
                result = tool.execute(giro='plomeros', location='ciudad xyz abc', chat_id=123)
        assert result.success is False

    def test_rate_limit_blocks_after_3_requests(self, mock_enqueue, mock_geocode_ok):
        from django.core.cache import cache
        cache.clear()
        tool = ProspectMapsTool()
        chat_id = 777777
        with override_settings(GOOGLE_PLACES_API_KEY='key', N8N_WEBHOOK_URL='http://n8n/test'):
            for _ in range(3):
                result = tool.execute(giro='test', location='25.0,25.0', rango_km=1.0, chat_id=chat_id)
                assert result.success is True
            # 4to intento debe fallar
            result = tool.execute(giro='test', location='25.0,25.0', rango_km=1.0, chat_id=chat_id)
        assert result.success is False
        assert 'límite' in result.error.lower()
        cache.clear()

    def test_rate_limit_is_per_user(self, mock_enqueue, mock_geocode_ok):
        from django.core.cache import cache
        cache.clear()
        tool = ProspectMapsTool()
        with override_settings(GOOGLE_PLACES_API_KEY='key', N8N_WEBHOOK_URL='http://n8n/test'):
            for _ in range(3):
                tool.execute(giro='test', location='25.0,25.0', rango_km=1.0, chat_id=111)
            # Usuario diferente no debe estar bloqueado
            result = tool.execute(giro='test', location='25.0,25.0', rango_km=1.0, chat_id=222)
        assert result.success is True
        cache.clear()


# ─── prospect_n8n_job ──────────────────────────────────────────────────────

class TestProspectN8nJob:
    def test_sends_success_telegram_on_completion(self):
        from core.agent.infrastructure.jobs import prospect_n8n_job

        leads = [
            {'place_id': f'ChIJ{i}', 'name': f'Negocio {i}', 'address': 'Calle 1',
             'phone': '8181234567', 'website': '', 'rating': 4.0, 'reviews_total': 10,
             'lat': 25.68, 'lng': -100.31}
            for i in range(42)
        ]
        with patch('core.agent.infrastructure.jobs.requests.post') as mock_post, \
             patch('core.agent.infrastructure.jobs._send_telegram') as mock_telegram, \
             patch('core.agent.infrastructure.jobs._score_leads_with_gemini',
                   side_effect=lambda leads, giro: leads):

            mock_post.return_value.json.return_value = {
                'success': True, 'total': 42, 'giro': 'plomeros', 'leads': leads,
            }
            mock_post.return_value.raise_for_status = lambda: None

            with override_settings(
                N8N_WEBHOOK_URL='http://n8n/webhook/prospector',
                GOOGLE_SHEET_ID='sheet123',
                TELEGRAM_BOT_TOKEN='token',
            ):
                prospect_n8n_job(
                    giro='plomeros', lat=25.68, lng=-100.31, rango_km=5.0, chat_id=123
                )

        mock_telegram.assert_called_once()
        msg = mock_telegram.call_args[0][1]
        assert '42' in msg
        assert 'plomeros' in msg.lower()

    def test_sends_error_on_timeout(self):
        from core.agent.infrastructure.jobs import prospect_n8n_job
        import requests as req_lib

        with patch('core.agent.infrastructure.jobs.requests.post') as mock_post, \
             patch('core.agent.infrastructure.jobs._send_telegram') as mock_telegram:

            mock_post.side_effect = req_lib.Timeout()

            with override_settings(
                N8N_WEBHOOK_URL='http://n8n/webhook/prospector',
                TELEGRAM_BOT_TOKEN='token',
                GOOGLE_SHEET_ID='',
            ):
                prospect_n8n_job(
                    giro='plomeros', lat=25.68, lng=-100.31, rango_km=5.0, chat_id=123
                )

        mock_telegram.assert_called_once()
        msg = mock_telegram.call_args[0][1]
        assert 'tardando' in msg.lower() or 'timeout' in msg.lower() or 'minutos' in msg.lower()

    def test_does_nothing_without_n8n_url(self):
        from core.agent.infrastructure.jobs import prospect_n8n_job

        with patch('core.agent.infrastructure.jobs._send_telegram') as mock_telegram:
            with override_settings(N8N_WEBHOOK_URL='', TELEGRAM_BOT_TOKEN='token', GOOGLE_SHEET_ID=''):
                prospect_n8n_job(
                    giro='test', lat=0.0, lng=0.0, rango_km=1.0, chat_id=123
                )

        mock_telegram.assert_called_once()
        assert 'configurada' in mock_telegram.call_args[0][1].lower()
