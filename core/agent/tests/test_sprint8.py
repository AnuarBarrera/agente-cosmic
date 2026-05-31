"""Tests del Sprint 8 — Generación de imágenes para posts (Minimax MCP)."""
import base64
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings
from core.agent.infrastructure.gemini_adapter import GeminiAdapter
from core.agent.infrastructure.mcp_client import McpClient

pytestmark = pytest.mark.django_db


class TestGeneratePostImageTool:
    def _fake_b64(self, data: bytes) -> str:
        return base64.b64encode(data).decode()

    def test_execute_returns_png_bytes_for_instagram(self):
        from core.agent.infrastructure.tools.image_tools import GeneratePostImageTool
        fake_img = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        with patch.object(GeminiAdapter, 'generate_response', return_value='a marketing photo'), \
             patch.object(McpClient, 'call', return_value={
                 'image_bytes_b64': self._fake_b64(fake_img)
             }), \
             override_settings(GEMINI_API_KEY='test-key',
                               MCP_SERVERS={'minimax': 'http://minimax-mcp:8080'}):
            tool = GeneratePostImageTool()
            result = tool.execute(topic='Nuevo servicio de diseño', platform='instagram')
        assert result.success is True
        assert result.tool_name == 'generate_post_image'
        assert isinstance(result.metadata.get('image_bytes'), bytes)

    def test_execute_returns_error_without_api_key(self):
        from core.agent.infrastructure.tools.image_tools import GeneratePostImageTool
        with override_settings(GEMINI_API_KEY=''):
            tool = GeneratePostImageTool()
            result = tool.execute(topic='algo', platform='instagram')
        assert result.success is False

    def test_execute_uses_correct_size_per_platform(self):
        from core.agent.infrastructure.tools.image_tools import PLATFORM_SIZE
        assert PLATFORM_SIZE['instagram'] == (1024, 1024)
        assert PLATFORM_SIZE['story'] == (576, 1024)
        assert PLATFORM_SIZE['linkedin'] == (1024, 576)

    def test_execute_defaults_to_instagram_for_unknown_platform(self):
        from core.agent.infrastructure.tools.image_tools import GeneratePostImageTool
        fake_img = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        mock_resp = MagicMock()
        mock_resp.content = fake_img
        mock_resp.raise_for_status = MagicMock()
        with patch.object(GeminiAdapter, 'generate_response', return_value='a photo'), \
             patch('core.agent.infrastructure.tools.image_tools.requests.get',
                   return_value=mock_resp) as mock_get, \
             override_settings(GEMINI_API_KEY='test-key'):
            tool = GeneratePostImageTool()
            result = tool.execute(topic='test', platform='tiktok')
        assert result.success is True
        url = mock_get.call_args[0][0]
        assert 'width=1024' in url
        assert 'height=1024' in url

    def test_execute_metadata_includes_filename(self):
        from core.agent.infrastructure.tools.image_tools import GeneratePostImageTool
        fake_img = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        with patch.object(GeminiAdapter, 'generate_response', return_value='a photo'), \
             patch.object(McpClient, 'call', return_value={
                 'image_bytes_b64': self._fake_b64(fake_img)
             }), \
             override_settings(GEMINI_API_KEY='test-key',
                               MCP_SERVERS={'minimax': 'http://minimax-mcp:8080'}):
            tool = GeneratePostImageTool()
            result = tool.execute(topic='test', platform='linkedin')
        assert 'filename' in result.metadata
        assert result.metadata['filename'].endswith('.jpg')
