# Sprint 14B — Google Calendar via n8n Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrar Google Calendar al agente via n8n: crear eventos con lenguaje natural (`/agenda`) y listar próximos eventos (`/calendario`).

**Architecture:** Dos nuevos comandos de Telegram despachan RQ jobs que usan `N8nClient` para llamar workflows de n8n con conectores nativos de Google Calendar. La creación de eventos usa Gemini para parsear la descripción en lenguaje natural y extraer fecha/hora/título. Los resultados regresan via el callback `/api/n8n/callback/` existente, que ya formatea con Gemini y envía por Telegram. Los workflow IDs se configuran en `.env`.

**Tech Stack:** Django 5.2, N8nClient (existente), RQ workers, Gemini (gemini-3.5-flash), n8n native Google Calendar connector, python-telegram-bot.

---

## Prerequisito manual: configurar n8n

Antes de ejecutar el código, crear los workflows en n8n (`http://localhost:5678`):

**Workflow 1: `google_calendar_create`**
- Trigger: Webhook (POST). Recibe: `{ job_id, chat_id, params: { title, start_datetime, end_datetime, description } }`
- Node: Google Calendar → Create Event (credenciales OAuth configuradas en n8n)
- Node: HTTP Request → POST `http://172.17.0.1:8000/api/n8n/callback/` con header `X-N8N-Token: <token>` y body `{ job_id, chat_id, status: "ok", data: { event_id, title, start, end, link } }`

**Workflow 2: `google_calendar_list`**
- Trigger: Webhook (POST). Recibe: `{ job_id, chat_id, params: { days } }`
- Node: Google Calendar → Get All Events (rango: ahora → ahora+days días)
- Node: Set → transforma a `{ events: [ { title, start, end, location } ] }`
- Node: HTTP Request → POST callback con `{ job_id, chat_id, status: "ok", data: { events: [...] } }`

Los IDs de los workflows van en `.env` como:
```
N8N_WORKFLOW_CALENDAR_CREATE=<webhook_path>
N8N_WORKFLOW_CALENDAR_LIST=<webhook_path>
```

---

## File Structure

- **Modify**: `saas_chatbot/settings.py` — añadir `N8N_WORKFLOW_CALENDAR_CREATE`, `N8N_WORKFLOW_CALENDAR_LIST`
- **Modify**: `core/agent/infrastructure/jobs.py` — añadir `calendar_create_job`, `calendar_list_job`
- **Modify**: `core/agent/management/commands/run_telegram_bot.py` — handlers `cmd_agenda`, `cmd_calendario`
- **Create**: `core/agent/tests/test_sprint14_calendar.py`

---

### Task 1: Settings + RQ jobs de calendario

**Files:**
- Modify: `saas_chatbot/settings.py`
- Modify: `core/agent/infrastructure/jobs.py`
- Test: `core/agent/tests/test_sprint14_calendar.py`

- [ ] **Step 1: Escribir tests fallando**

Crear `core/agent/tests/test_sprint14_calendar.py`:

