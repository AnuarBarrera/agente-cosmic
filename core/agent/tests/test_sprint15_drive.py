"""Tests Sprint 15B — drive_search_job y cmd_drive."""
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings

pytestmark = pytest.mark.django_db

DRIVE_SETTINGS = {
    'N8N_BASE_URL': 'http://172.17.0.1:5678/webhook',
    'N8N_WORKFLOW_DRIVE_SEARCH': 'drive_search',
    'GOOGLE_DRIVE_FOLDER_ID': 'folder_abc123',
    'TELEGRAM_BOT_TOKEN': 'test-token',
    'N8N_CALLBACK_TOKEN': 'test-cb-token',
}


class TestDriveSearchJob:
    @override_settings(**DRIVE_SETTINGS)
    def test_creates_pending_job_and_dispatches(self):
        """drive_search_job crea PendingJob y llama N8nClient.dispatch."""
        from core.agent.infrastructure.jobs import drive_search_job
        from core.agent.infrastructure.models import PendingJob

        with patch('core.agent.infrastructure.jobs.N8nClient') as MockClient:
            mock_instance = MagicMock()
            MockClient.return_value = mock_instance
            drive_search_job(query='propuesta marketing', chat_id=999)

        assert PendingJob.objects.filter(command='drive', workflow='drive_search').exists()
        mock_instance.dispatch.assert_called_once()
        call_kwargs = mock_instance.dispatch.call_args
        assert call_kwargs.kwargs['workflow_id'] == 'drive_search'
        assert call_kwargs.kwargs['params']['query'] == 'propuesta marketing'
        assert call_kwargs.kwargs['params']['folder_id'] == 'folder_abc123'

    @override_settings(**{**DRIVE_SETTINGS, 'N8N_WORKFLOW_DRIVE_SEARCH': ''})
    def test_sends_telegram_when_not_configured(self):
        """drive_search_job notifica vía Telegram si el workflow no está configurado."""
        from core.agent.infrastructure.jobs import drive_search_job

        with patch('core.agent.infrastructure.jobs._send_telegram') as mock_tg:
            drive_search_job(query='test', chat_id=999)

        mock_tg.assert_called_once()
        assert '❌' in mock_tg.call_args[0][1]

    @override_settings(**DRIVE_SETTINGS)
    def test_sends_telegram_on_dispatch_error(self):
        """drive_search_job maneja errores de N8nClient y notifica por Telegram."""
        from core.agent.infrastructure.jobs import drive_search_job

        with patch('core.agent.infrastructure.jobs.N8nClient') as MockClient:
            MockClient.return_value.dispatch.side_effect = Exception('n8n unreachable')
            with patch('core.agent.infrastructure.jobs._send_telegram') as mock_tg:
                drive_search_job(query='test', chat_id=999)

        mock_tg.assert_called_once()
        assert '❌' in mock_tg.call_args[0][1]


class TestDriveCallbackParsing:
    """Verifica que el callback de n8n para 'drive' se procesa correctamente."""

    def _make_callback_request(self, client, data: dict):
        import json
        return client.post(
            '/api/v1/agent/n8n/callback/',
            data=json.dumps(data),
            content_type='application/json',
            HTTP_X_N8N_TOKEN='test-cb-token',
        )

    @override_settings(N8N_CALLBACK_TOKEN='test-cb-token', TELEGRAM_BOT_TOKEN='test-token',
                       AI_MODEL='gemini-3.1-flash-lite', GEMINI_API_KEY='test-key')
    def test_drive_callback_formats_and_sends(self):
        """El callback con command='drive' formatea con Gemini y envía a Telegram."""
        from core.agent.infrastructure.models import PendingJob
        from django.test import Client as DjangoClient
        import uuid

        job_id = str(uuid.uuid4())
        PendingJob.objects.create(job_id=job_id, chat_id='999', command='drive', workflow='drive_search')

        with patch('core.agent.interfaces.n8n_views._format_with_gemini', return_value='📁 Archivos encontrados'):
            with patch('core.agent.interfaces.n8n_views._send_telegram') as mock_tg:
                resp = self._make_callback_request(DjangoClient(), {
                    'job_id': job_id,
                    'chat_id': '999',
                    'status': 'ok',
                    'data': {'files': [{'name': 'propuesta.docx', 'url': 'https://drive.google.com/...'}]},
                })

        assert resp.status_code == 200
        mock_tg.assert_called_once_with('999', '📁 Archivos encontrados')
