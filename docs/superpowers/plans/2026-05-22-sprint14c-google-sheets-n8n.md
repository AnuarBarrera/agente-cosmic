# Sprint 14C — Google Sheets via n8n Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrar Google Sheets al agente via n8n: exportar prospectos de la BD a una hoja de cálculo (`/exportar leads`) e importar datos desde la hoja (`/importar`).

**Architecture:** Dos nuevos comandos despachan RQ jobs. El job de exportación lee `ProspectLead` de la BD y envía los datos a n8n via `N8nClient`; n8n los escribe en Google Sheets. El job de importación solicita datos a n8n (que lee la hoja) y los recibe via callback. Los resultados de ambos flujos regresan por el callback `/api/n8n/callback/` existente. Los IDs de workflow y el spreadsheet ID van en `.env`.

**Tech Stack:** Django 5.2, N8nClient (existente), RQ workers, n8n native Google Sheets connector, ProspectLead model (existente).

---

## Prerequisito manual: configurar n8n

Antes de ejecutar el código, crear los workflows en n8n (`http://localhost:5678`):

**Workflow 1: `sheets_export_leads`**
- Trigger: Webhook (POST). Recibe: `{ job_id, chat_id, params: { leads: [ {...} ], sheet_id } }`
- Node: Google Sheets → Append Rows (a la hoja configurada con credenciales OAuth)
- Node: HTTP Request → POST callback con `{ job_id, chat_id, status: "ok", data: { rows_added: N } }`

**Workflow 2: `sheets_read`**
- Trigger: Webhook (POST). Recibe: `{ job_id, chat_id, params: { sheet_id, range } }`
- Node: Google Sheets → Get Rows (rango configurado)
- Node: HTTP Request → POST callback con `{ job_id, chat_id, status: "ok", data: { rows: [ {...} ], count: N } }`

Variables en `.env`:
```
N8N_WORKFLOW_SHEETS_EXPORT=sheets_export_leads
N8N_WORKFLOW_SHEETS_READ=sheets_read
GOOGLE_SHEETS_LEADS_ID=<spreadsheet_id>
```

---

## File Structure

- **Modify**: `saas_chatbot/settings.py` — añadir `N8N_WORKFLOW_SHEETS_EXPORT`, `N8N_WORKFLOW_SHEETS_READ`, `GOOGLE_SHEETS_LEADS_ID`
- **Modify**: `core/agent/infrastructure/jobs.py` — añadir `sheets_export_job`, `sheets_read_job`
- **Modify**: `core/agent/management/commands/run_telegram_bot.py` — handlers `cmd_exportar`, `cmd_importar`
- **Create**: `core/agent/tests/test_sprint14_sheets.py`

---

### Task 1: Settings + RQ jobs de Sheets

**Files:**
- Modify: `saas_chatbot/settings.py`
- Modify: `core/agent/infrastructure/jobs.py`
- Test: `core/agent/tests/test_sprint14_sheets.py`

- [ ] **Step 1: Escribir tests fallando**

Crear `core/agent/tests/test_sprint14_sheets.py`:

```python
"""Tests Sprint 14C — Google Sheets via n8n."""
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings

pytestmark = pytest.mark.django_db


class TestSheetsExportJob:
    def test_reads_prospect_leads_and_dispatches_to_n8n(self):
        """sheets_export_job lee ProspectLead de la BD y hace dispatch al workflow de n8n."""
        from core.agent.infrastructure.models import ProspectLead
        from core.agent.infrastructure.jobs import sheets_export_job

        # Crear leads de prueba
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
        """Si N8N_WORKFLOW_SHEETS_EXPORT no está configurado, envía error por Telegram."""
        from core.agent.infrastructure.jobs import sheets_export_job

        with patch('core.agent.infrastructure.jobs._send_telegram') as mock_tg, \
             override_settings(N8N_WORKFLOW_SHEETS_EXPORT=''):
            sheets_export_job(chat_id=12345)

        mock_tg.assert_called_once()
        assert '❌' in mock_tg.call_args[0][1]

    def test_sends_message_when_no_leads_found(self):
        """Si no hay leads en la BD para el chat_id, informa al usuario."""
        from core.agent.infrastructure.jobs import sheets_export_job

        with patch('core.agent.infrastructure.jobs._send_telegram') as mock_tg, \
             override_settings(
                 N8N_WORKFLOW_SHEETS_EXPORT='sheets_export_leads',
                 GOOGLE_SHEETS_LEADS_ID='abc',
             ):
            sheets_export_job(chat_id=99999)  # chat_id sin leads

        mock_tg.assert_called_once()
        msg = mock_tg.call_args[0][1]
        assert 'leads' in msg.lower() or 'prospectos' in msg.lower()


class TestSheetsReadJob:
    def test_dispatches_to_n8n_with_sheet_params(self):
        """sheets_read_job hace dispatch al workflow de n8n con sheet_id y range."""
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
        """Si N8N_WORKFLOW_SHEETS_READ no está configurado, envía error."""
        from core.agent.infrastructure.jobs import sheets_read_job

        with patch('core.agent.infrastructure.jobs._send_telegram') as mock_tg, \
             override_settings(N8N_WORKFLOW_SHEETS_READ=''):
            sheets_read_job(chat_id=12345)

        mock_tg.assert_called_once()
        assert '❌' in mock_tg.call_args[0][1]
```

