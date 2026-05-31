import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class McpClient:
    def call(self, server: str, tool: str, params: dict, timeout: int = 20) -> dict:
        """Llama a un tool de un MCP server via HTTP POST. Retorna el JSON de respuesta."""
        servers = getattr(settings, 'MCP_SERVERS', {})
        base_url = servers.get(server)
        if not base_url:
            raise ValueError(f"MCP server '{server}' not configured in MCP_SERVERS.")
        url = f'{base_url}/call'
        resp = requests.post(url, json={'tool': tool, 'params': params}, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
