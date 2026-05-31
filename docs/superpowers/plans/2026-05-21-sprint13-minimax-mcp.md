# Sprint 13 — Minimax MCP: /imagen, /video y /audio

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrar Minimax como MCP server Docker, reemplazar `/imagen` (Playwright+HTML → API Minimax), y añadir dos comandos nuevos: `/video` (async vía RQ) y `/audio` (síncrono).

**Architecture:** Nuevo Flask server `mcp_servers/minimax/server.py` (misma interfaz que `brave-search-mcp`), envuelve la REST API de Minimax. `/imagen` reemplaza Playwright con `McpClient`. `/video` encola un RQ job (3-5 min de generación) que notifica por Telegram al terminar. `/audio` es síncrono (<30 s).

**Tech Stack:** Flask, httpx, gunicorn (timeout 360 s para video), Minimax REST API (`api.minimax.io`), Docker Compose, Django RQ, python-telegram-bot.

---

### Task 1: Minimax MCP Flask server

**Files:**
- Create: `mcp_servers/minimax/server.py`
- Create: `core/agent/tests/test_sprint13_minimax.py`

- [ ] **Step 1: Escribir los tests iniciales**

```python
# core/agent/tests/test_sprint13_minimax.py
import asyncio
import base64
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from django.test import override_settings

pytestmark = pytest.mark.django_db


class TestGeneratePostImageTool:
    def test_calls_minimax_mcp_and_returns_image_bytes(self):
        from core.agent.infrastructure.tools.image_tools import GeneratePostImageTool
        from core.agent.infrastructure.mcp_client import McpClient
        from core.agent.infrastructure.gemini_adapter import GeminiAdapter
        fake_bytes = b'\x89PNG\r\n\x1a\nFAKE'
        with patch.object(GeminiAdapter, 'generate_response', return_value='a marketing photo'), \
             patch.object(McpClient, 'call', return_value={
                 'image_bytes_b64': base64.b64encode(fake_bytes).decode()
             }), \
             override_settings(GEMINI_API_KEY='k',
                               MCP_SERVERS={'minimax': 'http://minimax-mcp:8080'}):
            result = GeneratePostImageTool().execute(topic='diseño web', platform='instagram')
        assert result.success
        assert result.metadata['image_bytes'] == fake_bytes
        assert result.metadata['filename'].endswith('.jpg')

    def test_returns_error_when_mcp_unavailable(self):
        from core.agent.infrastructure.tools.image_tools import GeneratePostImageTool
        from core.agent.infrastructure.mcp_client import McpClient
        from core.agent.infrastructure.gemini_adapter import GeminiAdapter
        with patch.object(GeminiAdapter, 'generate_response', return_value='a photo'), \
             patch.object(McpClient, 'call', side_effect=Exception('connection refused')), \
             override_settings(GEMINI_API_KEY='k',
                               MCP_SERVERS={'minimax': 'http://minimax-mcp:8080'}):
            result = GeneratePostImageTool().execute(topic='test')
        assert not result.success
        assert 'No pude generar' in result.content

    def test_story_platform_uses_9x16_aspect(self):
        from core.agent.infrastructure.tools.image_tools import GeneratePostImageTool
        from core.agent.infrastructure.mcp_client import McpClient
        from core.agent.infrastructure.gemini_adapter import GeminiAdapter
        fake_bytes = b'JPGDATA'
        with patch.object(GeminiAdapter, 'generate_response', return_value='a photo'), \
             patch.object(McpClient, 'call', return_value={
                 'image_bytes_b64': base64.b64encode(fake_bytes).decode()
             }) as mock_call, \
             override_settings(GEMINI_API_KEY='k',
                               MCP_SERVERS={'minimax': 'http://minimax-mcp:8080'}):
            GeneratePostImageTool().execute(topic='test', platform='story')
        params = mock_call.call_args[0][2]
        assert params['aspect_ratio'] == '9:16'


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
        from core.agent.infrastructure.mcp_client import McpClient
        fake_audio = b'ID3\x03\x00FAKEMP3'
        with patch.object(McpClient, 'call', return_value={
            'audio_bytes_b64': base64.b64encode(fake_audio).decode(),
            'format': 'mp3',
        }), override_settings(MCP_SERVERS={'minimax': 'http://minimax-mcp:8080'}):
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
        assert call_args[0] == 'core.agent.infrastructure.jobs.video_minimax_job'
        assert '⏳' in result.content

    def test_returns_error_when_rq_fails(self):
        from core.agent.infrastructure.tools.media_tools import GenerateVideoTool
        with patch('core.agent.infrastructure.tools.media_tools.django_rq') as mock_rq:
            mock_rq.get_queue.side_effect = Exception('Redis unavailable')
            result = GenerateVideoTool().execute(prompt='test', chat_id=123)
        assert not result.success
```