- [ ] **Step 2: Ejecutar tests para verificar que fallan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint14_sheets.py -v
```
Expected: FAIL — `ImportError: cannot import name 'sheets_export_job' from 'core.agent.infrastructure.jobs'`

- [ ] **Step 3: Añadir settings de Sheets**

En `saas_chatbot/settings.py`, en el bloque de Agent Settings:

```python
N8N_WORKFLOW_SHEETS_EXPORT = get_env('N8N_WORKFLOW_SHEETS_EXPORT', default='')
N8N_WORKFLOW_SHEETS_READ = get_env('N8N_WORKFLOW_SHEETS_READ', default='')
GOOGLE_SHEETS_LEADS_ID = get_env('GOOGLE_SHEETS_LEADS_ID', default='')
```

- [ ] **Step 4: Añadir `sheets_export_job` y `sheets_read_job` a jobs.py**

En `core/agent/infrastructure/jobs.py`, añadir al final del archivo:

```python
def sheets_export_job(chat_id: int) -> None:
    """
    Lee ProspectLead de la BD para el chat_id y exporta a Google Sheets via n8n.
    Solo exporta los leads del chat_id solicitante.
    Se ejecuta en rqworker.
    """
    import uuid
    from core.agent.infrastructure.models import PendingJob, ProspectLead

    workflow_id = getattr(settings, 'N8N_WORKFLOW_SHEETS_EXPORT', '')
    sheet_id = getattr(settings, 'GOOGLE_SHEETS_LEADS_ID', '')
    if not workflow_id:
        _send_telegram(chat_id, '❌ Google Sheets no está configurado.')
        return

    leads_qs = ProspectLead.objects.filter(chat_id=str(chat_id)).order_by('-searched_at')
    if not leads_qs.exists():
        _send_telegram(chat_id, '📊 No hay prospectos guardados para exportar. Usa `/prospectar` primero.')
        return

    leads_data = [
        {
            'name': lead.name,
            'phone': lead.phone,
            'address': lead.address,
            'website': lead.website,
            'rating': lead.rating,
            'reviews_total': lead.reviews_total,
            'giro': lead.giro,
            'score': lead.score,
            'contacted': lead.contacted,
            'searched_at': lead.searched_at.isoformat() if lead.searched_at else '',
        }
        for lead in leads_qs
    ]

    job_id = str(uuid.uuid4())
    PendingJob.objects.create(
        job_id=job_id,
        chat_id=str(chat_id),
        command='exportar',
        workflow=workflow_id,
    )

    try:
        N8nClient().dispatch(
            workflow_id=workflow_id,
            params={'leads': leads_data, 'sheet_id': sheet_id},
            job_id=job_id,
            chat_id=str(chat_id),
        )
    except Exception as e:
        logger.error(f'Error dispatch sheets_export: {e}')
        _send_telegram(chat_id, f'❌ Error al exportar a Google Sheets: {e}')


def sheets_read_job(chat_id: int, sheet_range: str = 'A:Z') -> None:
    """
    Lee datos desde Google Sheets via n8n y los envía al usuario.
    Se ejecuta en rqworker.
    """
    import uuid
    from core.agent.infrastructure.models import PendingJob

    workflow_id = getattr(settings, 'N8N_WORKFLOW_SHEETS_READ', '')
    sheet_id = getattr(settings, 'GOOGLE_SHEETS_LEADS_ID', '')
    if not workflow_id:
        _send_telegram(chat_id, '❌ Google Sheets no está configurado.')
        return

    job_id = str(uuid.uuid4())
    PendingJob.objects.create(
        job_id=job_id,
        chat_id=str(chat_id),
        command='importar',
        workflow=workflow_id,
    )

    try:
        N8nClient().dispatch(
            workflow_id=workflow_id,
            params={'sheet_id': sheet_id, 'range': sheet_range},
            job_id=job_id,
            chat_id=str(chat_id),
        )
    except Exception as e:
        logger.error(f'Error dispatch sheets_read: {e}')
        _send_telegram(chat_id, f'❌ Error al leer Google Sheets: {e}')
