"""Tests Sprint 14B — Google Calendar via n8n."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from django.test import override_settings

pytestmark = pytest.mark.django_db


class TestCalendarCreateJob:
    def test_gemini_extracts_event_data_and_dispatches_to_n8n(self):
        """calendar_create_job extracts event with Gemini and calls N8nClient.dispatch."""
        from core.agent.infrastructure.jobs import calendar_create_job

        mock_gemini = MagicMock()
        mock_gemini.generate_response.return_value = (
            '{"title": "Llamada con Carlos", '
            '"start_datetime": "2026-05-25T15:00:00-06:00", '
            '"end_datetime": "2026-05-25T16:00:00-06:00", '
            '"description": ""}'
        )
        mock_dispatch = MagicMock()

        # GeminiAdapter is imported inside calendar_create_job, patch at source module
        with patch('core.agent.infrastructure.gemini_adapter.GeminiAdapter', return_value=mock_gemini), \
             patch('core.agent.infrastructure.jobs.N8nClient') as MockN8n, \
             override_settings(
                 GEMINI_API_KEY='test-key',
                 AI_MODEL='gemini-3.5-flash',
                 N8N_WORKFLOW_CALENDAR_CREATE='google_calendar_create',
             ):
            MockN8n.return_value.dispatch = mock_dispatch
            calendar_create_job(
                description='Llama con Carlos el lunes a las 3pm',
                chat_id=12345,
            )

        mock_dispatch.assert_called_once()
        call_kwargs = mock_dispatch.call_args[1]
        assert call_kwargs['workflow_id'] == 'google_calendar_create'
        assert call_kwargs['params']['title'] == 'Llamada con Carlos'

    def test_sends_error_on_gemini_failure(self):
        """If Gemini fails to parse, sends error via Telegram."""
        from core.agent.infrastructure.jobs import calendar_create_job

        mock_gemini = MagicMock()
        mock_gemini.generate_response.side_effect = Exception('API error')

        with patch('core.agent.infrastructure.gemini_adapter.GeminiAdapter', return_value=mock_gemini), \
             patch('core.agent.infrastructure.jobs._send_telegram') as mock_tg, \
             override_settings(
                 GEMINI_API_KEY='test-key',
                 AI_MODEL='gemini-3.5-flash',
                 N8N_WORKFLOW_CALENDAR_CREATE='google_calendar_create',
             ):
            calendar_create_job(description='reunión', chat_id=12345)

        mock_tg.assert_called_once()
        assert '❌' in mock_tg.call_args[0][1]


class TestCalendarListJob:
    def test_dispatches_to_n8n_with_days_param(self):
        """calendar_list_job dispatches to n8n workflow with days parameter."""
        from core.agent.infrastructure.jobs import calendar_list_job

        mock_dispatch = MagicMock()

        with patch('core.agent.infrastructure.jobs.N8nClient') as MockN8n, \
             override_settings(N8N_WORKFLOW_CALENDAR_LIST='google_calendar_list'):
            MockN8n.return_value.dispatch = mock_dispatch
            calendar_list_job(days=7, chat_id=12345)

        mock_dispatch.assert_called_once()
        call_kwargs = mock_dispatch.call_args[1]
        assert call_kwargs['workflow_id'] == 'google_calendar_list'
        assert call_kwargs['params']['days'] == 7

    def test_sends_error_when_workflow_not_configured(self):
        """If N8N_WORKFLOW_CALENDAR_LIST is not set, sends error via Telegram."""
        from core.agent.infrastructure.jobs import calendar_list_job

        with patch('core.agent.infrastructure.jobs._send_telegram') as mock_tg, \
             override_settings(N8N_WORKFLOW_CALENDAR_LIST=''):
            calendar_list_job(days=7, chat_id=12345)

        mock_tg.assert_called_once()
        assert '❌' in mock_tg.call_args[0][1]


class TestCalendarHandlers:
    @pytest.mark.asyncio
    async def test_cmd_agenda_enqueues_job(self):
        from core.agent.management.commands.run_telegram_bot import cmd_agenda
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_user.username = 'anuar'
        update.effective_user.full_name = 'Anuar'
        update.effective_chat.id = 12345
        context = MagicMock()
        context.args = ['Llamada', 'con', 'Carlos', 'el', 'viernes', 'a', 'las', '3pm']

        session = MagicMock(is_authorized=True, id=1)
        with patch('core.agent.management.commands.run_telegram_bot.get_or_create_session',
                   return_value=session), \
             patch('core.agent.management.commands.run_telegram_bot.django_rq') as mock_rq:
            mock_queue = MagicMock()
            mock_rq.get_queue.return_value = mock_queue
            await cmd_agenda(update, context)

        mock_queue.enqueue.assert_called_once()
        assert 'calendar_create_job' in str(mock_queue.enqueue.call_args)
        assert 'chat_id' in str(mock_queue.enqueue.call_args)

    @pytest.mark.asyncio
    async def test_cmd_agenda_shows_usage_when_no_args(self):
        from core.agent.management.commands.run_telegram_bot import cmd_agenda
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_user.username = 'anuar'
        update.effective_user.full_name = 'Anuar'
        update.effective_chat.id = 12345
        context = MagicMock()
        context.args = []

        await cmd_agenda(update, context)
        update.message.reply_text.assert_called_once()
        assert '/agenda' in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cmd_calendario_enqueues_job(self):
        from core.agent.management.commands.run_telegram_bot import cmd_calendario
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_user.username = 'anuar'
        update.effective_user.full_name = 'Anuar'
        update.effective_chat.id = 12345
        context = MagicMock()
        context.args = ['7']

        session = MagicMock(is_authorized=True, id=1)
        with patch('core.agent.management.commands.run_telegram_bot.get_or_create_session',
                   return_value=session), \
             patch('core.agent.management.commands.run_telegram_bot.django_rq') as mock_rq:
            mock_queue = MagicMock()
            mock_rq.get_queue.return_value = mock_queue
            await cmd_calendario(update, context)

        mock_queue.enqueue.assert_called_once()
        assert 'calendar_list_job' in str(mock_queue.enqueue.call_args)
