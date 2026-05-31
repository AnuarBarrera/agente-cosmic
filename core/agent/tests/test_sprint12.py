"""Tests del Sprint 12 — N8nClient, PendingJob, callback endpoint, estadisticas y prospecto via n8n."""
import uuid
import json
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings, TestCase
from django.test import Client as DjangoClient

pytestmark = pytest.mark.django_db


# ─── PendingJob ──────────────────────────────────────────────────────────────

class TestPendingJob:
    def test_creates_with_auto_uuid(self):
        """PendingJob genera un UUID único al crearse sin job_id explícito."""
        from core.agent.infrastructure.models import PendingJob
        job = PendingJob.objects.create(
            chat_id='123456789',
            command='estadisticas',
            workflow='instagram_stats',
        )
        assert job.job_id is not None
        assert str(job.job_id)

    def test_default_status_is_pending(self):
        """El status por defecto es 'pending'."""
        from core.agent.infrastructure.models import PendingJob
        job = PendingJob.objects.create(
            chat_id='123456789',
            command='estadisticas',
            workflow='instagram_stats',
        )
        assert job.status == 'pending'

    def test_completed_at_is_null_by_default(self):
        """completed_at es null al crearse."""
        from core.agent.infrastructure.models import PendingJob
        job = PendingJob.objects.create(
            chat_id='123456789',
            command='estadisticas',
            workflow='instagram_stats',
        )
        assert job.completed_at is None


# ─── N8nClient ───────────────────────────────────────────────────────────────

class TestN8nClient:
    def test_dispatch_posts_to_workflow_url(self):
        """dispatch() hace POST a {N8N_BASE_URL}/{workflow_id} con job_id y chat_id."""
        from core.agent.infrastructure.n8n_client import N8nClient
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()

        with patch('core.agent.infrastructure.n8n_client.requests.post', return_value=mock_resp) as mock_post, \
             override_settings(N8N_BASE_URL='http://172.17.0.1:5678/webhook'):
            client = N8nClient()
            client.dispatch(
                workflow_id='instagram_stats',
                params={'url': 'https://instagram.com/p/abc'},
                job_id='test-uuid-123',
                chat_id='987654321',
            )

        mock_post.assert_called_once_with(
            'http://172.17.0.1:5678/webhook/instagram_stats',
            json={
                'job_id': 'test-uuid-123',
                'chat_id': '987654321',
                'params': {'url': 'https://instagram.com/p/abc'},
            },
            timeout=30,
        )

    def test_dispatch_raises_when_base_url_not_configured(self):
        """dispatch() lanza ValueError cuando N8N_BASE_URL no está configurada."""
        from core.agent.infrastructure.n8n_client import N8nClient
        with override_settings(N8N_BASE_URL=''):
            client = N8nClient()
            with pytest.raises(ValueError, match='N8N_BASE_URL'):
                client.dispatch('instagram_stats', {}, 'job-1', '123')

    def test_dispatch_propagates_http_error(self):
        """dispatch() propaga la excepción cuando n8n responde con error HTTP."""
        import requests as req
        from core.agent.infrastructure.n8n_client import N8nClient
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = req.HTTPError('404')

        with patch('core.agent.infrastructure.n8n_client.requests.post', return_value=mock_resp), \
             override_settings(N8N_BASE_URL='http://172.17.0.1:5678/webhook'):
            client = N8nClient()
            with pytest.raises(req.HTTPError):
                client.dispatch('workflow_inexistente', {}, 'job-1', '123')


# ─── Callback endpoint ────────────────────────────────────────────────────────

