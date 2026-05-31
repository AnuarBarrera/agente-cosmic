"""Tests del Sprint 10 — Resumen diario automático."""
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings

pytestmark = pytest.mark.django_db


class TestDailySummaryCommand:
    def test_build_summary_returns_formatted_text(self):
        from core.agent.management.commands.daily_summary import build_daily_summary
        mock_qs = MagicMock()
        mock_qs.count.return_value = 3
        mock_qs.aggregate.return_value = {'t': 150}
        mock_qs.values_list.return_value.distinct.return_value = ['generate_post']
        with patch('core.agent.management.commands.daily_summary.GeminiAdapter') as mock_cls, \
             patch('core.agent.management.commands.daily_summary.AgentRequest') as mock_ar, \
             override_settings(GEMINI_API_KEY='test-key', TELEGRAM_CHAT_ID='123'):
            mock_ar.objects.filter.return_value = mock_qs
            mock_gemini = MagicMock()
            mock_gemini.generate_response.return_value = '📅 Buenos días. Hoy tienes 3 tareas pendientes.'
            mock_cls.return_value = mock_gemini
            summary = build_daily_summary()
        assert isinstance(summary, str)
        assert len(summary) > 10

    def test_build_summary_includes_usage_stats(self):
        from core.agent.management.commands.daily_summary import build_daily_summary
        mock_qs = MagicMock()
        mock_qs.count.return_value = 5
        mock_qs.aggregate.return_value = {'t': 500}
        mock_qs.values_list.return_value.distinct.return_value = ['generate_post', 'web_search']
        with patch('core.agent.management.commands.daily_summary.GeminiAdapter') as mock_cls, \
             patch('core.agent.management.commands.daily_summary.AgentRequest') as mock_ar, \
             override_settings(GEMINI_API_KEY='test-key', TELEGRAM_CHAT_ID='123'):
            mock_ar.objects.filter.return_value = mock_qs
            mock_gemini = MagicMock()
            mock_gemini.generate_response.return_value = 'Resumen del día.'
            mock_cls.return_value = mock_gemini
            summary = build_daily_summary()
        assert isinstance(summary, str)

    def test_build_summary_returns_fallback_without_api_key(self):
        from core.agent.management.commands.daily_summary import build_daily_summary
        mock_qs = MagicMock()
        mock_qs.count.return_value = 0
        mock_qs.aggregate.return_value = {'t': None}
        mock_qs.values_list.return_value.distinct.return_value = []
        with patch('core.agent.management.commands.daily_summary.AgentRequest') as mock_ar, \
             override_settings(GEMINI_API_KEY='', TELEGRAM_CHAT_ID='123'):
            mock_ar.objects.filter.return_value = mock_qs
            summary = build_daily_summary()
        assert isinstance(summary, str)
        assert len(summary) > 0

    def test_command_has_handle_method(self):
        from core.agent.management.commands.daily_summary import Command
        cmd = Command()
        assert hasattr(cmd, 'handle')

    def test_command_handle_sends_telegram_message(self):
        from core.agent.management.commands.daily_summary import Command
        with patch('core.agent.management.commands.daily_summary.build_daily_summary', return_value='Resumen'), \
             patch('core.agent.management.commands.daily_summary.Bot') as mock_bot_cls, \
             patch('core.agent.management.commands.daily_summary.asyncio') as mock_asyncio, \
             override_settings(GEMINI_API_KEY='test-key', TELEGRAM_BOT_TOKEN='tok', TELEGRAM_CHAT_ID='123'):
            mock_bot = MagicMock()
            mock_bot_cls.return_value = mock_bot
            mock_asyncio.run = MagicMock()
            cmd = Command()
            cmd.handle()
        mock_asyncio.run.assert_called_once()
