"""Tests Sprint 14A — Multi-usuario: roles, guards, /usuarios."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from django.test import override_settings

pytestmark = pytest.mark.django_db


class TestAgentSessionRole:
    def test_default_role_is_viewer(self):
        from core.agent.infrastructure.models import AgentSession
        s = AgentSession.objects.create(chat_id=11100, is_authorized=True)
        assert s.role == 'viewer'

    def test_role_choices_include_admin_and_viewer(self):
        from core.agent.infrastructure.models import AgentSession
        choices = dict(AgentSession.ROLE_CHOICES)
        assert 'admin' in choices
        assert 'viewer' in choices

    def test_admin_role_can_be_set(self):
        from core.agent.infrastructure.models import AgentSession
        s = AgentSession.objects.create(chat_id=11101, is_authorized=True, role='admin')
        s.refresh_from_db()
        assert s.role == 'admin'


class TestRoleAssignment:
    def test_admin_chat_id_gets_admin_role(self):
        from core.agent.infrastructure.repositories import DjangoSessionRepository
        with override_settings(
            TELEGRAM_ADMIN_CHAT_IDS=[11200],
            TELEGRAM_AUTHORIZED_CHAT_IDS=[11200, 11201],
        ):
            repo = DjangoSessionRepository()
            session = repo.get_or_create(11200, 'admin_user', 'Admin User')
        assert session.role == 'admin'
        assert session.is_authorized is True

    def test_authorized_non_admin_gets_viewer_role(self):
        from core.agent.infrastructure.repositories import DjangoSessionRepository
        with override_settings(
            TELEGRAM_ADMIN_CHAT_IDS=[11200],
            TELEGRAM_AUTHORIZED_CHAT_IDS=[11200, 11201],
        ):
            repo = DjangoSessionRepository()
            session = repo.get_or_create(11201, 'viewer_user', 'Viewer User')
        assert session.role == 'viewer'
        assert session.is_authorized is True

    def test_unauthorized_user_is_not_authorized(self):
        from core.agent.infrastructure.repositories import DjangoSessionRepository
        with override_settings(
            TELEGRAM_ADMIN_CHAT_IDS=[11200],
            TELEGRAM_AUTHORIZED_CHAT_IDS=[11200, 11201],
        ):
            repo = DjangoSessionRepository()
            session = repo.get_or_create(99999, 'unknown', 'Unknown')
        assert session.is_authorized is False

    def test_role_updates_when_promoted_to_admin(self):
        """If a viewer chat_id is promoted to admin in env, the next get_or_create updates the role."""
        from core.agent.infrastructure.repositories import DjangoSessionRepository
        # First time: viewer
        with override_settings(
            TELEGRAM_ADMIN_CHAT_IDS=[],
            TELEGRAM_AUTHORIZED_CHAT_IDS=[11202],
        ):
            repo = DjangoSessionRepository()
            repo.get_or_create(11202, 'user', 'User')
        # Second time: promoted to admin
        with override_settings(
            TELEGRAM_ADMIN_CHAT_IDS=[11202],
            TELEGRAM_AUTHORIZED_CHAT_IDS=[11202],
        ):
            repo = DjangoSessionRepository()
            session = repo.get_or_create(11202, 'user', 'User')
        assert session.role == 'admin'


class TestAdminGuard:
    @pytest.mark.asyncio
    async def test_viewer_blocked_from_cmd_usuarios(self):
        from core.agent.management.commands.run_telegram_bot import cmd_usuarios
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_user.username = 'viewer'
        update.effective_user.full_name = 'Viewer'
        update.effective_chat.id = 300
        context = MagicMock()

        viewer_session = MagicMock(is_authorized=True, role='viewer')
        with patch('core.agent.management.commands.run_telegram_bot.get_or_create_session',
                   return_value=viewer_session):
            await cmd_usuarios(update, context)

        call_text = update.message.reply_text.call_args[0][0]
        assert 'admin' in call_text.lower() or 'administrador' in call_text.lower()

    @pytest.mark.asyncio
    async def test_unauthorized_blocked_from_cmd_usuarios(self):
        from core.agent.management.commands.run_telegram_bot import cmd_usuarios
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_user.username = 'unknown'
        update.effective_user.full_name = 'Unknown'
        update.effective_chat.id = 301
        context = MagicMock()

        unauth_session = MagicMock(is_authorized=False, role='viewer')
        with patch('core.agent.management.commands.run_telegram_bot.get_or_create_session',
                   return_value=unauth_session):
            await cmd_usuarios(update, context)

        call_text = update.message.reply_text.call_args[0][0]
        assert 'autorizado' in call_text.lower()

    @pytest.mark.asyncio
    async def test_admin_can_run_cmd_usuarios(self):
        from core.agent.management.commands.run_telegram_bot import cmd_usuarios
        update = MagicMock()
        update.message.reply_text = AsyncMock()
        update.effective_user.username = 'admin'
        update.effective_user.full_name = 'Admin'
        update.effective_chat.id = 302
        context = MagicMock()

        admin_session = MagicMock(is_authorized=True, role='admin')
        with patch('core.agent.management.commands.run_telegram_bot.get_or_create_session',
                   return_value=admin_session), \
             patch('core.agent.management.commands.run_telegram_bot._list_sessions',
                   return_value='📋 *Usuarios autorizados:*\n👑 Admin'):
            await cmd_usuarios(update, context)

        # Should have sent the user list (not an error)
        assert update.message.reply_text.called
        call_text = update.message.reply_text.call_args[0][0]
        assert 'Usuarios autorizados' in call_text