class TestN8nCallbackView:
    def _post(self, client, data, token='test-token'):
        return client.post(
            '/api/v1/agent/n8n/callback/',
            data=json.dumps(data),
            content_type='application/json',
            HTTP_X_N8N_TOKEN=token,
        )

    def test_returns_401_without_token(self, client):
        """Devuelve 401 si no hay header X-N8N-Token."""
        with override_settings(N8N_CALLBACK_TOKEN='test-token'):
            resp = client.post(
                '/api/v1/agent/n8n/callback/',
                data='{}',
                content_type='application/json',
            )
        assert resp.status_code == 401

    def test_returns_401_with_wrong_token(self, client):
        """Devuelve 401 si el token no coincide."""
        with override_settings(N8N_CALLBACK_TOKEN='correct-token'):
            resp = self._post(client, {}, token='wrong-token')
        assert resp.status_code == 401

    def test_marks_job_completed_and_sends_telegram(self, client):
        """Con token correcto y job válido, marca PendingJob como completed y llama Telegram."""
        from core.agent.infrastructure.models import PendingJob
        job = PendingJob.objects.create(
            chat_id='111222333',
            command='estadisticas',
            workflow='instagram_stats',
        )
        payload = {
            'job_id': str(job.job_id),
            'chat_id': '111222333',
            'status': 'ok',
            'data': {'likes': 150, 'comments': 20},
        }
        with override_settings(N8N_CALLBACK_TOKEN='test-token', GEMINI_API_KEY='test-key'), \
             patch('core.agent.interfaces.n8n_views.GeminiAdapter') as MockGemini, \
             patch('core.agent.interfaces.n8n_views._send_telegram') as mock_tg:
            MockGemini.return_value.generate_response.return_value = '📊 150 likes, 20 comentarios.'
            resp = self._post(client, payload)

        assert resp.status_code == 200
        job.refresh_from_db()
        assert job.status == 'completed'
        assert job.completed_at is not None
        mock_tg.assert_called_once()
        args = mock_tg.call_args[0]
        assert args[0] == '111222333'

    def test_marks_job_failed_on_error_status(self, client):
        """Si status != 'ok', marca PendingJob como failed y envía mensaje de error."""
        from core.agent.infrastructure.models import PendingJob
        job = PendingJob.objects.create(
            chat_id='111222333',
            command='estadisticas',
            workflow='instagram_stats',
        )
        payload = {
            'job_id': str(job.job_id),
            'chat_id': '111222333',
            'status': 'error',
            'data': {'error': 'Token expirado'},
        }
        with override_settings(N8N_CALLBACK_TOKEN='test-token'), \
             patch('core.agent.interfaces.n8n_views._send_telegram') as mock_tg:
            resp = self._post(client, payload)

        assert resp.status_code == 200
        job.refresh_from_db()
        assert job.status == 'failed'
        mock_tg.assert_called_once()

    def test_returns_404_for_unknown_job_id(self, client):
        """Devuelve 404 si job_id no existe en BD."""
        payload = {
            'job_id': str(uuid.uuid4()),
            'chat_id': '111222333',
            'status': 'ok',
            'data': {},
        }
        with override_settings(N8N_CALLBACK_TOKEN='test-token'):
            resp = self._post(client, payload)
        assert resp.status_code == 404

    def test_returns_400_on_missing_job_id(self, client):
        """Devuelve 400 si faltan job_id o chat_id."""
        with override_settings(N8N_CALLBACK_TOKEN='test-token'):
            resp = self._post(client, {'status': 'ok', 'data': {}})
        assert resp.status_code == 400


# ─── stats_n8n_job ────────────────────────────────────────────────────────────

class TestStatsN8nJob:
    def test_creates_pending_job_before_dispatch(self):
        """stats_n8n_job crea un PendingJob en BD antes de llamar a N8nClient."""
        from core.agent.infrastructure.jobs import stats_n8n_job
        from core.agent.infrastructure.models import PendingJob

        with patch('core.agent.infrastructure.jobs.N8nClient') as MockClient, \
             override_settings(N8N_BASE_URL='http://n8n:5678/webhook'):
            MockClient.return_value.dispatch = MagicMock()
            stats_n8n_job(url='https://instagram.com/p/abc', platform='instagram', chat_id=123)

        assert PendingJob.objects.filter(command='estadisticas', workflow='instagram_stats').exists()

    def test_dispatches_to_correct_workflow(self):
        """stats_n8n_job llama N8nClient.dispatch con workflow_id={platform}_stats."""
        from core.agent.infrastructure.jobs import stats_n8n_job
        mock_dispatch = MagicMock()

        with patch('core.agent.infrastructure.jobs.N8nClient') as MockClient, \
             override_settings(N8N_BASE_URL='http://n8n:5678/webhook'):
            MockClient.return_value.dispatch = mock_dispatch
            stats_n8n_job(url='https://tiktok.com/@user/video/123', platform='tiktok', chat_id=456)

        call_kwargs = mock_dispatch.call_args
        assert call_kwargs[1]['workflow_id'] == 'tiktok_stats'
        assert call_kwargs[1]['params']['url'] == 'https://tiktok.com/@user/video/123'

    def test_marks_job_failed_on_n8n_error(self):
        """Si N8nClient.dispatch lanza excepción, el PendingJob queda como failed."""
        from core.agent.infrastructure.jobs import stats_n8n_job
        from core.agent.infrastructure.models import PendingJob

        with patch('core.agent.infrastructure.jobs.N8nClient') as MockClient, \
             patch('core.agent.infrastructure.jobs._send_telegram'), \
             override_settings(N8N_BASE_URL='http://n8n:5678/webhook'):
            MockClient.return_value.dispatch.side_effect = Exception('Connection refused')
            stats_n8n_job(url='https://instagram.com/p/abc', platform='instagram', chat_id=789)

        job = PendingJob.objects.filter(command='estadisticas', workflow='instagram_stats').first()
        assert job is not None
        assert job.status == 'failed'


