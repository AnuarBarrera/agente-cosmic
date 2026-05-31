"""Tests Sprint 14C — Google Sheets via n8n."""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from django.test import override_settings

pytestmark = pytest.mark.django_db


class TestSheetsExportJob:
    def test_reads_prospect_leads_and_dispatches_to_n8n(self):
        """sheets_export_job reads ProspectLead from DB and dispatches to n8n."""
        from core.agent.infrastructure.models import ProspectLead
        from core.agent.infrastructure.jobs import sheets_export_job

        ProspectLead.objects.create(
            place_id='test_place_001', chat_id='12345',
            name='Plomería López', phone='8112345678',
            address='Monterrey, NL', score=7,
        )
        ProspectLead.objects.create(
            place_id='test_place_002', chat_id='12345',
            name='Fontanería García', phone='8119876543',
            address='San Pedro, NL', score=5,
        )

        mock_dispatch = MagicMock()

        with patch('core.agent.infrastructure.jobs.N8nClient') as MockN8n, \
             override_settings(
                 N8N_WORKFLOW_SHEETS_EXPORT='sheets_export_leads',
                 GOOGLE_SHEETS_LEADS_ID='spreadsheet_abc123',
             ):
            MockN8n.return_value.dispatch = mock_dispatch
            sheets_export_job(chat_id=12345)

        mock_dispatch.assert_called_once()
        call_kwargs = mock_dispatch.call_args[1]
        assert call_kwargs['workflow_id'] == 'sheets_export_leads'
        leads_sent = call_kwargs['params']['leads']
        assert len(leads_sent) == 2
        names = [lead['name'] for lead in leads_sent]
        assert 'Plomería López' in names

    def test_sends_error_when_no_workflow_configured(self):
        """If N8N_WORKFLOW_SHEETS_EXPORT is not set, sends error via Telegram."""
        from core.agent.infrastructure.jobs import sheets_export_job

        with patch('core.agent.infrastructure.jobs._send_telegram') as mock_tg, \
             override_settings(N8N_WORKFLOW_SHEETS_EXPORT=''):
            sheets_export_job(chat_id=12345)

        mock_tg.assert_called_once()
        assert '❌' in mock_tg.call_args[0][1]

    def test_sends_message_when_no_leads_found(self):
        """If there are no leads for chat_id, informs the user."""
        from core.agent.infrastructure.jobs import sheets_export_job

        with patch('core.agent.infrastructure.jobs._send_telegram') as mock_tg, \
             override_settings(
                 N8N_WORKFLOW_SHEETS_EXPORT='sheets_export_leads',
                 GOOGLE_SHEETS_LEADS_ID='abc',
             ):
            sheets_export_job(chat_id=99999)

        mock_tg.assert_called_once()
        msg = mock_tg.call_args[0][1]
        assert 'leads' in msg.lower() or 'prospectos' in msg.lower()


class TestSheetsReadJob:
    def test_dispatches_to_n8n_with_sheet_params(self):
        """sheets_read_job dispatches to n8n with sheet_id and range."""
        from core.agent.infrastructure.jobs import sheets_read_job

        mock_dispatch = MagicMock()

        with patch('core.agent.infrastructure.jobs.N8nClient') as MockN8n, \
             override_settings(
                 N8N_WORKFLOW_SHEETS_READ='sheets_read',
                 GOOGLE_SHEETS_LEADS_ID='spreadsheet_abc123',
             ):
            MockN8n.return_value.dispatch = mock_dispatch
            sheets_read_job(chat_id=12345)

        mock_dispatch.assert_called_once()
        call_kwargs = mock_dispatch.call_args[1]
        assert call_kwargs['workflow_id'] == 'sheets_read'
        assert 'sheet_id' in call_kwargs['params']

    def test_sends_error_when_workflow_not_configured(self):
        """If N8N_WORKFLOW_SHEETS_READ is not set, sends error."""
        from core.agent.infrastructure.jobs import sheets_read_job

        with patch('core.agent.infrastructure.jobs._send_telegram') as mock_tg, \
             override_settings(N8N_WORKFLOW_SHEETS_READ=''):
            sheets_read_job(chat_id=12345)

        mock_tg.assert_called_once()
        assert '❌' in mock_tg.call_args[0][1]


class TestSheetsHandlers:
    @pytest.mark.asyncio
    async def test_cmd_exportar_leads_enqueues_job(self):
        from core.agent.management.commands.run_telegram_bot import cmd_exportar
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_user.username = 'anuar'
        update.effective_user.full_name = 'Anuar'
        update.effective_chat.id = 12345
        context = MagicMock()
        context.args = ['leads']

        session = MagicMock(is_authorized=True, id=1)
        with patch('core.agent.management.commands.run_telegram_bot.get_or_create_session',
                   return_value=session), \
             patch('core.agent.management.commands.run_telegram_bot.django_rq') as mock_rq:
            mock_queue = MagicMock()
            mock_rq.get_queue.return_value = mock_queue
            await cmd_exportar(update, context)

        mock_queue.enqueue.assert_called_once()
        assert 'sheets_export_job' in str(mock_queue.enqueue.call_args)

    @pytest.mark.asyncio
    async def test_cmd_exportar_shows_usage_when_no_args(self):
        from core.agent.management.commands.run_telegram_bot import cmd_exportar
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_user.username = 'anuar'
        update.effective_user.full_name = 'Anuar'
        update.effective_chat.id = 12345
        context = MagicMock()
        context.args = []

        await cmd_exportar(update, context)
        update.message.reply_text.assert_called_once()
        assert '/exportar' in update.message.reply_text.call_args[0][0]

    @pytest.mark.asyncio
    async def test_cmd_importar_enqueues_job(self):
        from core.agent.management.commands.run_telegram_bot import cmd_importar
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_user.username = 'anuar'
        update.effective_user.full_name = 'Anuar'
        update.effective_chat.id = 12345
        context = MagicMock()
        context.args = []

        session = MagicMock(is_authorized=True, id=1)
        with patch('core.agent.management.commands.run_telegram_bot.get_or_create_session',
                   return_value=session), \
             patch('core.agent.management.commands.run_telegram_bot.django_rq') as mock_rq:
            mock_queue = MagicMock()
            mock_rq.get_queue.return_value = mock_queue
            await cmd_importar(update, context)

        mock_queue.enqueue.assert_called_once()
        assert 'sheets_read_job' in str(mock_queue.enqueue.call_args)
