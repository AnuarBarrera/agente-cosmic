import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class N8nClient:
    def dispatch(self, workflow_id: str, params: dict, job_id: str, chat_id: str) -> None:
        """Envía un job a n8n via webhook. Lanza excepción si falla."""
        base_url = getattr(settings, 'N8N_BASE_URL', '')
        if not base_url:
            raise ValueError('N8N_BASE_URL no configurada en settings.')
        url = f'{base_url}/{workflow_id}'
        payload = {'job_id': job_id, 'chat_id': str(chat_id), 'params': params}
        resp = requests.post(url, json=payload, timeout=30)
        resp.raise_for_status()
