"""Tests de los endpoints REST del agente."""
import pytest
from django.test import Client, override_settings
from core.agent.infrastructure.models import AgentSession, AgentRequest

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def session_with_requests(db):
    session = AgentSession.objects.create(
        chat_id=333333333,
        username='anuar',
        full_name='Anuar Barrera',
        is_authorized=True,
    )
    AgentRequest.objects.create(
        session=session,
        user_message='Hola',
        ai_response='Hola, ¿en qué te ayudo?',
        model_used='gemini-2.5-flash',
        duration_ms=300,
        estimated_tokens=50,
        success=True,
    )
    AgentRequest.objects.create(
        session=session,
        user_message='Error test',
        ai_response='Error',
        model_used='gemini-2.5-flash',
        duration_ms=100,
        estimated_tokens=10,
        success=False,
        error_message='Timeout',
    )
    return session


# --- /api/v1/agent/health/ ---

def test_health_returns_200(client):
    response = client.get('/api/v1/agent/health/')
    assert response.status_code == 200


def test_health_returns_ok_status(client):
    data = client.get('/api/v1/agent/health/').json()
    assert data['status'] == 'ok'


def test_health_returns_model_name(client):
    with override_settings(AI_MODEL='gemini-2.5-flash'):
        data = client.get('/api/v1/agent/health/').json()
    assert data['model'] == 'gemini-2.5-flash'


def test_health_returns_telegram_flag(client):
    with override_settings(TELEGRAM_BOT_TOKEN='test-token'):
        data = client.get('/api/v1/agent/health/').json()
    assert data['telegram'] is True


def test_health_telegram_false_when_no_token(client):
    with override_settings(TELEGRAM_BOT_TOKEN=''):
        data = client.get('/api/v1/agent/health/').json()
    assert data['telegram'] is False


def test_health_rejects_post(client):
    response = client.post('/api/v1/agent/health/')
    assert response.status_code == 405


# --- /api/v1/agent/metrics/ ---

def test_metrics_returns_200(client, session_with_requests):
    response = client.get('/api/v1/agent/metrics/')
    assert response.status_code == 200


def test_metrics_returns_expected_fields(client, session_with_requests):
    data = client.get('/api/v1/agent/metrics/').json()
    assert 'total_requests' in data
    assert 'successful' in data
    assert 'avg_duration_ms' in data
    assert 'total_tokens' in data
    assert 'sessions_authorized' in data
    assert 'period_days' in data


def test_metrics_counts_total_requests(client, session_with_requests):
    data = client.get('/api/v1/agent/metrics/').json()
    assert data['total_requests'] == 2


def test_metrics_counts_successful_requests(client, session_with_requests):
    data = client.get('/api/v1/agent/metrics/').json()
    assert data['successful'] == 1


def test_metrics_counts_authorized_sessions(client, session_with_requests):
    data = client.get('/api/v1/agent/metrics/').json()
    assert data['sessions_authorized'] == 1


def test_metrics_period_is_30_days(client):
    data = client.get('/api/v1/agent/metrics/').json()
    assert data['period_days'] == 30


def test_metrics_rejects_post(client):
    response = client.post('/api/v1/agent/metrics/')
    assert response.status_code == 405