```python
"""Tests Sprint 14B — Google Calendar via n8n."""
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings

pytestmark = pytest.mark.django_db


class TestCalendarCreateJob:
    def test_gemini_extracts_event_data_and_dispatches_to_n8n(self):
        """calendar_create_job extrae datos del evento con Gemini y llama N8nClient.dispatch."""
        from core.agent.infrastructure.jobs import calendar_create_job

        fake_event = {
            'title': 'Llamada con Carlos',
            'start_datetime': '2026-05-25T15:00:00-06:00',
            'end_datetime': '2026-05-25T16:00:00-06:00',
            'description': '',
        }
        mock_gemini = MagicMock()
        mock_gemini.generate_response.return_value = (
            '{"title": "Llamada con Carlos", '
            '"start_datetime": "2026-05-25T15:00:00-06:00", '
            '"end_datetime": "2026-05-25T16:00:00-06:00", '
            '"description": ""}'
        )
        mock_dispatch = MagicMock()

        # GeminiAdapter se importa dentro de calendar_create_job, se parchea en su módulo origen
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
        """Si Gemini falla al parsear, se envía mensaje de error por Telegram."""
        from core.agent.infrastructure.jobs import calendar_create_job

        mock_gemini = MagicMock()
        mock_gemini.generate_response.side_effect = Exception('API error')

        with patch('core.agent.infrastructure.gemini_adapter.GeminiAdapter', return_value=mock_gemini), \
             patch('core.agent.infrastructure.jobs._send_telegram') as mock_tg, \
             override_settings(GEMINI_API_KEY='test-key', AI_MODEL='gemini-3.5-flash'):
            calendar_create_job(description='reunión', chat_id=12345)

        mock_tg.assert_called_once()
        assert '❌' in mock_tg.call_args[0][1]


class TestCalendarListJob:
    def test_dispatches_to_n8n_with_days_param(self):
        """calendar_list_job hace dispatch al workflow de n8n con el parámetro days."""
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
        """Si N8N_WORKFLOW_CALENDAR_LIST no está configurado, envía error por Telegram."""
        from core.agent.infrastructure.jobs import calendar_list_job

        with patch('core.agent.infrastructure.jobs._send_telegram') as mock_tg, \
             override_settings(N8N_WORKFLOW_CALENDAR_LIST=''):
            calendar_list_job(days=7, chat_id=12345)

        mock_tg.assert_called_once()
        assert '❌' in mock_tg.call_args[0][1]
```

- [ ] **Step 2: Ejecutar tests para verificar que fallan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint14_calendar.py -v
```
Expected: FAIL — `ImportError: cannot import name 'calendar_create_job' from 'core.agent.infrastructure.jobs'`

- [ ] **Step 3: Añadir settings de Calendar**

En `saas_chatbot/settings.py`, dentro del bloque de Agent Settings (junto a los otros N8N_ vars):

```python
N8N_WORKFLOW_CALENDAR_CREATE = get_env('N8N_WORKFLOW_CALENDAR_CREATE', default='')
N8N_WORKFLOW_CALENDAR_LIST = get_env('N8N_WORKFLOW_CALENDAR_LIST', default='')
```

- [ ] **Step 4: Añadir `calendar_create_job` y `calendar_list_job` a jobs.py**

En `core/agent/infrastructure/jobs.py`, añadir al final del archivo:

```python
def calendar_create_job(description: str, chat_id: int) -> None:
    """
    Parsea descripción en lenguaje natural con Gemini, crea evento en Google Calendar via n8n.
    Se ejecuta en rqworker.
    """
    from core.agent.infrastructure.gemini_adapter import GeminiAdapter
    from django.utils import timezone
    import uuid

    workflow_id = getattr(settings, 'N8N_WORKFLOW_CALENDAR_CREATE', '')
    if not workflow_id:
        _send_telegram(chat_id, '❌ Google Calendar no está configurado.')
        return

    now = timezone.now().isoformat()
    api_key = getattr(settings, 'GEMINI_API_KEY', '')
    model = getattr(settings, 'AI_MODEL', 'gemini-3.5-flash')

    prompt = (
        f'Fecha y hora actual: {now}\n'
        f'Solicitud del usuario: "{description}"\n\n'
        'Extrae los datos del evento de calendario como JSON con este formato exacto:\n'
        '{\n'
        '  "title": "Título del evento",\n'
        '  "start_datetime": "2026-05-25T15:00:00-06:00",\n'
        '  "end_datetime": "2026-05-25T16:00:00-06:00",\n'
        '  "description": "notas opcionales"\n'
        '}\n\n'
        'Zona horaria: México (UTC-6). Si no se indica hora de fin, suma 1 hora al inicio.\n'
        'Responde SOLO con el JSON, sin texto adicional, sin markdown, sin ```.'
    )

    try:
        gemini = GeminiAdapter()
        raw = gemini.generate_response(prompt=prompt, api_key=api_key, model_name=model)
        # Limpiar posibles backticks de markdown
        raw = raw.strip().strip('`').strip()
        if raw.startswith('json'):
            raw = raw[4:].strip()
        import json
        event_data = json.loads(raw)
    except Exception as e:
        logger.error(f'Error parseando evento de Gemini: {e}')
        _send_telegram(chat_id, f'❌ No pude interpretar la fecha/hora del evento. Intenta con: "/agenda Reunión con X el viernes 23 mayo a las 3pm"')
        return

    from core.agent.infrastructure.models import PendingJob
    job_id = str(uuid.uuid4())
    PendingJob.objects.create(
        job_id=job_id,
        chat_id=str(chat_id),
        command='agenda',
        workflow=workflow_id,
    )

    try:
        N8nClient().dispatch(
            workflow_id=workflow_id,
            params=event_data,
            job_id=job_id,
            chat_id=str(chat_id),
        )
    except Exception as e:
        logger.error(f'Error dispatch calendar_create: {e}')
        _send_telegram(chat_id, f'❌ Error al crear el evento: {e}')