- [ ] **Step 2: Correr tests para verificar que fallan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_minimax.py -q
```

Expected: múltiples FAILs — los módulos no existen todavía.

- [ ] **Step 3: Crear `mcp_servers/minimax/server.py`**

```python
import os
import time
import base64
import logging
import httpx
from flask import Flask, request, jsonify

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
MINIMAX_API_KEY = os.environ.get('MINIMAX_API_KEY', '')
MINIMAX_BASE = 'https://api.minimax.io'

ASPECT_TO_SIZE = {
    '1:1': '1024x1024',
    '16:9': '1024x576',
    '9:16': '576x1024',
    '4:3': '1024x768',
    '3:4': '768x1024',
}


def _headers():
    return {'Authorization': f'Bearer {MINIMAX_API_KEY}', 'Content-Type': 'application/json'}


def _generate_image(params: dict):
    prompt = params.get('prompt', '')
    aspect_ratio = params.get('aspect_ratio', '1:1')
    size = ASPECT_TO_SIZE.get(aspect_ratio, '1024x1024')
    if not MINIMAX_API_KEY:
        return jsonify({'error': 'MINIMAX_API_KEY not set'}), 500
    resp = httpx.post(
        f'{MINIMAX_BASE}/v1/images/generations',
        headers=_headers(),
        json={'model': 'image-01', 'prompt': prompt, 'n': 1, 'size': size},
        timeout=60.0,
    )
    resp.raise_for_status()
    image_url = resp.json()['data'][0]['url']
    img_resp = httpx.get(image_url, timeout=30.0)
    img_resp.raise_for_status()
    return jsonify({
        'image_url': image_url,
        'image_bytes_b64': base64.b64encode(img_resp.content).decode(),
    })


def _generate_video(params: dict):
    prompt = params.get('prompt', '')
    model = params.get('model', 'T2V-01')
    if not MINIMAX_API_KEY:
        return jsonify({'error': 'MINIMAX_API_KEY not set'}), 500
    submit = httpx.post(
        f'{MINIMAX_BASE}/v1/video_generation',
        headers=_headers(),
        json={'model': model, 'prompt': prompt},
        timeout=30.0,
    )
    submit.raise_for_status()
    task_id = submit.json()['task_id']
    logger.info(f'Video task submitted: {task_id}')
    # Polling máximo 5 minutos (30 × 10 s)
    for attempt in range(30):
        time.sleep(10)
        status_resp = httpx.get(
            f'{MINIMAX_BASE}/v1/query/video_generation?task_id={task_id}',
            headers=_headers(),
            timeout=10.0,
        )
        status_resp.raise_for_status()
        data = status_resp.json()
        state = data.get('status', '')
        logger.info(f'Video {task_id} [{attempt + 1}/30]: {state}')
        if state == 'Success':
            return jsonify({'video_url': data['download_url'], 'task_id': task_id})
        if state in ('Fail', 'Failed'):
            return jsonify({'error': f'Minimax video failed: {data}'}), 500
    return jsonify({'error': 'Timeout: video not ready after 5 minutes'}), 504


