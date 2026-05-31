# Sprint 15B — Google Drive via n8n Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Comando `/drive <búsqueda>` que busca archivos en la biblioteca de contenido de Google Drive y retorna nombre + link de los resultados vía Telegram.

**Architecture:** Mismo patrón que Sprint 14 (Calendar/Sheets): bot encola job RQ → `drive_search_job` en rqworker despacha a n8n vía N8nClient → n8n busca en Google Drive con las credenciales OAuth2 ya existentes → callback a Django → Gemini formatea → mensaje a Telegram.

**Tech Stack:** Django RQ, N8nClient, n8n (Google Drive node), python-telegram-bot. Credenciales Google OAuth2 ya configuradas en n8n (mismas que Calendar/Sheets, requiere agregar scope Drive — ver Task 4).

---

## File Structure

| Archivo | Cambio |
|---|---|
| `saas_chatbot/settings.py` | Añadir `N8N_WORKFLOW_DRIVE_SEARCH`, `GOOGLE_DRIVE_FOLDER_ID` |
| `core/agent/infrastructure/jobs.py` | Añadir `drive_search_job()` |
| `core/agent/management/commands/run_telegram_bot.py` | Añadir `cmd_drive` + registrar handler |
| `core/agent/tests/test_sprint15_drive.py` | Nuevo archivo de tests |

**n8n workflow:** `drive_search` (creado en Task 4 desde la UI de n8n en `http://localhost:5678`).

---

### Task 1: Settings + tests fallidos

**Files:**
- Modify: `saas_chatbot/settings.py`
- Create: `core/agent/tests/test_sprint15_drive.py`

- [ ] **Step 1: Añadir settings para Drive**

En `saas_chatbot/settings.py`, después de la línea `GOOGLE_SHEET_ID = ...` (línea ~123), añadir:

```python
N8N_WORKFLOW_DRIVE_SEARCH = get_env('N8N_WORKFLOW_DRIVE_SEARCH', default='')
GOOGLE_DRIVE_FOLDER_ID = get_env('GOOGLE_DRIVE_FOLDER_ID', default='')
```

- [ ] **Step 2: Añadir variables al .env**

Abrir `.env` y añadir al final:

```
N8N_WORKFLOW_DRIVE_SEARCH=drive_search
GOOGLE_DRIVE_FOLDER_ID=<ID de la carpeta de Drive con la biblioteca de contenido>
```

El `GOOGLE_DRIVE_FOLDER_ID` se obtiene de la URL de la carpeta en Google Drive:
`https://drive.google.com/drive/folders/1AbCdEfG...` → el ID es `1AbCdEfG...`

- [ ] **Step 3: Escribir tests fallidos**

Crear `core/agent/tests/test_sprint15_drive.py`:

```python
"""Tests Sprint 15B — drive_search_job y cmd_drive."""
import pytest
from unittest.mock import patch, MagicMock, call
from django.test import override_settings

pytestmark = pytest.mark.django_db

DRIVE_SETTINGS = {
    'N8N_BASE_URL': 'http://172.17.0.1:5678/webhook',
    'N8N_WORKFLOW_DRIVE_SEARCH': 'drive_search',
    'GOOGLE_DRIVE_FOLDER_ID': 'folder_abc123',
    'TELEGRAM_BOT_TOKEN': 'test-token',
    'N8N_CALLBACK_TOKEN': 'test-cb-token',
}


class TestDriveSearchJob:
    @override_settings(**DRIVE_SETTINGS)
    def test_creates_pending_job_and_dispatches(self):
        """drive_search_job crea PendingJob y llama N8nClient.dispatch."""
        from core.agent.infrastructure.jobs import drive_search_job
        from core.agent.infrastructure.models import PendingJob

        with patch('core.agent.infrastructure.jobs.N8nClient') as MockClient:
            mock_instance = MagicMock()
            MockClient.return_value = mock_instance
            drive_search_job(query='propuesta marketing', chat_id=999)

        assert PendingJob.objects.filter(command='drive', workflow='drive_search').exists()
        mock_instance.dispatch.assert_called_once()
        call_kwargs = mock_instance.dispatch.call_args
        assert call_kwargs.kwargs['workflow_id'] == 'drive_search'
        assert call_kwargs.kwargs['params']['query'] == 'propuesta marketing'
        assert call_kwargs.kwargs['params']['folder_id'] == 'folder_abc123'

    @override_settings(**{**DRIVE_SETTINGS, 'N8N_WORKFLOW_DRIVE_SEARCH': ''})
    def test_sends_telegram_when_not_configured(self):
        """drive_search_job notifica vía Telegram si el workflow no está configurado."""
        from core.agent.infrastructure.jobs import drive_search_job

        with patch('core.agent.infrastructure.jobs._send_telegram') as mock_tg:
            drive_search_job(query='test', chat_id=999)

        mock_tg.assert_called_once()
        assert '❌' in mock_tg.call_args[0][1]

    @override_settings(**DRIVE_SETTINGS)
    def test_sends_telegram_on_dispatch_error(self):
        """drive_search_job maneja errores de N8nClient y notifica por Telegram."""
        from core.agent.infrastructure.jobs import drive_search_job

        with patch('core.agent.infrastructure.jobs.N8nClient') as MockClient:
            MockClient.return_value.dispatch.side_effect = Exception('n8n unreachable')
            with patch('core.agent.infrastructure.jobs._send_telegram') as mock_tg:
                drive_search_job(query='test', chat_id=999)

        mock_tg.assert_called_once()
        assert '❌' in mock_tg.call_args[0][1]


class TestDriveCallbackParsing:
    """Verifica que el callback de n8n para 'drive' se procesa correctamente."""

    def _make_callback_request(self, client, data: dict):
        import json
        return client.post(
            '/api/v1/agent/n8n/callback/',
            data=json.dumps(data),
            content_type='application/json',
            HTTP_X_N8N_TOKEN='test-cb-token',
        )

    @override_settings(N8N_CALLBACK_TOKEN='test-cb-token', TELEGRAM_BOT_TOKEN='test-token',
                       AI_MODEL='gemini-3.1-flash-lite', GEMINI_API_KEY='test-key')
    def test_drive_callback_formats_and_sends(self):
        """El callback con command='drive' formatea con Gemini y envía a Telegram."""
        from core.agent.infrastructure.models import PendingJob
        from django.test import Client as DjangoClient
        import uuid

        job_id = str(uuid.uuid4())
        PendingJob.objects.create(job_id=job_id, chat_id='999', command='drive', workflow='drive_search')

        with patch('core.agent.interfaces.n8n_views._format_with_gemini', return_value='📁 Archivos encontrados'):
            with patch('core.agent.interfaces.n8n_views._send_telegram') as mock_tg:
                resp = self._make_callback_request(DjangoClient(), {
                    'job_id': job_id,
                    'chat_id': '999',
                    'status': 'ok',
                    'data': {'files': [{'name': 'propuesta.docx', 'url': 'https://drive.google.com/...'}]},
                })

        assert resp.status_code == 200
        mock_tg.assert_called_once_with('999', '📁 Archivos encontrados')
```

- [ ] **Step 4: Verificar que los tests fallan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint15_drive.py -v 2>&1 | tail -10
```

Resultado esperado: `FAILED` con `ImportError` o `AttributeError` porque `drive_search_job` no existe aún.

- [ ] **Step 5: Commit**

```bash
GIT_EDITOR=true git add saas_chatbot/settings.py core/agent/tests/test_sprint15_drive.py
GIT_EDITOR=true git commit -m "test(sprint15b): failing tests for Drive search job"
```

---

### Task 2: Implementar `drive_search_job` en jobs.py

**Files:**
- Modify: `core/agent/infrastructure/jobs.py`

- [ ] **Step 1: Añadir `drive_search_job` al final de jobs.py**

Abrir `core/agent/infrastructure/jobs.py` y añadir al final:

```python
def drive_search_job(query: str, chat_id: int) -> None:
    """
    Busca archivos en Google Drive via n8n. La respuesta llega por callback.
    Runs in rqworker.
    """
    import uuid
    from core.agent.infrastructure.models import PendingJob

    workflow_id = getattr(settings, 'N8N_WORKFLOW_DRIVE_SEARCH', '')
    folder_id = getattr(settings, 'GOOGLE_DRIVE_FOLDER_ID', '')
    if not workflow_id:
        _send_telegram(chat_id, '❌ Google Drive no está configurado.')
        return

    job_id = str(uuid.uuid4())
    PendingJob.objects.create(
        job_id=job_id,
        chat_id=str(chat_id),
        command='drive',
        workflow=workflow_id,
    )

    try:
        N8nClient().dispatch(
            workflow_id=workflow_id,
            params={'query': query, 'folder_id': folder_id},
            job_id=job_id,
            chat_id=str(chat_id),
        )
    except Exception as e:
        logger.error(f'Error dispatch drive_search: {e}')
        _send_telegram(chat_id, f'❌ Error al buscar en Drive: {e}')