def calendar_list_job(days: int, chat_id: int) -> None:
    """
    Lista los próximos N días de eventos de Google Calendar via n8n.
    Se ejecuta en rqworker.
    """
    import uuid
    from core.agent.infrastructure.models import PendingJob

    workflow_id = getattr(settings, 'N8N_WORKFLOW_CALENDAR_LIST', '')
    if not workflow_id:
        _send_telegram(chat_id, '❌ Google Calendar no está configurado.')
        return

    job_id = str(uuid.uuid4())
    PendingJob.objects.create(
        job_id=job_id,
        chat_id=str(chat_id),
        command='calendario',
        workflow=workflow_id,
    )

    try:
        N8nClient().dispatch(
            workflow_id=workflow_id,
            params={'days': days},
            job_id=job_id,
            chat_id=str(chat_id),
        )
    except Exception as e:
        logger.error(f'Error dispatch calendar_list: {e}')
        _send_telegram(chat_id, f'❌ Error al consultar el calendario: {e}')
```

- [ ] **Step 5: Verificar que los tests pasan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint14_calendar.py -v
```
Expected: PASS (4 tests)

- [ ] **Step 6: Commit**

```bash
git add saas_chatbot/settings.py core/agent/infrastructure/jobs.py core/agent/tests/test_sprint14_calendar.py
git commit -m "feat: calendar_create_job and calendar_list_job via n8n Google Calendar"
```

---

### Task 2: Handlers `/agenda` y `/calendario` en el bot

**Files:**
- Modify: `core/agent/management/commands/run_telegram_bot.py`
- Test: `core/agent/tests/test_sprint14_calendar.py`

- [ ] **Step 1: Escribir tests fallando**

Añadir a `core/agent/tests/test_sprint14_calendar.py`:

```python
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

        session = MagicMock(is_authorized=True, role='admin', id=1)
        with patch('core.agent.management.commands.run_telegram_bot.get_or_create_session',
                   return_value=session), \
             patch('core.agent.management.commands.run_telegram_bot.django_rq') as mock_rq:
            mock_queue = MagicMock()
            mock_rq.get_queue.return_value = mock_queue
            await cmd_agenda(update, context)

        mock_queue.enqueue.assert_called_once()
        enqueue_args = mock_queue.enqueue.call_args
        assert 'calendar_create_job' in str(enqueue_args)
        assert 'chat_id' in str(enqueue_args)

    @pytest.mark.asyncio
    async def test_cmd_agenda_shows_error_when_no_args(self):
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
        enqueue_args = str(mock_queue.enqueue.call_args)
        assert 'calendar_list_job' in enqueue_args
```