def _text_to_audio(params: dict):
    text = params.get('text', '')
    voice_id = params.get('voice_id', 'presenter_male')
    if not MINIMAX_API_KEY:
        return jsonify({'error': 'MINIMAX_API_KEY not set'}), 500
    resp = httpx.post(
        f'{MINIMAX_BASE}/v1/t2a_v2',
        headers=_headers(),
        json={
            'model': 'speech-2.6-hd',
            'text': text,
            'voice_setting': {
                'voice_id': voice_id,
                'speed': 1.0,
                'vol': 1.0,
                'pitch': 0,
            },
            'audio_setting': {
                'sample_rate': 32000,
                'bitrate': 128000,
                'format': 'mp3',
                'channel': 1,
            },
        },
        timeout=60.0,
    )
    resp.raise_for_status()
    data = resp.json()
    # Minimax TTS devuelve audio como hex en audio_info.audio
    audio_hex = data.get('audio_info', {}).get('audio', '')
    if not audio_hex:
        return jsonify({'error': 'No audio in Minimax response', 'raw': data}), 500
    audio_bytes = bytes.fromhex(audio_hex)
    return jsonify({'audio_bytes_b64': base64.b64encode(audio_bytes).decode(), 'format': 'mp3'})


@app.post('/call')
def call_tool():
    body = request.get_json(force=True)
    tool = body.get('tool')
    params = body.get('params', {})
    try:
        if tool == 'generate_image':
            return _generate_image(params)
        if tool == 'generate_video':
            return _generate_video(params)
        if tool == 'text_to_audio':
            return _text_to_audio(params)
        return jsonify({'error': f'Tool not found: {tool}'}), 404
    except httpx.HTTPStatusError as e:
        logger.error(f'Minimax API error [{tool}]: {e.response.text}')
        return jsonify({'error': str(e)}), 502
    except Exception as e:
        logger.error(f'Server error [{tool}]: {e}', exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.get('/health')
def health():
    return jsonify({'status': 'ok', 'api_key_set': bool(MINIMAX_API_KEY)})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, threaded=True)
```

- [ ] **Step 4: Commit**

```bash
git add mcp_servers/minimax/server.py core/agent/tests/test_sprint13_minimax.py
GIT_EDITOR=true git commit -m "feat: add minimax MCP Flask server + sprint13 test scaffold"
```

---

### Task 2: Docker integration

**Files:**
- Create: `mcp_servers/minimax/requirements.txt`
- Create: `mcp_servers/minimax/Dockerfile`
- Modify: `docker-compose.yml`

- [ ] **Step 1: Crear `mcp_servers/minimax/requirements.txt`**

```
flask==3.1.0
httpx==0.28.1
gunicorn==23.0.0
```

- [ ] **Step 2: Crear `mcp_servers/minimax/Dockerfile`**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
EXPOSE 8080
# gunicorn con timeout 360 s para que el worker aguante el polling de video (~5 min)
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "360", "--workers", "2", "server:app"]
```

- [ ] **Step 3: Añadir servicio en `docker-compose.yml`**

Localizar el bloque `brave-search-mcp:` (al final del archivo, antes de `volumes:`) y añadir inmediatamente después:

```yaml
  minimax-mcp:
    build:
      context: ./mcp_servers/minimax
    environment:
      - MINIMAX_API_KEY=${MINIMAX_API_KEY}
    networks:
      - chatbot-net
```

- [ ] **Step 4: Build y verificar health**

```bash
docker compose build minimax-mcp
docker compose up -d minimax-mcp
docker exec chatbot-backend-1 curl http://minimax-mcp:8080/health
```

Expected: `{"api_key_set": false, "status": "ok"}` (sin API key todavía)

- [ ] **Step 5: Commit**

```bash
git add mcp_servers/minimax/ docker-compose.yml
GIT_EDITOR=true git commit -m "feat: add minimax-mcp Docker service with gunicorn 360s timeout"
```

---

### Task 3: Settings y .env

**Files:**
- Modify: `saas_chatbot/settings.py`

- [ ] **Step 1: Añadir minimax a `MCP_SERVERS` en `settings.py`**

Localizar la línea 107-109 en `saas_chatbot/settings.py`:

```python
MCP_SERVERS = {
    'brave_search': get_env('BRAVE_SEARCH_MCP_URL', default='http://brave-search-mcp:8080'),
}
```

Reemplazar con:

```python
MCP_SERVERS = {
    'brave_search': get_env('BRAVE_SEARCH_MCP_URL', default='http://brave-search-mcp:8080'),
    'minimax': get_env('MINIMAX_MCP_URL', default='http://minimax-mcp:8080'),
}
```

- [ ] **Step 2: Añadir `MINIMAX_API_KEY` al archivo `.env`**

```
MINIMAX_API_KEY=tu_api_key_aqui
```

Obtener la key en: https://platform.minimaxi.com/user-center/basic-information/interface-key

- [ ] **Step 3: Rebuild minimax-mcp y verificar que detecta la key**

```bash
docker compose up -d minimax-mcp
docker exec chatbot-backend-1 curl http://minimax-mcp:8080/health
```

Expected: `{"api_key_set": true, "status": "ok"}`

- [ ] **Step 4: Commit**

```bash
git add saas_chatbot/settings.py
GIT_EDITOR=true git commit -m "feat: add minimax MCP URL to MCP_SERVERS settings"
```

---

### Task 4: McpClient con timeout configurable

**Files:**
- Modify: `core/agent/infrastructure/mcp_client.py`

- [ ] **Step 1: Correr el test existente**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_minimax.py::TestMcpClientTimeout -q
```

Expected: FAIL — `call()` no acepta parámetro `timeout`.

- [ ] **Step 2: Actualizar `mcp_client.py`**

```python
import logging
import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class McpClient:
    def call(self, server: str, tool: str, params: dict, timeout: int = 20) -> dict:
        """Llama a un tool de un MCP server via HTTP POST. Retorna el JSON de respuesta."""
        servers = getattr(settings, 'MCP_SERVERS', {})
        base_url = servers.get(server)
        if not base_url:
            raise ValueError(f"MCP server '{server}' not configured in MCP_SERVERS.")
        url = f'{base_url}/call'
        resp = requests.post(url, json={'tool': tool, 'params': params}, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
```

- [ ] **Step 3: Correr tests**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_minimax.py::TestMcpClientTimeout -q
```

Expected: PASS (2 tests).

- [ ] **Step 4: Commit**

```bash
git add core/agent/infrastructure/mcp_client.py
GIT_EDITOR=true git commit -m "feat: add configurable timeout to McpClient.call()"
```

---

### Task 5: Reescribir GeneratePostImageTool

**Files:**
- Modify: `core/agent/infrastructure/tools/image_tools.py`

- [ ] **Step 1: Correr tests que ya fallaban**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_minimax.py::TestGeneratePostImageTool -q
```

Expected: FAIL — tool todavía usa Playwright.

- [ ] **Step 2: Reemplazar el contenido completo de `image_tools.py`**

```python
import base64
import logging
from datetime import date
from django.conf import settings
from core.agent.domain.tools import BaseTool, ToolResult
from core.agent.infrastructure.gemini_adapter import GeminiAdapter, FALLBACK_MESSAGE
from core.agent.infrastructure.mcp_client import McpClient
from core.agent.infrastructure.rag_utils import get_rag_context

logger = logging.getLogger(__name__)

PLATFORM_ASPECT = {
    'instagram': '1:1',
    'story': '9:16',
    'linkedin': '16:9',
}


class GeneratePostImageTool(BaseTool):
    name = 'generate_post_image'

    def __init__(self):
        self._gemini = GeminiAdapter()
        self._mcp = McpClient()

    def execute(self, topic: str, platform: str = 'instagram') -> ToolResult:
        api_key = settings.GEMINI_API_KEY
        if not api_key:
            return self._error('API key de Gemini no configurada.')
        model = getattr(settings, 'AI_MODEL', 'gemini-2.5-flash')
        platform = platform.lower().strip()
        if platform not in PLATFORM_ASPECT:
            platform = 'instagram'

        rag_context = get_rag_context(topic)
        rag_section = (
            f'\n\nContexto adicional (úsalo si es relevante):\n{rag_context}'
        ) if rag_context else ''

        # Gemini construye el prompt visual en inglés (Minimax trabaja mejor en inglés)
        gemini_prompt = (
            f'You are a visual marketing expert. Create a detailed image generation prompt '
            f'in English for a professional {platform} post about: "{topic}".{rag_section}\n'
            f'The image must look modern, business-appropriate, and visually impactful. '
            f'Include details: color palette, style (photorealistic/flat/3D), composition, '
            f'key visual elements. Reply ONLY with the prompt in English. Max 150 words.'
        )
        image_prompt = self._gemini.generate_response(
            prompt=gemini_prompt, api_key=api_key, model_name=model, thinking_budget=0
        )
        if image_prompt == FALLBACK_MESSAGE or not image_prompt.strip():
            return self._error('El servicio de IA no está disponible temporalmente.')

        try:
            mcp_result = self._mcp.call(
                'minimax',
                'generate_image',
                {'prompt': image_prompt, 'aspect_ratio': PLATFORM_ASPECT[platform]},
                timeout=90,
            )
            image_b64 = mcp_result.get('image_bytes_b64', '')
            if not image_b64:
                return self._error('No se recibió imagen de Minimax.')
            image_bytes = base64.b64decode(image_b64)
            filename = f'post_{platform}_{date.today().isoformat()}.jpg'
            return ToolResult(
                content=f'Imagen para {platform} generada con Minimax.',
                tool_name=self.name,
                success=True,
                metadata={'image_bytes': image_bytes, 'filename': filename, 'platform': platform},
            )
        except Exception as e:
            logger.error(f'Error en GeneratePostImageTool via Minimax: {e}', exc_info=True)
            return self._error(f'No pude generar la imagen: {e}')
```

- [ ] **Step 3: Correr tests de image**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_minimax.py::TestGeneratePostImageTool -q
```

Expected: PASS (3 tests).

- [ ] **Step 4: Correr suite completa y arreglar tests rotos**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/ -q 2>&1 | grep -E "FAIL|ERROR|passed"
```

Si hay tests en `test_tools.py` que usen `GeneratePostImageTool` con Playwright, actualizarlos:

```python
# Ejemplo de actualización en test_tools.py (si hay test de imagen)
def test_generate_post_image(self):
    from core.agent.infrastructure.tools.image_tools import GeneratePostImageTool
    from core.agent.infrastructure.mcp_client import McpClient
    from core.agent.infrastructure.gemini_adapter import GeminiAdapter
    fake_bytes = b'\x89PNG'
    with patch.object(GeminiAdapter, 'generate_response', return_value='a marketing image'), \
         patch.object(McpClient, 'call', return_value={
             'image_bytes_b64': base64.b64encode(fake_bytes).decode()
         }), \
         override_settings(GEMINI_API_KEY='key',
                           MCP_SERVERS={'minimax': 'http://minimax:8080'}):
        result = GeneratePostImageTool().execute(topic='test topic')
    assert result.success
    assert result.metadata['image_bytes'] == fake_bytes
```

- [ ] **Step 5: Verificar que el total de tests pasa**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/ -q
```

Expected: todos los tests pasan (237+ según estado Sprint 12).

- [ ] **Step 6: Commit**

```bash
git add core/agent/infrastructure/tools/image_tools.py core/agent/tests/
GIT_EDITOR=true git commit -m "feat: rewrite GeneratePostImageTool to use Minimax MCP instead of Playwright"
```

---

### Task 6: GenerateAudioTool

**Files:**
- Create: `core/agent/infrastructure/tools/media_tools.py`

- [ ] **Step 1: Correr tests existentes**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_minimax.py::TestGenerateAudioTool -q
```

Expected: FAIL — `media_tools` no existe.

- [ ] **Step 2: Crear `media_tools.py`**

```python
import base64
import logging
import django_rq
from datetime import date
from core.agent.domain.tools import BaseTool, ToolResult
from core.agent.infrastructure.mcp_client import McpClient

logger = logging.getLogger(__name__)


class GenerateAudioTool(BaseTool):
    name = 'generate_audio'

    def __init__(self):
        self._mcp = McpClient()

    def execute(self, text: str, voice_id: str = 'presenter_male') -> ToolResult:
        if len(text) > 2000:
            return self._error('El texto es demasiado largo (máximo 2000 caracteres).')
        try:
            result = self._mcp.call(
                'minimax',
                'text_to_audio',
                {'text': text, 'voice_id': voice_id},
                timeout=60,
            )
            audio_b64 = result.get('audio_bytes_b64', '')
            if not audio_b64:
                return self._error('No se recibió audio de Minimax.')
            audio_bytes = base64.b64decode(audio_b64)
            filename = f'audio_{date.today().isoformat()}.mp3'
            return ToolResult(
                content='Audio generado correctamente.',
                tool_name=self.name,
                success=True,
                metadata={'audio_bytes': audio_bytes, 'filename': filename},
            )
        except Exception as e:
            logger.error(f'Error en GenerateAudioTool: {e}', exc_info=True)
            return self._error(f'No pude generar el audio: {e}')


class GenerateVideoTool(BaseTool):
    name = 'generate_video'

    def execute(self, prompt: str, chat_id: int = None) -> ToolResult:
        try:
            queue = django_rq.get_queue('default')
            queue.enqueue(
                'core.agent.infrastructure.jobs.video_minimax_job',
                prompt=prompt,
                chat_id=chat_id,
                job_timeout=600,
            )
        except Exception as e:
            logger.error(f'Error encolando video job: {e}', exc_info=True)
            return self._error(f'No pude iniciar la generación del video: {e}')
        return ToolResult(
            content='⏳ Generando video... Te aviso cuando esté listo (puede tardar 3-5 minutos).',
            tool_name=self.name,
            success=True,
            metadata={'prompt': prompt},
        )
```

- [ ] **Step 3: Correr tests de audio y video**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_minimax.py::TestGenerateAudioTool core/agent/tests/test_sprint13_minimax.py::TestGenerateVideoTool -q
```

Expected: FAIL para `TestGenerateVideoTool` (el job `video_minimax_job` no existe aún). `TestGenerateAudioTool` debería PASAR.

- [ ] **Step 4: Commit parcial**

```bash
git add core/agent/infrastructure/tools/media_tools.py
GIT_EDITOR=true git commit -m "feat: add GenerateAudioTool and GenerateVideoTool (media_tools.py)"
```

---

### Task 7: video_minimax_job en jobs.py

**Files:**
- Modify: `core/agent/infrastructure/jobs.py`

- [ ] **Step 1: Correr test del video tool**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_minimax.py::TestGenerateVideoTool -q
```

Expected: FAIL (enqueue mock funciona, pero el job string apunta a función inexistente).

- [ ] **Step 2: Añadir `video_minimax_job` y `_send_telegram_video` al final de `jobs.py`**

```python
def video_minimax_job(prompt: str, chat_id: int) -> None:
    """Genera video via Minimax MCP y lo envía por Telegram cuando está listo."""
    from core.agent.infrastructure.mcp_client import McpClient
    try:
        client = McpClient()
        result = client.call('minimax', 'generate_video', {'prompt': prompt}, timeout=360)
        video_url = result.get('video_url')
        if not video_url:
            raise ValueError(f'No video_url en respuesta Minimax: {result}')
        video_bytes = requests.get(video_url, timeout=120).content
        _send_telegram_video(chat_id, video_bytes)
    except Exception as e:
        logger.error(f'Error en video_minimax_job: {e}', exc_info=True)
        _send_telegram(chat_id, f'❌ Error generando el video: {e}')


def _send_telegram_video(chat_id: int, video_bytes: bytes) -> None:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token or not chat_id:
        return
    try:
        requests.post(
            f'https://api.telegram.org/bot{token}/sendVideo',
            data={'chat_id': str(chat_id), 'caption': '🎬 Video generado'},
            files={'video': ('video.mp4', video_bytes, 'video/mp4')},
            timeout=120,
        )
    except Exception as e:
        logger.error(f'Error enviando video a Telegram {chat_id}: {e}')
```

- [ ] **Step 3: Correr todos los tests de sprint13**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_minimax.py -q
```

Expected: todos PASS excepto los handlers de Telegram (todavía no existen `cmd_video`/`cmd_audio`).

- [ ] **Step 4: Commit**

```bash
git add core/agent/infrastructure/jobs.py
GIT_EDITOR=true git commit -m "feat: add video_minimax_job for async Minimax video delivery via Telegram"
```

---

### Task 8: Registry + handlers Telegram /video y /audio

**Files:**
- Modify: `core/agent/infrastructure/tools/registry.py`
- Modify: `core/agent/management/commands/run_telegram_bot.py`

- [ ] **Step 1: Actualizar `registry.py`**

```python
from .content_tools import GeneratePostTool, WriteTextTool, GenerateShortScriptTool
from .report_tools import GenerateMonthlyReportTool
from .whisper_tool import TranscribeAudioTool
from .maps_tools import ProspectMapsTool
from .browser_tools import GetPostStatsTool
from .login_tool import BrowserLoginTool
from .search_tools import WebSearchTool
from .document_tools import GenerateDocumentTool
from .prospect_tools import ProspectResearchTool
from .image_tools import GeneratePostImageTool
from .media_tools import GenerateAudioTool, GenerateVideoTool
from .rag_tools import RAGUploadTool, RAGQueryTool

_registry = None


def get_registry() -> dict:
    global _registry
    if _registry is None:
        _registry = {
            'generate_post': GeneratePostTool(),
            'write_text': WriteTextTool(),
            'generate_short_script': GenerateShortScriptTool(),
            'generate_monthly_report': GenerateMonthlyReportTool(),
            'transcribe_audio': TranscribeAudioTool(),
            'prospect_maps': ProspectMapsTool(),
            'get_post_stats': GetPostStatsTool(),
            'browser_login': BrowserLoginTool(),
            'web_search': WebSearchTool(),
            'generate_document': GenerateDocumentTool(),
            'prospect_research': ProspectResearchTool(),
            'generate_post_image': GeneratePostImageTool(),
            'generate_audio': GenerateAudioTool(),
            'generate_video': GenerateVideoTool(),
            'rag_upload': RAGUploadTool(),
            'rag_query': RAGQueryTool(),
        }
    return _registry


def get_tool(name: str):
    return get_registry().get(name)
```

- [ ] **Step 2: Añadir `cmd_video` y `cmd_audio` en `run_telegram_bot.py`**

Añadir después de `cmd_imagen` (después de la línea 801):

```python
async def cmd_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/video <descripción>"""
    prompt = ' '.join(context.args).strip() if context.args else ''
    if not prompt:
        await update.message.reply_text(
            '🎬 Uso: `/video <descripción del video>`\n'
            'Ejemplo: `/video Los beneficios de tener presencia en redes sociales`\n\n'
            '⏱ Tiempo estimado: 3-5 minutos.',
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
    result = await run_tool(
        'generate_video', session.id,
        prompt=prompt, chat_id=update.effective_chat.id,
    )
    if result:
        await safe_reply(update.message, result.content)
    else:
        await update.message.reply_text('❌ Herramienta no disponible.')


async def cmd_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/audio <texto a narrar>"""
    text = ' '.join(context.args).strip() if context.args else ''
    if not text:
        await update.message.reply_text(
            '🎙 Uso: `/audio <texto a narrar>`\n'
            'Ejemplo: `/audio Bienvenidos a Tu Web MX, especialistas en diseño web`\n\n'
            'Máximo 2000 caracteres.',
            parse_mode='Markdown',
        )
        return
    if len(text) > 2000:
        await update.message.reply_text('❌ Texto demasiado largo (máximo 2000 caracteres).')
        return
    user = update.effective_user
    session = await get_or_create_session(
        update.effective_chat.id, user.username or '', user.full_name or ''
    )
    if not session.is_authorized:
        await update.message.reply_text('No estás autorizado para usar este agente.')
        return
    await update.message.reply_text('🎙 Generando audio...')
    result = await run_tool('generate_audio', session.id, text=text)
    if result and result.success:
        audio_bytes = result.metadata.get('audio_bytes')
        filename = result.metadata.get('filename', 'audio.mp3')
        await update.message.reply_document(
            document=io.BytesIO(audio_bytes),
            filename=filename,
            caption='🎙 Audio generado',
        )
    elif result:
        await update.message.reply_text(f'❌ {result.content}')
    else:
        await update.message.reply_text('❌ Herramienta no disponible.')
```

- [ ] **Step 3: Registrar los handlers**

Localizar la línea `app.add_handler(CommandHandler('imagen', cmd_imagen))` y añadir justo debajo:

```python
        app.add_handler(CommandHandler('video', cmd_video))
        app.add_handler(CommandHandler('audio', cmd_audio))
```

- [ ] **Step 4: Actualizar texto de `/ayuda`**

Localizar el bloque del mensaje de ayuda (cerca de la línea 192) y añadir después de la sección de `/imagen`:

```python
"🎬 */video <descripción>*\n"
"Genera un video corto con IA (3-5 min de espera).\n"
"Ejemplo: `/video Beneficios del diseño web profesional`\n\n"
"🎙 */audio <texto>*\n"
"Convierte texto a narración de voz en MP3.\n"
"Ejemplo: `/audio Bienvenidos a Tu Web MX`\n\n"
```

- [ ] **Step 5: Correr todos los tests de sprint13**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_minimax.py -q
```

Expected: todos PASS.

- [ ] **Step 6: Correr suite completa**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/ -q
```

Expected: 240+ tests PASS.

- [ ] **Step 7: Commit**

```bash
git add core/agent/infrastructure/tools/registry.py core/agent/management/commands/run_telegram_bot.py
GIT_EDITOR=true git commit -m "feat: add /video and /audio Telegram commands + registry update"
```

---

### Task 9: Prueba end-to-end

- [ ] **Step 1: Rebuild y restart completo**

```bash
docker compose build minimax-mcp
docker compose up -d
docker compose restart backend rqworker telegram_bot
```

- [ ] **Step 2: Verificar minimax-mcp sano**

```bash
docker exec chatbot-backend-1 curl http://minimax-mcp:8080/health
```

Expected: `{"api_key_set": true, "status": "ok"}`

- [ ] **Step 3: Probar `/imagen` en Telegram**

Enviar: `/imagen instagram Marketing digital para restaurantes`

Expected: bot responde con imagen generada por Minimax (ya no usa Playwright).

- [ ] **Step 4: Probar `/audio` en Telegram**

Enviar: `/audio Bienvenidos a Tu Web MX, somos especialistas en presencia digital`

Expected: bot responde con archivo MP3 en <30 segundos.

- [ ] **Step 5: Probar `/video` en Telegram**

Enviar: `/video Los beneficios de tener una página web profesional para tu negocio`

Expected:
1. Bot responde inmediatamente: "⏳ Generando video..."
2. En 3-5 minutos llega el archivo MP4

- [ ] **Step 6: Commit final**

```bash
GIT_EDITOR=true git commit -m "sprint 13a completo: /imagen via Minimax, /video y /audio nuevos"
```

---

## Self-Review

**Cobertura del spec:**
- ✅ Levantar contenedor Docker (`minimax-mcp`) — Task 2
- ✅ Mejorar `/imagen` — Minimax API en vez de HTML+Playwright — Task 5
- ✅ Nuevo `/video` — async RQ job, ~5 min — Tasks 7, 8
- ✅ Nuevo `/audio` — TTS síncrono, <30 s — Tasks 6, 8
- ✅ `/post` y `/short` ya funcionan con Gemini, no requieren cambios

**Placeholder scan:** ninguno. Cada step tiene código completo.

**Type consistency:**
- `GeneratePostImageTool.execute()` → `metadata['image_bytes']` (bytes) ← leído en `cmd_imagen` línea 792.
- `GenerateAudioTool.execute()` → `metadata['audio_bytes']` (bytes) ← leído en `cmd_audio`.
- `GenerateVideoTool.execute()` → `result.content` (string con "⏳") ← pasado a `safe_reply` en `cmd_video`.
- `McpClient.call(..., timeout=90)` para imagen, `timeout=360` para video — consistente con gunicorn `--timeout 360`.
