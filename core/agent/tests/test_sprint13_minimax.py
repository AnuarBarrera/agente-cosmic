# core/agent/tests/test_sprint13_minimax.py
import asyncio
import base64
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from django.test import override_settings

pytestmark = pytest.mark.django_db


class TestGeneratePostImageTool:
    def test_calls_pollinations_and_returns_image_bytes(self):
        from core.agent.infrastructure.tools.image_tools import GeneratePostImageTool
        from core.agent.infrastructure.gemini_adapter import GeminiAdapter
        fake_bytes = b'\x89PNG\r\n\x1a\nFAKE'
        mock_resp = MagicMock()
        mock_resp.content = fake_bytes
        mock_resp.raise_for_status = MagicMock()
        with patch.object(GeminiAdapter, 'generate_response', return_value='a marketing photo'), \
             patch('core.agent.infrastructure.tools.image_tools.requests.get',
                   return_value=mock_resp), \
             override_settings(GEMINI_API_KEY='k'):
            result = GeneratePostImageTool().execute(topic='diseño web', platform='instagram')
        assert result.success
        assert result.metadata['image_bytes'] == fake_bytes
        assert result.metadata['filename'].endswith('.jpg')

    def test_returns_error_when_pollinations_fails(self):
        from core.agent.infrastructure.tools.image_tools import GeneratePostImageTool
        from core.agent.infrastructure.gemini_adapter import GeminiAdapter
        with patch.object(GeminiAdapter, 'generate_response', return_value='a photo'), \
             patch('core.agent.infrastructure.tools.image_tools.requests.get',
                   side_effect=Exception('connection refused')), \
             override_settings(GEMINI_API_KEY='k'):
            result = GeneratePostImageTool().execute(topic='test')
        assert not result.success
        assert 'No pude generar' in result.content

    def test_story_platform_uses_576x1024(self):
        from core.agent.infrastructure.tools.image_tools import GeneratePostImageTool
        from core.agent.infrastructure.gemini_adapter import GeminiAdapter
        mock_resp = MagicMock()
        mock_resp.content = b'JPGDATA'
        mock_resp.raise_for_status = MagicMock()
        with patch.object(GeminiAdapter, 'generate_response', return_value='a photo'), \
             patch('core.agent.infrastructure.tools.image_tools.requests.get',
                   return_value=mock_resp) as mock_get, \
             override_settings(GEMINI_API_KEY='k'):
            GeneratePostImageTool().execute(topic='test', platform='story')
        url = mock_get.call_args[0][0]
        assert 'width=576' in url
        assert 'height=1024' in url


class TestMcpClientTimeout:
    def test_uses_custom_timeout(self):
        from core.agent.infrastructure.mcp_client import McpClient
        with patch('core.agent.infrastructure.mcp_client.requests.post') as mock_post, \
             override_settings(MCP_SERVERS={'minimax': 'http://minimax-mcp:8080'}):
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {'result': 'ok'}
            mock_post.return_value = mock_resp
            McpClient().call('minimax', 'generate_video', {'prompt': 'test'}, timeout=360)
        _, kwargs = mock_post.call_args
        assert kwargs.get('timeout') == 360

    def test_defaults_to_20s(self):
        from core.agent.infrastructure.mcp_client import McpClient
        with patch('core.agent.infrastructure.mcp_client.requests.post') as mock_post, \
             override_settings(MCP_SERVERS={'minimax': 'http://minimax-mcp:8080'}):
            mock_resp = MagicMock()
            mock_resp.raise_for_status = MagicMock()
            mock_resp.json.return_value = {}
            mock_post.return_value = mock_resp
            McpClient().call('minimax', 'generate_image', {'prompt': 'test'})
        _, kwargs = mock_post.call_args
        assert kwargs.get('timeout') == 20


class TestGenerateAudioTool:
    def test_returns_audio_bytes(self):
        from core.agent.infrastructure.tools.media_tools import GenerateAudioTool
        import core.agent.infrastructure.tools.media_tools as media_module
        fake_audio = b'ID3\x03\x00FAKEMP3'
        with patch.object(media_module, '_tts', return_value=fake_audio):
            result = GenerateAudioTool().execute(text='Bienvenidos a Tu Web MX')
        assert result.success
        assert result.metadata['audio_bytes'] == fake_audio
        assert result.metadata['filename'].endswith('.mp3')

    def test_rejects_text_over_2000_chars(self):
        from core.agent.infrastructure.tools.media_tools import GenerateAudioTool
        result = GenerateAudioTool().execute(text='a' * 2001)
        assert not result.success
        assert '2000' in result.content


class TestGenerateVideoTool:
    def test_enqueues_rq_job_and_returns_pending_message(self):
        from core.agent.infrastructure.tools.media_tools import GenerateVideoTool
        with patch('core.agent.infrastructure.tools.media_tools.django_rq') as mock_rq:
            mock_queue = MagicMock()
            mock_rq.get_queue.return_value = mock_queue
            result = GenerateVideoTool().execute(
                prompt='Un short sobre diseño web', chat_id=123456
            )
        assert result.success
        mock_queue.enqueue.assert_called_once()
        call_args = mock_queue.enqueue.call_args[0]
        assert call_args[0] == 'core.agent.infrastructure.jobs.video_pexels_job'
        assert '⏳' in result.content

    def test_returns_error_when_rq_fails(self):
        from core.agent.infrastructure.tools.media_tools import GenerateVideoTool
        with patch('core.agent.infrastructure.tools.media_tools.django_rq') as mock_rq:
            mock_rq.get_queue.side_effect = Exception('Redis unavailable')
            result = GenerateVideoTool().execute(prompt='test', chat_id=123)
        assert not result.success