```

- [ ] **Step 5: Verificar que los tests pasan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint14_sheets.py -v
```
Expected: PASS (5 tests)

- [ ] **Step 6: Commit**

```bash
git add saas_chatbot/settings.py core/agent/infrastructure/jobs.py core/agent/tests/test_sprint14_sheets.py
git commit -m "feat: sheets_export_job and sheets_read_job via n8n Google Sheets"
```

---

### Task 2: Handlers `/exportar` y `/importar` en el bot

**Files:**
- Modify: `core/agent/management/commands/run_telegram_bot.py`
- Test: `core/agent/tests/test_sprint14_sheets.py`

- [ ] **Step 1: Escribir tests fallando**

Añadir a `core/agent/tests/test_sprint14_sheets.py`:

```python
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
```

- [ ] **Step 2: Ejecutar tests para verificar que fallan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint14_sheets.py::TestSheetsHandlers -v
```
Expected: FAIL — `ImportError: cannot import name 'cmd_exportar'`

- [ ] **Step 3: Añadir `cmd_exportar` y `cmd_importar` al bot**

En `core/agent/management/commands/run_telegram_bot.py`, añadir después de `cmd_calendario`:

```python
async def cmd_exportar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /exportar leads
    Exporta los prospectos guardados a Google Sheets.
    """
    target = ' '.join(context.args).strip().lower() if context.args else ''
    if not target:
        await update.message.reply_text(
            '📊 Uso: `/exportar <qué exportar>`\n\n'
            'Opciones disponibles:\n'
            '`/exportar leads` — exporta todos tus prospectos a Google Sheets',
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

    if target == 'leads':
        await update.message.reply_text('📊 Exportando prospectos a Google Sheets... Te aviso cuando esté listo.')
        queue = django_rq.get_queue('default')
        queue.enqueue(
            'core.agent.infrastructure.jobs.sheets_export_job',
            chat_id=update.effective_chat.id,
            job_timeout=60,
        )
    else:
        await update.message.reply_text(
            f'❌ Opción no reconocida: `{target}`\n'
            'Usa `/exportar leads` para exportar prospectos.',
            parse_mode='Markdown',
        )


async def cmd_importar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /importar
    Lee datos desde Google Sheets y los muestra.
    """
    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text('No estás autorizado para usar este agente.')
        return

    await update.message.reply_text('📊 Leyendo tu Google Sheet... Te aviso cuando tenga los datos.')
    queue = django_rq.get_queue('default')
    queue.enqueue(
        'core.agent.infrastructure.jobs.sheets_read_job',
        chat_id=update.effective_chat.id,
        job_timeout=30,
    )
```

- [ ] **Step 4: Registrar handlers en `Command.handle`**

```python
        app.add_handler(CommandHandler('exportar', cmd_exportar))
        app.add_handler(CommandHandler('importar', cmd_importar))
```

- [ ] **Step 5: Añadir a `AYUDA_TEXT`**

```python
    "📊 */exportar leads*\n"
    "Exporta tus prospectos guardados a Google Sheets.\n\n"
    "📊 */importar*\n"
    "Lee datos desde tu Google Sheet configurado.\n\n"
```

- [ ] **Step 6: Verificar que los tests pasan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint14_sheets.py -v
```
Expected: PASS (todos los tests)

- [ ] **Step 7: Ejecutar suite completa**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/ -q
```
Expected: todos los tests pasan

- [ ] **Step 8: Commit**

```bash
git add core/agent/management/commands/run_telegram_bot.py core/agent/tests/test_sprint14_sheets.py
git commit -m "feat: /exportar and /importar commands for Google Sheets via n8n"
```

---

## Configuración en .env

Añadir al `.env` (o `.env.prod`):
```
N8N_WORKFLOW_SHEETS_EXPORT=sheets_export_leads
N8N_WORKFLOW_SHEETS_READ=sheets_read
GOOGLE_SHEETS_LEADS_ID=<spreadsheet_id_de_Google_Sheets>
```

Para obtener el `spreadsheet_id`: abrir la hoja en el navegador, el ID está en la URL: `docs.google.com/spreadsheets/d/**<este_es_el_id>**/edit`

Reiniciar containers:
```bash
docker compose up -d
```

## Verificación final en Telegram

1. `/exportar leads` → responde "Exportando..." y luego (via callback n8n) confirma cuántas filas se añadieron
2. `/importar` → responde "Leyendo..." y luego (via callback n8n) muestra un resumen de los datos
3. `/exportar` sin argumentos → muestra mensaje de uso
4. `/exportar metricas` (opción no válida) → muestra error claro
