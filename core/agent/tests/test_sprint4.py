"""Tests del Sprint 4 — Browser automation y estadísticas de redes sociales."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from django.test import override_settings
from core.agent.infrastructure.browser import (
    detect_platform, PostStats, scrape_post_stats,
)
from core.agent.infrastructure.tools.browser_tools import GetPostStatsTool
from core.agent.domain.tools import ToolResult

pytestmark = pytest.mark.django_db


# ─── detect_platform ──────────────────────────────────────────────────────

class TestDetectPlatform:
    def test_instagram(self):
        assert detect_platform('https://www.instagram.com/p/ABC123/') == 'instagram'

    def test_tiktok(self):
        assert detect_platform('https://www.tiktok.com/@user/video/123') == 'tiktok'

    def test_facebook(self):
        assert detect_platform('https://www.facebook.com/user/posts/123') == 'facebook'

    def test_linkedin(self):
        assert detect_platform('https://www.linkedin.com/posts/user-123') == 'linkedin'

    def test_twitter(self):
        assert detect_platform('https://twitter.com/user/status/123') == 'twitter'

    def test_x_com(self):
        assert detect_platform('https://x.com/user/status/123') == 'twitter'

    def test_unknown(self):
        assert detect_platform('https://www.youtube.com/watch?v=123') == 'unknown'


# ─── PostStats ────────────────────────────────────────────────────────────

class TestPostStats:
    def test_success_when_has_likes(self):
        stats = PostStats(platform='tiktok', url='http://t.co', likes='1.2K')
        assert stats.success is True

    def test_success_when_has_views(self):
        stats = PostStats(platform='tiktok', url='http://t.co', views='10K')
        assert stats.success is True

    def test_not_success_when_empty(self):
        stats = PostStats(platform='instagram', url='http://ig.co')
        assert stats.success is False

    def test_not_success_when_has_error(self):
        stats = PostStats(platform='instagram', url='http://ig.co', likes='100', error='blocked')
        assert stats.success is False

    def test_format_telegram_includes_platform(self):
        stats = PostStats(platform='tiktok', url='http://t.co', likes='500', views='10K')
        text = stats.format_telegram()
        assert 'Tiktok' in text or 'tiktok' in text.lower()

    def test_format_telegram_includes_likes(self):
        stats = PostStats(platform='tiktok', url='http://t.co', likes='1.2K')
        text = stats.format_telegram()
        assert '1.2K' in text

    def test_format_telegram_includes_views(self):
        stats = PostStats(platform='tiktok', url='http://t.co', views='50K')
        text = stats.format_telegram()
        assert '50K' in text

    def test_format_telegram_no_stats_message(self):
        stats = PostStats(platform='instagram', url='http://ig.co')
        text = stats.format_telegram()
        assert 'estadísticas' in text.lower() or 'inicio de sesión' in text.lower()


# ─── scrape_post_stats ────────────────────────────────────────────────────

class TestScrapePostStats:
    def test_returns_post_stats_object(self):
        with patch('core.agent.infrastructure.browser.asyncio.run') as mock_run:
            mock_run.return_value = PostStats(
                platform='tiktok', url='http://t.co', likes='500', views='10K'
            )
            result = scrape_post_stats('https://www.tiktok.com/@user/video/123')
        assert isinstance(result, PostStats)
        assert result.platform == 'tiktok'

    def test_returns_error_on_exception(self):
        with patch('core.agent.infrastructure.browser.asyncio.run') as mock_run:
            mock_run.side_effect = Exception('Browser crash')
            result = scrape_post_stats('https://www.tiktok.com/@user/video/123')
        assert result.error is not None
        assert result.success is False


# ─── GetPostStatsTool ─────────────────────────────────────────────────────

@pytest.fixture
def mock_enqueue_stats():
    with patch('core.agent.infrastructure.tools.browser_tools.django_rq') as mock_rq:
        mock_queue = MagicMock()
        mock_rq.get_queue.return_value = mock_queue
        yield mock_queue


class TestGetPostStatsTool:
    def test_returns_tool_result(self, mock_enqueue_stats):
        from django.core.cache import cache
        cache.clear()
        tool = GetPostStatsTool()
        result = tool.execute(url='https://www.tiktok.com/@user/video/123', chat_id=123)
        assert isinstance(result, ToolResult)

    def test_success_enqueues_job(self, mock_enqueue_stats):
        from django.core.cache import cache
        cache.clear()
        tool = GetPostStatsTool()
        result = tool.execute(url='https://www.tiktok.com/@user/video/123', chat_id=456)
        assert result.success is True
        mock_enqueue_stats.enqueue.assert_called_once()

    def test_enqueued_with_correct_url(self, mock_enqueue_stats):
        from django.core.cache import cache
        cache.clear()
        url = 'https://www.instagram.com/p/ABC123/'
        tool = GetPostStatsTool()
        tool.execute(url=url, chat_id=789)
        call_kwargs = mock_enqueue_stats.enqueue.call_args[1]
        assert call_kwargs['url'] == url

    def test_unknown_platform_returns_error(self, mock_enqueue_stats):
        from django.core.cache import cache
        cache.clear()
        tool = GetPostStatsTool()
        result = tool.execute(url='https://www.youtube.com/watch?v=ABC', chat_id=123)
        assert result.success is False
        assert 'reconozco' in result.error.lower()
        mock_enqueue_stats.enqueue.assert_not_called()

    def test_url_without_https_not_crash(self, mock_enqueue_stats):
        from django.core.cache import cache
        cache.clear()
        tool = GetPostStatsTool()
        # La validación de http la hace el handler de Telegram, aquí solo chequeamos plataforma
        result = tool.execute(url='tiktok.com/video/123', chat_id=123)
        assert isinstance(result, ToolResult)

    def test_enqueues_stats_n8n_job(self, mock_enqueue_stats):
        """GetPostStatsTool encola stats_n8n_job (migrado de scrape_post_job a n8n)."""
        from django.core.cache import cache
        cache.clear()
        tool = GetPostStatsTool()
        result = tool.execute(url='https://www.tiktok.com/@u/video/1', chat_id=888888)
        assert result.success is True
        mock_enqueue_stats.enqueue.assert_called_once()
        enqueue_args = mock_enqueue_stats.enqueue.call_args
        assert 'stats_n8n_job' in enqueue_args[0][0]

    def test_result_mentions_platform(self, mock_enqueue_stats):
        from django.core.cache import cache
        cache.clear()
        tool = GetPostStatsTool()
        result = tool.execute(url='https://www.tiktok.com/@user/video/123', chat_id=123)
        assert 'tiktok' in result.content.lower() or 'Tiktok' in result.content
        cache.clear()


# ─── scrape_post_job ──────────────────────────────────────────────────────

class TestScrapePostJob:
    def test_sends_stats_on_success(self):
        from core.agent.infrastructure.jobs import scrape_post_job
        mock_stats = PostStats(
            platform='tiktok',
            url='https://tiktok.com/v/1',
            likes='1.5K',
            views='50K',
        )
        with patch('core.agent.infrastructure.browser.scrape_post_stats', return_value=mock_stats), \
             patch('core.agent.infrastructure.jobs._send_telegram') as mock_tg, \
             patch('core.agent.infrastructure.jobs._get_browser_cookies', return_value=[]):
            scrape_post_job(url='https://tiktok.com/v/1', chat_id=123)

        mock_tg.assert_called_once()
        msg = mock_tg.call_args[0][1]
        assert '1.5K' in msg or '50K' in msg

    def test_sends_error_on_exception(self):
        from core.agent.infrastructure.jobs import scrape_post_job
        with patch('core.agent.infrastructure.browser.scrape_post_stats', side_effect=Exception('crash')), \
             patch('core.agent.infrastructure.jobs._send_telegram') as mock_tg, \
             patch('core.agent.infrastructure.jobs._get_browser_cookies', return_value=[]):
            scrape_post_job(url='https://tiktok.com/v/1', chat_id=123)

        mock_tg.assert_called_once()
        assert 'error' in mock_tg.call_args[0][1].lower()


# ─── BrowserSession model ─────────────────────────────────────────────────

class TestBrowserSession:
    def test_create_session(self, db):
        from core.agent.infrastructure.models import BrowserSession
        session = BrowserSession.objects.create(
            platform='instagram',
            username='testuser',
            cookies=[{'name': 'sessionid', 'value': 'abc123'}],
        )
        assert session.id is not None
        assert session.is_valid is True

    def test_unique_per_platform_username(self, db):
        from core.agent.infrastructure.models import BrowserSession
        from django.db import IntegrityError
        BrowserSession.objects.create(platform='tiktok', username='user1', cookies=[])
        with pytest.raises(IntegrityError):
            BrowserSession.objects.create(platform='tiktok', username='user1', cookies=[])

    def test_get_cookies_for_platform(self, db):
        from core.agent.infrastructure.models import BrowserSession
        cookies = [{'name': 'auth', 'value': 'token123'}]
        BrowserSession.objects.create(platform='instagram', username='anuar', cookies=cookies)
        session = BrowserSession.objects.filter(platform='instagram', is_valid=True).first()
        assert session.cookies == cookies