class TestGetPostStatsToolWithN8n:
    def test_enqueues_stats_n8n_job_for_instagram(self):
        """GetPostStatsTool encola stats_n8n_job para URLs de Instagram."""
        from core.agent.infrastructure.tools.browser_tools import GetPostStatsTool

        with patch('core.agent.infrastructure.tools.browser_tools.django_rq') as mock_rq:
            mock_queue = MagicMock()
            mock_rq.get_queue.return_value = mock_queue
            tool = GetPostStatsTool()
            result = tool.execute(url='https://www.instagram.com/p/abc123/', chat_id=111)

        assert result.success is True
        mock_queue.enqueue.assert_called_once()
        enqueue_args = mock_queue.enqueue.call_args
        assert 'stats_n8n_job' in enqueue_args[0][0]

    def test_returns_error_for_unknown_platform(self):
        """GetPostStatsTool retorna error para URLs de plataformas no soportadas."""
        from core.agent.infrastructure.tools.browser_tools import GetPostStatsTool
        tool = GetPostStatsTool()
        result = tool.execute(url='https://www.ejemplo-random.com/post/123')
        assert result.success is False


# ─── competitor_n8n_job ───────────────────────────────────────────────────────

class TestCompetitorN8nJob:
    def test_creates_pending_job_and_dispatches(self):
        """competitor_n8n_job crea PendingJob y llama N8nClient.dispatch."""
        from core.agent.infrastructure.jobs import competitor_n8n_job
        from core.agent.infrastructure.models import PendingJob
        mock_dispatch = MagicMock()

        with patch('core.agent.infrastructure.jobs.N8nClient') as MockClient, \
             override_settings(N8N_BASE_URL='http://n8n:5678/webhook'):
            MockClient.return_value.dispatch = mock_dispatch
            competitor_n8n_job(
                name='Competidor X',
                social_url='https://instagram.com/competidorx',
                platform='instagram',
                chat_id=111,
            )

        assert PendingJob.objects.filter(command='prospecto', workflow='instagram_competitor').exists()
        call_kwargs = mock_dispatch.call_args
        assert call_kwargs[1]['workflow_id'] == 'instagram_competitor'
        assert call_kwargs[1]['params']['url'] == 'https://instagram.com/competidorx'
        assert call_kwargs[1]['params']['name'] == 'Competidor X'

    def test_marks_job_failed_on_error(self):
        """Si dispatch falla, el PendingJob queda como failed."""
        from core.agent.infrastructure.jobs import competitor_n8n_job
        from core.agent.infrastructure.models import PendingJob

        with patch('core.agent.infrastructure.jobs.N8nClient') as MockClient, \
             patch('core.agent.infrastructure.jobs._send_telegram'), \
             override_settings(N8N_BASE_URL='http://n8n:5678/webhook'):
            MockClient.return_value.dispatch.side_effect = Exception('n8n down')
            competitor_n8n_job('Brand X', 'https://linkedin.com/in/brandx', 'linkedin', 222)

        job = PendingJob.objects.filter(command='prospecto', workflow='linkedin_competitor').first()
        assert job is not None
        assert job.status == 'failed'


class TestDetectSocialPlatform:
    def test_detects_instagram(self):
        from core.agent.infrastructure.jobs import detect_social_platform
        assert detect_social_platform('https://instagram.com/user') == 'instagram'

    def test_detects_linkedin(self):
        from core.agent.infrastructure.jobs import detect_social_platform
        assert detect_social_platform('https://www.linkedin.com/in/user') == 'linkedin'

    def test_detects_facebook(self):
        from core.agent.infrastructure.jobs import detect_social_platform
        assert detect_social_platform('https://facebook.com/page') == 'facebook'

    def test_detects_tiktok(self):
        from core.agent.infrastructure.jobs import detect_social_platform
        assert detect_social_platform('https://tiktok.com/@user') == 'tiktok'

    def test_returns_none_for_non_social(self):
        from core.agent.infrastructure.jobs import detect_social_platform
        assert detect_social_platform('https://tuwebmx.com') is None


# ─── Deprecación /login y /importcookies ─────────────────────────────────────

class TestDeprecatedCommands:
    def test_login_not_in_ayuda_text(self):
        """AYUDA_TEXT no menciona /login (comando deprecado)."""
        from core.agent.management.commands.run_telegram_bot import AYUDA_TEXT
        assert '/login' not in AYUDA_TEXT

    def test_importcookies_not_in_ayuda_text(self):
        """AYUDA_TEXT no menciona /importcookies (comando deprecado)."""
        from core.agent.management.commands.run_telegram_bot import AYUDA_TEXT
        assert '/importcookies' not in AYUDA_TEXT
