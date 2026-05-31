"""Tests de los handlers del bot de Telegram usando mocks de Update/Context."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from core.agent.management.commands.run_telegram_bot import (
    cmd_start,
    cmd_ayuda,
    cmd_estado,
    handle_message,
)


def make_update(text=None, chat_id=123456, username='anuar', full_name='Anuar Barrera', first_name='Anuar'):
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user.username = username
    update.effective_user.full_name = full_name
    update.effective_user.first_name = first_name
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def make_context():
    context = MagicMock()
    context.bot.send_chat_action = AsyncMock()
    return context


# --- /start ---

@pytest.mark.asyncio
async def test_cmd_start_replies(db):
    update = make_update()
    context = make_context()
    await cmd_start(update, context)
    update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_cmd_start_includes_name(db):
    update = make_update(first_name='Anuar')
    context = make_context()
    await cmd_start(update, context)
    reply = update.message.reply_text.call_args[0][0]
    assert 'Anuar' in reply


@pytest.mark.asyncio
async def test_cmd_start_mentions_commands(db):
    update = make_update()
    context = make_context()
    await cmd_start(update, context)
    reply = update.message.reply_text.call_args[0][0]
    assert '/ayuda' in reply


# --- /ayuda ---

@pytest.mark.asyncio
async def test_cmd_ayuda_replies(db):
    update = make_update()
    context = make_context()
    await cmd_ayuda(update, context)
    update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_cmd_ayuda_mentions_features(db):
    update = make_update()
    context = make_context()
    await cmd_ayuda(update, context)
    reply = update.message.reply_text.call_args[0][0]
    assert 'posts' in reply.lower() or 'redes' in reply.lower()


# --- /estado ---

@pytest.mark.asyncio
async def test_cmd_estado_replies(db):
    update = make_update()
    context = make_context()
    await cmd_estado(update, context)
    update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_cmd_estado_shows_model(db):
    from django.test import override_settings
    with override_settings(AI_MODEL='gemini-2.5-flash'):
        update = make_update()
        context = make_context()
        await cmd_estado(update, context)
    reply = update.message.reply_text.call_args[0][0]
    assert 'gemini' in reply.lower()


# --- handle_message ---

@pytest.mark.asyncio
async def test_handle_message_sends_typing_action(db):
    update = make_update(text='Hola')
    context = make_context()
    with patch('core.agent.management.commands.run_telegram_bot.process_message', new=AsyncMock(return_value='Hola!')):
        await handle_message(update, context)
    context.bot.send_chat_action.assert_called_once()


@pytest.mark.asyncio
async def test_handle_message_replies_with_agent_response(db):
    update = make_update(text='Hola')
    context = make_context()
    with patch('core.agent.management.commands.run_telegram_bot.process_message', new=AsyncMock(return_value='Respuesta del agente')):
        await handle_message(update, context)
    update.message.reply_text.assert_called_once_with('Respuesta del agente')


@pytest.mark.asyncio
async def test_handle_message_handles_internal_error(db):
    update = make_update(text='Hola')
    context = make_context()
    with patch('core.agent.management.commands.run_telegram_bot.process_message', new=AsyncMock(side_effect=Exception('crash'))):
        await handle_message(update, context)
    # Debe responder con mensaje de error, no propagar la excepción
    update.message.reply_text.assert_called_once()
    reply = update.message.reply_text.call_args[0][0]
    assert 'error' in reply.lower() or 'intenta' in reply.lower()