- [ ] **Step 2: Ejecutar tests para verificar que fallan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint14_calendar.py::TestCalendarHandlers -v
```
Expected: FAIL — `ImportError: cannot import name 'cmd_agenda'`

- [ ] **Step 3: Añadir `cmd_agenda` y `cmd_calendario` al bot**

En `core/agent/management/commands/run_telegram_bot.py`, añadir después de `cmd_audio` (aprox línea 948):

```python
async def cmd_agenda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /agenda <descripción en lenguaje natural>
    Crea un evento en Google Calendar.
    Ejemplo: /agenda Llamada con Carlos el viernes a las 3pm
    """
    description = ' '.join(context.args).strip() if context.args else ''
    if not description:
        await update.message.reply_text(
            '📅 Uso: `/agenda <descripción del evento>`\n\n'
            'Ejemplos:\n'
            '`/agenda Llamada con Carlos el viernes 23 mayo a las 3pm`\n'
            '`/agenda Reunión de equipo mañana 10am, 2 horas`\n'
            '`/agenda Entrega de propuesta a cliente el lunes a las 9am`',
            parse_mode='Markdown',
        )
        return

    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text('No estás autorizado para usar este agente.')
        return

    await update.message.reply_text('📅 Creando evento en tu calendario... Te aviso cuando esté listo.')
    queue = django_rq.get_queue('default')
    queue.enqueue(
        'core.agent.infrastructure.jobs.calendar_create_job',
        description=description,
        chat_id=update.effective_chat.id,
        job_timeout=60,
    )


async def cmd_calendario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /calendario [dias]
    Muestra los próximos eventos del calendario.
    Ejemplo: /calendario 7
    """
    try:
        days = int(context.args[0]) if context.args else 7
        if days < 1 or days > 90:
            raise ValueError
    except (ValueError, IndexError):
        await update.message.reply_text(
            '📅 Uso: `/calendario [días]`\n'
            'Ejemplo: `/calendario 7` — próximos 7 días\n'
            'Rango válido: 1-90 días.',
            parse_mode='Markdown',
        )
        return

    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text('No estás autorizado para usar este agente.')
        return

    await update.message.reply_text(f'📅 Consultando tu calendario para los próximos {days} días...')
    queue = django_rq.get_queue('default')
    queue.enqueue(
        'core.agent.infrastructure.jobs.calendar_list_job',
        days=days,
        chat_id=update.effective_chat.id,
        job_timeout=30,
    )
```

- [ ] **Step 4: Registrar handlers en `Command.handle`**

En el bloque de `app.add_handler`, añadir:
```python
        app.add_handler(CommandHandler('agenda', cmd_agenda))
        app.add_handler(CommandHandler('calendario', cmd_calendario))
```

- [ ] **Step 5: Añadir los nuevos comandos a `AYUDA_TEXT`**

En la constante `AYUDA_TEXT`, añadir:
```python
    "📅 */agenda <descripción>*\n"
    "Crea un evento en Google Calendar con lenguaje natural.\n"
    "Ejemplo: `/agenda Llamada con Carlos el viernes a las 3pm`\n\n"
    "📅 */calendario [días]*\n"
    "Muestra los próximos eventos del calendario (default: 7 días).\n"
    "Ejemplo: `/calendario 14`\n\n"
```

- [ ] **Step 6: Verificar que los tests pasan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint14_calendar.py -v
```
Expected: PASS (todos los tests)

- [ ] **Step 7: Ejecutar suite completa**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/ -q
```
Expected: todos los tests pasan

- [ ] **Step 8: Commit**

```bash
git add core/agent/management/commands/run_telegram_bot.py core/agent/tests/test_sprint14_calendar.py
git commit -m "feat: /agenda and /calendario commands via Google Calendar n8n workflow"
```

---

## Configuración en .env

Añadir al `.env` (o `.env.prod`) con los paths del webhook de n8n:
```
N8N_WORKFLOW_CALENDAR_CREATE=google_calendar_create
N8N_WORKFLOW_CALENDAR_LIST=google_calendar_list
```
Nota: el path debe coincidir exactamente con el "Path" del Webhook trigger en n8n.

Reiniciar para cargar vars:
```bash
docker compose up -d
```

## Verificación final en Telegram

1. `/agenda Reunión con Carlos el lunes 25 mayo a las 4pm` → debe responder "📅 Creando evento..." y luego (tras callback n8n) confirmar el evento creado
2. `/calendario 7` → debe responder con los eventos de los próximos 7 días
3. `/agenda` sin argumentos → debe mostrar el mensaje de uso
4. `/calendario 0` → debe mostrar error de rango