```

- [ ] **Step 2: Correr tests**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint15_drive.py -v
```

Resultado esperado: 4 PASSED.

- [ ] **Step 3: Commit**

```bash
GIT_EDITOR=true git add core/agent/infrastructure/jobs.py
GIT_EDITOR=true git commit -m "feat(sprint15b): add drive_search_job to dispatch Drive search via n8n"
```

---

### Task 3: Añadir comando `/drive` al bot de Telegram

**Files:**
- Modify: `core/agent/management/commands/run_telegram_bot.py`

El archivo es grande. Localizar `cmd_exportar` (línea ~1080) como referencia de los comandos n8n más recientes.

- [ ] **Step 1: Añadir el texto de ayuda para `/drive` en `HELP_TEXT`**

Buscar el bloque `HELP_TEXT` (string largo al inicio del archivo con todos los comandos). Añadir al final de la sección de Google, después del bloque de `/importar`:

```python
    "🗂 */drive <búsqueda>*\n"
    "Busca archivos en tu biblioteca de Google Drive.\n"
    "Ejemplo: `/drive propuesta identidad de marca`\n\n"
```

- [ ] **Step 2: Añadir `cmd_drive` al bot**

Después de la función `cmd_importar` (buscar `async def cmd_importar`), añadir:

```python
async def cmd_drive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/drive <búsqueda> — busca en Google Drive"""
    args = ' '.join(context.args).strip()
    if not args:
        await update.message.reply_text(
            '🗂 Uso: `/drive <búsqueda>`\n'
            'Ejemplo: `/drive propuesta identidad de marca`',
            parse_mode='Markdown',
        )
        return

    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text("No estás autorizado para usar este agente.")
        return

    await update.message.reply_text('🗂 Buscando en Google Drive...', parse_mode='Markdown')
    queue = django_rq.get_queue('default')
    queue.enqueue(
        'core.agent.infrastructure.jobs.drive_search_job',
        kwargs={'query': args, 'chat_id': update.effective_chat.id},
        job_timeout=60,
    )
```

- [ ] **Step 3: Registrar el handler**

Buscar el bloque de `app.add_handler` al final del archivo (línea ~1255) y añadir junto a los comandos de Sheets:

```python
        app.add_handler(CommandHandler('drive', cmd_drive))
```

- [ ] **Step 4: Verificar que el bot arranca sin errores**

```bash
docker compose restart telegram_bot
docker logs chatbot-telegram_bot-1 --tail=20 2>&1
```

Resultado esperado: sin errores de importación.

- [ ] **Step 5: Correr todos los tests del sprint**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint15_drive.py -v
```

Resultado esperado: 4 PASSED.

- [ ] **Step 6: Commit**

```bash
GIT_EDITOR=true git add core/agent/management/commands/run_telegram_bot.py
GIT_EDITOR=true git commit -m "feat(sprint15b): add /drive command to Telegram bot"
```

---

### Task 4: Crear el workflow `drive_search` en n8n

Acceder a n8n en `http://localhost:5678`. Crear un nuevo workflow llamado `drive_search`.

**Credenciales requeridas:** Las credenciales de Google OAuth2 existentes (usadas en Calendar/Sheets) necesitan el scope `https://www.googleapis.com/auth/drive.readonly`. Si la credencial no lo tiene, crear una nueva desde n8n → Settings → Credentials → Google OAuth2 API con los scopes: `drive.readonly`.

**Estructura del workflow (5 nodos):**

- [ ] **Step 1: Nodo Webhook (trigger)**

```json
{
  "name": "Webhook",
  "type": "n8n-nodes-base.webhook",
  "typeVersion": 2,
  "parameters": {
    "httpMethod": "POST",
    "path": "drive_search",
    "responseMode": "responseNode"
  }
}
```

- [ ] **Step 2: Nodo Google Drive — buscar archivos**

```json
{
  "name": "Search Drive",
  "type": "n8n-nodes-base.googleDrive",
  "typeVersion": 3,
  "parameters": {
    "operation": "search",
    "queryString": "={{ $('Webhook').first().json.body.params.query }} and '{{ $('Webhook').first().json.body.params.folder_id }}' in parents",
    "filter": {
      "includeTrashed": false
    },
    "options": {
      "fields": "files(id,name,mimeType,webViewLink,modifiedTime,size)"
    }
  },
  "credentials": {
    "googleDriveOAuth2Api": {
      "id": "<ID de credencial Google Drive>"
    }
  }
}
```

**Nota:** El `queryString` busca archivos que contengan el término en el nombre AND están dentro de la carpeta especificada.

- [ ] **Step 3: Nodo Code — formatear resultados**

```json
{
  "name": "Format Results",
  "type": "n8n-nodes-base.code",
  "parameters": {
    "jsCode": "const wb = $('Webhook').first().json.body;\nconst items = $input.all();\nconst files = items.map(item => ({\n  name: item.json.name,\n  url: item.json.webViewLink,\n  type: item.json.mimeType,\n  modified: item.json.modifiedTime\n}));\nreturn [{ json: {\n  job_id: wb.job_id,\n  chat_id: wb.chat_id,\n  query: wb.params.query,\n  total: files.length,\n  files: files\n}}];"
  }
}
```

- [ ] **Step 4: Nodo HTTP Request — callback a Django**

```json
{
  "name": "Callback Django",
  "type": "n8n-nodes-base.httpRequest",
  "typeVersion": 4,
  "parameters": {
    "method": "POST",
    "url": "http://172.17.0.1:3001/api/v1/agent/n8n/callback/",
    "sendHeaders": true,
    "headerParameters": {
      "parameters": [
        {"name": "X-N8N-Token", "value": "<N8N_CALLBACK_TOKEN del .env>"},
        {"name": "Content-Type", "value": "application/json"}
      ]
    },
    "sendBody": true,
    "contentType": "json",
    "bodyParameters": {
      "parameters": [
        {"name": "job_id",  "value": "={{ $json.job_id }}"},
        {"name": "chat_id", "value": "={{ $json.chat_id }}"},
        {"name": "status",  "value": "ok"},
        {"name": "data",    "value": "={{ JSON.stringify({query: $json.query, total: $json.total, files: $json.files}) }}"}
      ]
    }
  }
}
```

- [ ] **Step 5: Nodo Respond to Webhook**

```json
{
  "name": "Respond to Webhook",
  "type": "n8n-nodes-base.respondToWebhook",
  "parameters": {
    "respondWith": "json",
    "responseBody": "{\"ok\": true}"
  }
}
```

- [ ] **Step 6: Activar el workflow**

Clic en el toggle "Active" del workflow. Verificar que el webhook path sea `drive_search`.

- [ ] **Step 7: Verificar en .env y reiniciar rqworker**

Confirmar que `.env` tiene:
```
N8N_WORKFLOW_DRIVE_SEARCH=drive_search
GOOGLE_DRIVE_FOLDER_ID=<ID real de la carpeta>
```

```bash
docker compose up -d rqworker
```

- [ ] **Step 8: Test manual desde Telegram**

```
/drive propuesta marketing
```

Resultado esperado: el bot responde con lista de archivos encontrados o "0 archivos encontrados para 'propuesta marketing'".

---

## Notas importantes

- Si la búsqueda retorna 0 archivos aunque existan, verificar que los scopes de la credencial Google incluyen `drive.readonly` y que la carpeta es accesible con esa cuenta OAuth.
- El `folder_id` debe ser el ID de la carpeta raíz de la biblioteca (no una subcarpeta). Los archivos en subcarpetas no aparecerán a menos que la búsqueda sea recursiva (requiere ajustar el `queryString` para no filtrar por `parents`).
- Para buscar en toda la unidad (sin filtrar por carpeta), usar `queryString` sin la cláusula `and '...' in parents`.
