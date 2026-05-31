# Sprint 13b — Media sin Minimax: Pollinations + Edge-TTS + Pexels/MoviePy

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar Minimax (sin free tier) con herramientas gratuitas: Pollinations.ai para `/imagen`, Edge-TTS para `/audio`, y Pexels+MoviePy para `/video`; eliminando todo rastro de Minimax del proyecto.

**Architecture:** Llamadas directas desde los Django tools a cada API — sin Flask MCP proxy intermedio. `image_tools.py` llama a Pollinations.ai vía `requests.get`. `media_tools.py` llama a Edge-TTS vía asyncio y a Pexels+MoviePy vía RQ job. Las interfaces de los handlers de Telegram no cambian (`metadata['image_bytes']`, `metadata['audio_bytes']`).

**Tech Stack:** Pollinations.ai (free, no API key, modelo FLUX), edge-tts 7.x (Microsoft TTS, voz `es-MX-DaliaNeural`), Pexels Video API (free tier 200 req/h), MoviePy 1.0.3 (ffmpeg ya instalado en Docker), Django RQ.

---

## Mapa de archivos

| Archivo | Acción | Qué cambia |
|---|---|---|
| `mcp_servers/minimax/` | **Eliminar** directorio completo | — |
| `docker-compose.yml` | Modificar | Quitar servicio `minimax-mcp` |
| `saas_chatbot/settings.py` | Modificar | Quitar `minimax` de `MCP_SERVERS`, añadir `PEXELS_API_KEY` |
| `.env` | Modificar | Añadir `PEXELS_API_KEY=` (obtener en pexels.com/api) |
| `requirements.txt` | Modificar | Añadir `edge-tts==7.0.2` y `moviepy==1.0.3` |
| `core/agent/infrastructure/tools/image_tools.py` | Reescribir | Pollinations.ai directo en vez de McpClient |
| `core/agent/infrastructure/tools/media_tools.py` | Reescribir | Edge-TTS directo + job renombrado |
| `core/agent/infrastructure/jobs.py` | Modificar | Renombrar `video_minimax_job` → `video_pexels_job`, reescribir lógica |
| `core/agent/tests/test_sprint13_minimax.py` | Modificar | Actualizar mocks para las nuevas APIs |

---

### Task 1: Limpiar Minimax del proyecto

**Files:**
- Delete: `mcp_servers/minimax/` (directorio completo)
- Modify: `docker-compose.yml`
- Modify: `saas_chatbot/settings.py`

- [ ] **Step 1: Eliminar el directorio minimax**

```bash
rm -rf /home/anuarbarrera/miagent/chatbot/mcp_servers/minimax
```

Verificar que quedó limpio:
```bash
ls /home/anuarbarrera/miagent/chatbot/mcp_servers/
```
Expected: solo `brave_search/`

- [ ] **Step 2: Quitar `minimax-mcp` de `docker-compose.yml`**

Leer `docker-compose.yml`. Eliminar el bloque completo:
```yaml
  minimax-mcp:
    build:
      context: ./mcp_servers/minimax
    environment:
      - MINIMAX_API_KEY=${MINIMAX_API_KEY}
    networks:
      - chatbot-net
```

Verificar que el YAML sigue siendo válido:
```bash
docker compose -f /home/anuarbarrera/miagent/chatbot/docker-compose.yml config --quiet && echo "YAML valid"
```

- [ ] **Step 3: Limpiar `settings.py`**

Leer `saas_chatbot/settings.py`. Localizar el bloque `MCP_SERVERS` y reemplazarlo:

```python
MCP_SERVERS = {
    'brave_search': get_env('BRAVE_SEARCH_MCP_URL', default='http://brave-search-mcp:8080'),
}

PEXELS_API_KEY = get_env('PEXELS_API_KEY', default='')
```

También verificar que no haya otras referencias a `MINIMAX` en el archivo:
```bash
grep -n "minimax\|MINIMAX" /home/anuarbarrera/miagent/chatbot/saas_chatbot/settings.py
```
Expected: sin resultados.

- [ ] **Step 4: Añadir `PEXELS_API_KEY` al `.env`**

Añadir al final del archivo `.env`:
```
PEXELS_API_KEY=
```

Obtener la key gratuita en https://www.pexels.com/api/ (registrarse, es inmediato).

- [ ] **Step 5: Commit**

```bash
cd /home/anuarbarrera/miagent/chatbot
git add -A
GIT_EDITOR=true git commit -m "chore: remove minimax MCP server and all references"
```

---

### Task 2: Añadir dependencias Python + rebuild

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Añadir dependencias a `requirements.txt`**

Añadir al final del archivo:
```
edge-tts==7.0.2
moviepy==1.0.3
```

Nota: `ffmpeg` ya está instalado en `Dockerfile` y `Dockerfile.worker` (línea 13: `apt-get install -y postgresql-client ffmpeg`). No necesita cambio.

- [ ] **Step 2: Rebuild e instalar las nuevas dependencias en el contenedor activo**

```bash
docker exec chatbot-backend-1 pip install edge-tts==7.0.2 moviepy==1.0.3
```

(El rebuild completo con `docker compose build` puede hacerse al final. Esto instala las libs en el contenedor en ejecución para poder correr los tests ahora.)

También en el worker:
```bash
docker exec chatbot-rqworker-1 pip install edge-tts==7.0.2 moviepy==1.0.3
```

- [ ] **Step 3: Verificar que importan correctamente**

```bash
docker exec chatbot-backend-1 python -c "import edge_tts; import moviepy; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
GIT_EDITOR=true git commit -m "chore: add edge-tts and moviepy dependencies"
```

---

### Task 3: /imagen → Pollinations.ai

**Files:**
- Modify: `core/agent/infrastructure/tools/image_tools.py`
- Modify: `core/agent/tests/test_sprint13_minimax.py`

- [ ] **Step 1: Actualizar los tests primero**

En `core/agent/tests/test_sprint13_minimax.py`, reemplazar la clase `TestGeneratePostImageTool` completa con:

```python
class TestGeneratePostImageTool:
    def test_calls_pollinations_and_returns_image_bytes(self):
        from core.agent.infrastructure.tools.image_tools import GeneratePostImageTool
        from core.agent.infrastructure.gemini_adapter import GeminiAdapter
        import requests as req_module
        fake_bytes = b'\x89PNG\r\n\x1a\nFAKE'
        mock_resp = MagicMock()
        mock_resp.content = fake_bytes
        mock_resp.raise_for_status = MagicMock()
        with patch.object(GeminiAdapter, 'generate_response', return_value='a marketing photo'), \
             patch('core.agent.infrastructure.tools.image_tools.requests.get',
                   return_value=mock_resp), \
             override_settings(GEMINI_API_KEY='k'):
            result = GeneratePostImageTool().execute(topic='diseño web', platform='instagram')
        assert result.success
        assert result.metadata['image_bytes'] == fake_bytes
        assert result.metadata['filename'].endswith('.jpg')

    def test_returns_error_when_pollinations_fails(self):
        from core.agent.infrastructure.tools.image_tools import GeneratePostImageTool
        from core.agent.infrastructure.gemini_adapter import GeminiAdapter
        with patch.object(GeminiAdapter, 'generate_response', return_value='a photo'), \
             patch('core.agent.infrastructure.tools.image_tools.requests.get',
                   side_effect=Exception('connection refused')), \
             override_settings(GEMINI_API_KEY='k'):
            result = GeneratePostImageTool().execute(topic='test')
        assert not result.success
        assert 'No pude generar' in result.content

    def test_story_platform_uses_576x1024(self):
        from core.agent.infrastructure.tools.image_tools import GeneratePostImageTool
        from core.agent.infrastructure.gemini_adapter import GeminiAdapter
        mock_resp = MagicMock()
        mock_resp.content = b'JPGDATA'
        mock_resp.raise_for_status = MagicMock()
        with patch.object(GeminiAdapter, 'generate_response', return_value='a photo'), \
             patch('core.agent.infrastructure.tools.image_tools.requests.get',
                   return_value=mock_resp) as mock_get, \
             override_settings(GEMINI_API_KEY='k'):
            GeneratePostImageTool().execute(topic='test', platform='story')
        url = mock_get.call_args[0][0]
        assert 'width=576' in url
        assert 'height=1024' in url
```

- [ ] **Step 2: Correr para verificar que fallan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_minimax.py::TestGeneratePostImageTool -q
```
Expected: FAIL (tool todavía usa McpClient).

- [ ] **Step 3: Reescribir `image_tools.py`**

Reemplazar el contenido completo del archivo:

```python
import logging
import requests
from datetime import date
from urllib.parse import quote
from django.conf import settings
from core.agent.domain.tools import BaseTool, ToolResult
from core.agent.infrastructure.gemini_adapter import GeminiAdapter, FALLBACK_MESSAGE
from core.agent.infrastructure.rag_utils import get_rag_context

logger = logging.getLogger(__name__)

PLATFORM_SIZE = {
    'instagram': (1024, 1024),
    'story': (576, 1024),
    'linkedin': (1024, 576),
}


class GeneratePostImageTool(BaseTool):
    name = 'generate_post_image'

    def __init__(self):
        self._gemini = GeminiAdapter()

    def execute(self, topic: str, platform: str = 'instagram') -> ToolResult:
        api_key = settings.GEMINI_API_KEY
        if not api_key:
            return self._error('API key de Gemini no configurada.')
        model = getattr(settings, 'AI_MODEL', 'gemini-2.5-flash')
        platform = platform.lower().strip()
        if platform not in PLATFORM_SIZE:
            platform = 'instagram'

        rag_context = get_rag_context(topic)
        rag_section = (
            f'\n\nContexto adicional (úsalo si es relevante):\n{rag_context}'
        ) if rag_context else ''

        gemini_prompt = (
            f'You are a visual marketing expert. Create a detailed image generation prompt '
            f'in English for a professional {platform} post about: "{topic}".{rag_section}\n'
            f'The image must look modern, business-appropriate, and visually impactful. '
            f'Include: color palette, style (photorealistic/flat/3D), composition, key visual elements. '
            f'Reply ONLY with the image prompt in English. Max 150 words.'
        )
        image_prompt = self._gemini.generate_response(
            prompt=gemini_prompt, api_key=api_key, model_name=model, thinking_budget=0
        )
        if image_prompt == FALLBACK_MESSAGE or not image_prompt.strip():
            return self._error('El servicio de IA no está disponible temporalmente.')

        w, h = PLATFORM_SIZE[platform]
        url = (
            f'https://image.pollinations.ai/prompt/{quote(image_prompt)}'
            f'?model=flux&width={w}&height={h}&nologo=true&enhance=true'
        )
        try:
            resp = requests.get(url, timeout=90)
            resp.raise_for_status()
            image_bytes = resp.content
            if not image_bytes:
                return self._error('No se recibió imagen de Pollinations.')
            filename = f'post_{platform}_{date.today().isoformat()}.jpg'
            return ToolResult(
                content=f'Imagen para {platform} generada.',
                tool_name=self.name,
                success=True,
                metadata={'image_bytes': image_bytes, 'filename': filename, 'platform': platform},
            )
        except Exception as e:
            logger.error(f'Error en GeneratePostImageTool (Pollinations): {e}', exc_info=True)
            return self._error(f'No pude generar la imagen: {e}')
```

- [ ] **Step 4: Correr tests**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_minimax.py::TestGeneratePostImageTool -q
```
Expected: 3 tests PASS.

- [ ] **Step 5: Verificar suite completa sin regressions**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/ -q 2>&1 | tail -4
```

- [ ] **Step 6: Commit**

```bash
git add core/agent/infrastructure/tools/image_tools.py core/agent/tests/test_sprint13_minimax.py
GIT_EDITOR=true git commit -m "feat: replace Minimax image with Pollinations.ai (free, FLUX model)"
```

---

### Task 4: /audio → Edge-TTS

**Files:**
- Modify: `core/agent/infrastructure/tools/media_tools.py`
- Modify: `core/agent/tests/test_sprint13_minimax.py`

- [ ] **Step 1: Actualizar los tests de audio**

En `test_sprint13_minimax.py`, reemplazar la clase `TestGenerateAudioTool` completa con:

```python
class TestGenerateAudioTool:
    def test_returns_audio_bytes(self):
        from core.agent.infrastructure.tools.media_tools import GenerateAudioTool
        fake_audio = b'ID3\x03\x00FAKEMP3'
        mock_communicate = MagicMock()
        mock_communicate.save = AsyncMock()
        with patch('edge_tts.Communicate', return_value=mock_communicate), \
             patch('core.agent.infrastructure.tools.media_tools.open',
                   MagicMock(return_value=MagicMock(
                       __enter__=MagicMock(return_value=MagicMock(read=MagicMock(return_value=fake_audio))),
                       __exit__=MagicMock(return_value=False)))), \
             patch('core.agent.infrastructure.tools.media_tools.os.unlink'), \
             patch('core.agent.infrastructure.tools.media_tools.asyncio.run',
                   return_value=fake_audio):
            result = GenerateAudioTool().execute(text='Bienvenidos a Tu Web MX')
        assert result.success
        assert result.metadata['audio_bytes'] == fake_audio
        assert result.metadata['filename'].endswith('.mp3')

    def test_rejects_text_over_2000_chars(self):
        from core.agent.infrastructure.tools.media_tools import GenerateAudioTool
        result = GenerateAudioTool().execute(text='a' * 2001)
        assert not result.success
        assert '2000' in result.content
```

- [ ] **Step 2: Correr para verificar que fallan**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_minimax.py::TestGenerateAudioTool -q
```
Expected: FAIL (tool todavía usa McpClient).

- [ ] **Step 3: Reescribir `GenerateAudioTool` en `media_tools.py`**

Reemplazar solo la clase `GenerateAudioTool` (mantener `GenerateVideoTool` sin cambios por ahora):

```python
import asyncio
import logging
import os
import tempfile
import django_rq
from datetime import date
from core.agent.domain.tools import BaseTool, ToolResult

logger = logging.getLogger(__name__)


def _tts(text: str, voice: str = 'es-MX-DaliaNeural') -> bytes:
    """Genera audio MP3 con Edge-TTS. Se ejecuta de forma síncrona envolviendo la coroutine."""
    import edge_tts

    async def _run():
        tmp = tempfile.mktemp(suffix='.mp3')
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(tmp)
        with open(tmp, 'rb') as f:
            data = f.read()
        os.unlink(tmp)
        return data

    return asyncio.run(_run())


class GenerateAudioTool(BaseTool):
    name = 'generate_audio'

    def execute(self, text: str, voice: str = 'es-MX-DaliaNeural') -> ToolResult:
        if len(text) > 2000:
            return self._error('El texto es demasiado largo (máximo 2000 caracteres).')
        try:
            audio_bytes = _tts(text, voice)
            filename = f'audio_{date.today().isoformat()}.mp3'
            return ToolResult(
                content='Audio generado correctamente.',
                tool_name=self.name,
                success=True,
                metadata={'audio_bytes': audio_bytes, 'filename': filename},
            )
        except Exception as e:
            logger.error(f'Error en GenerateAudioTool (Edge-TTS): {e}', exc_info=True)
            return self._error(f'No pude generar el audio: {e}')


class GenerateVideoTool(BaseTool):
    name = 'generate_video'

    def execute(self, prompt: str, chat_id: int = None) -> ToolResult:
        try:
            queue = django_rq.get_queue('default')
            queue.enqueue(
                'core.agent.infrastructure.jobs.video_pexels_job',
                prompt=prompt,
                chat_id=chat_id,
                job_timeout=600,
            )
        except Exception as e:
            logger.error(f'Error encolando video job: {e}', exc_info=True)
            return self._error(f'No pude iniciar la generación del video: {e}')
        return ToolResult(
            content='⏳ Generando video... Te aviso cuando esté listo (puede tardar 2-4 minutos).',
            tool_name=self.name,
            success=True,
            metadata={'prompt': prompt},
        )
```

Nota: el parámetro `voice_id` de la versión anterior se renombra a `voice` para claridad. El handler de Telegram no pasa ese parámetro, así que no hay cambio de interfaz.

- [ ] **Step 4: Actualizar el test de video para el nuevo nombre del job**

En `TestGenerateVideoTool`, el string del job cambió de `video_minimax_job` a `video_pexels_job`. Actualizar:

```python
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
        assert call_args[0] == 'core.agent.infrastructure.jobs.video_pexels_job'
        assert '⏳' in result.content

    def test_returns_error_when_rq_fails(self):
        from core.agent.infrastructure.tools.media_tools import GenerateVideoTool
        with patch('core.agent.infrastructure.tools.media_tools.django_rq') as mock_rq:
            mock_rq.get_queue.side_effect = Exception('Redis unavailable')
            result = GenerateVideoTool().execute(prompt='test', chat_id=123)
        assert not result.success
```

- [ ] **Step 5: Correr tests de audio y video**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_minimax.py::TestGenerateAudioTool core/agent/tests/test_sprint13_minimax.py::TestGenerateVideoTool -q
```

Si `test_returns_audio_bytes` falla por la complejidad del mock de `asyncio.run`, usar este enfoque más simple que mockea directamente la función `_tts`:

```python
def test_returns_audio_bytes(self):
    from core.agent.infrastructure.tools.media_tools import GenerateAudioTool
    import core.agent.infrastructure.tools.media_tools as media_module
    fake_audio = b'ID3\x03\x00FAKEMP3'
    with patch.object(media_module, '_tts', return_value=fake_audio):
        result = GenerateAudioTool().execute(text='Bienvenidos a Tu Web MX')
    assert result.success
    assert result.metadata['audio_bytes'] == fake_audio
    assert result.metadata['filename'].endswith('.mp3')
```

Expected: 2 + 2 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add core/agent/infrastructure/tools/media_tools.py core/agent/tests/test_sprint13_minimax.py
GIT_EDITOR=true git commit -m "feat: replace Minimax TTS with Edge-TTS (es-MX-DaliaNeural, free)"
```

---

### Task 5: /video → Pexels + MoviePy

**Files:**
- Modify: `core/agent/infrastructure/jobs.py`

- [ ] **Step 1: Añadir función auxiliar `_search_pexels_clip` y `_assemble_video` en `jobs.py`**

Añadir al final del archivo `jobs.py` (después de `_send_telegram_video`):

```python
def _search_pexels_clip(keyword: str, api_key: str) -> str | None:
    """Busca un clip en Pexels y lo descarga a un archivo temporal. Retorna la ruta local o None."""
    try:
        resp = requests.get(
            'https://api.pexels.com/videos/search',
            headers={'Authorization': api_key},
            params={'query': keyword, 'per_page': 3, 'size': 'medium', 'orientation': 'landscape'},
            timeout=15,
        )
        resp.raise_for_status()
        videos = resp.json().get('videos', [])
        if not videos:
            logger.warning(f'Pexels: sin resultados para "{keyword}"')
            return None
        # Tomar el primer video con archivo SD o HD en MP4
        for video in videos:
            for vf in sorted(video.get('video_files', []), key=lambda x: x.get('width', 0)):
                if vf.get('file_type') == 'video/mp4' and vf.get('quality') in ('sd', 'hd'):
                    link = vf['link']
                    break
            else:
                continue
            r = requests.get(link, timeout=60)
            r.raise_for_status()
            import tempfile as _tmp
            f = _tmp.NamedTemporaryFile(suffix='.mp4', delete=False)
            f.write(r.content)
            f.close()
            return f.name
    except Exception as e:
        logger.warning(f'Error descargando clip Pexels "{keyword}": {e}')
    return None


def video_pexels_job(prompt: str, chat_id: int) -> None:
    """Genera video: Gemini crea guión, Edge-TTS narra, Pexels aporta clips, MoviePy ensambla."""
    import json as _json
    import re as _re
    import os as _os
    import asyncio as _asyncio
    import tempfile as _tmp
    from core.agent.infrastructure.gemini_adapter import GeminiAdapter
    from core.agent.infrastructure.tools.media_tools import _tts

    api_key = settings.GEMINI_API_KEY
    pexels_key = getattr(settings, 'PEXELS_API_KEY', '')
    model = getattr(settings, 'AI_MODEL', 'gemini-2.5-flash')

    if not pexels_key:
        _send_telegram(chat_id, '❌ PEXELS_API_KEY no configurada en .env')
        return

    temp_files = []
    try:
        # 1. Gemini genera narración + keywords de escenas
        gemini = GeminiAdapter()
        script_prompt = (
            f'Eres un creador de contenido para redes sociales. '
            f'Crea el script para un video corto de 45 segundos sobre: "{prompt}"\n\n'
            f'Responde ÚNICAMENTE con este JSON sin markdown:\n'
            f'{{"narration": "texto completo de la narración en español (máx 120 palabras)", '
            f'"scenes": ["keyword en inglés escena 1", "keyword en inglés escena 2", "keyword en inglés escena 3"]}}\n'
            f'Las keywords deben describir footage de stock (ej: "professional web design laptop", "business meeting office").'
        )
        raw = gemini.generate_response(
            prompt=script_prompt, api_key=api_key, model_name=model, thinking_budget=0
        )
        json_str = _re.sub(r'^```json\n?|^```\n?|```$', '', raw.strip(), flags=_re.MULTILINE).strip()
        script = _json.loads(json_str)
        narration = script['narration']
        scenes = script['scenes'][:3]

        # 2. Edge-TTS genera narración
        audio_bytes = _tts(narration)
        audio_tmp = _tmp.NamedTemporaryFile(suffix='.mp3', delete=False)
        audio_tmp.write(audio_bytes)
        audio_tmp.close()
        temp_files.append(audio_tmp.name)

        # 3. Pexels: descargar clips
        clip_paths = []
        for keyword in scenes:
            path = _search_pexels_clip(keyword, pexels_key)
            if path:
                clip_paths.append(path)
                temp_files.append(path)

        if not clip_paths:
            _send_telegram(chat_id, '❌ No se encontraron clips de video en Pexels para este tema. Intenta con una descripción más general.')
            return

        # 4. MoviePy: ensamblar clips + audio
        from moviepy.editor import VideoFileClip, concatenate_videoclips, AudioFileClip

        audio_clip = AudioFileClip(audio_tmp.name)
        total_dur = audio_clip.duration
        clip_dur = total_dur / len(clip_paths)

        processed = []
        for path in clip_paths:
            raw_clip = VideoFileClip(path)
            cut = min(clip_dur, raw_clip.duration)
            c = raw_clip.subclip(0, cut).resize(width=1080)
            processed.append(c)

        final = concatenate_videoclips(processed, method='compose').set_audio(audio_clip)

        output_tmp = _tmp.NamedTemporaryFile(suffix='.mp4', delete=False)
        output_path = output_tmp.name
        output_tmp.close()
        temp_files.append(output_path)

        final.write_videofile(
            output_path, fps=24, codec='libx264', audio_codec='aac',
            logger=None, threads=2,
        )

        # 5. Enviar a Telegram
        with open(output_path, 'rb') as f:
            _send_telegram_video(chat_id, f.read())

    except Exception as e:
        logger.error(f'Error en video_pexels_job: {e}', exc_info=True)
        _send_telegram(chat_id, f'❌ Error generando el video: {e}')
    finally:
        for path in temp_files:
            try:
                _os.unlink(path)
            except Exception:
                pass
```

- [ ] **Step 2: Eliminar `video_minimax_job` y `_send_telegram_video` obsoletos si aún existen**

Verificar si todavía existe `video_minimax_job` en el archivo:
```bash
grep -n "video_minimax_job" /home/anuarbarrera/miagent/chatbot/core/agent/infrastructure/jobs.py
```

Si existe, eliminar esa función (mantener `_send_telegram_video` — la reutiliza `video_pexels_job`).

- [ ] **Step 3: Correr suite de tests**

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/test_sprint13_minimax.py -q
```
Expected: todos los tests del archivo PASS.

```bash
docker exec chatbot-backend-1 python -m pytest core/agent/tests/ -q 2>&1 | tail -4
```
Expected: igual o más tests pasando que antes.

- [ ] **Step 4: Commit**

```bash
git add core/agent/infrastructure/jobs.py
GIT_EDITOR=true git commit -m "feat: replace Minimax video with Pexels stock + MoviePy assembly + Edge-TTS narration"
```

---

### Task 6: Rebuild Docker + prueba end-to-end

- [ ] **Step 1: Rebuild completo de las imágenes**

```bash
cd /home/anuarbarrera/miagent/chatbot
docker compose build backend rqworker telegram_bot
docker compose up -d
docker compose restart backend rqworker telegram_bot
```

- [ ] **Step 2: Verificar que minimax-mcp ya no existe**

```bash
docker compose ps | grep minimax
```
Expected: sin output.

- [ ] **Step 3: Verificar health de brave-search-mcp (sigue funcionando)**

```bash
docker exec chatbot-backend-1 curl http://brave-search-mcp:8080/health
```
Expected: `{"status": "ok"}`

- [ ] **Step 4: Probar `/imagen` en Telegram**

Enviar: `/imagen instagram Diseño web profesional para restaurantes`

Expected: imagen generada por Pollinations.ai en ~20-40 segundos (FLUX model, calidad alta).

- [ ] **Step 5: Probar `/audio` en Telegram**

Enviar: `/audio Bienvenidos a Tu Web MX, somos especialistas en presencia digital para tu negocio`

Expected: archivo MP3 con voz femenina mexicana (DaliaNeural) en ~10 segundos.

- [ ] **Step 6: Probar `/video` en Telegram**

Enviar: `/video Los beneficios de tener una página web profesional para tu negocio`

Expected:
1. Respuesta inmediata: "⏳ Generando video..."
2. En 2-4 minutos: video MP4 de ~45 segundos con clips de stock + narración en español

- [ ] **Step 7: Commit de cierre si hubo ajustes**

```bash
GIT_EDITOR=true git commit -m "sprint 13b completo: Pollinations+EdgeTTS+Pexels/MoviePy, sin Minimax"
```

---

## Self-Review

**Cobertura del spec:**
- ✅ `/imagen` → Pollinations.ai (free, FLUX, sin API key) — Task 3
- ✅ `/audio` → Edge-TTS (free, voz DaliaNeural español MX) — Task 4
- ✅ `/video` → Pexels stock + MoviePy + Edge-TTS narración — Task 5
- ✅ Eliminar todo rastro de Minimax (directorio, docker-compose, settings) — Task 1
- ✅ Dependencias instaladas (edge-tts, moviepy) — Task 2
- ✅ Tests actualizados (mocks de nuevas APIs) — Tasks 3, 4

**Placeholder scan:** ninguno. Todo el código es completo.

**Type consistency:**
- `GeneratePostImageTool.execute()` → `metadata['image_bytes']` (bytes) ← sin cambio de interfaz para `cmd_imagen`
- `GenerateAudioTool.execute()` → `metadata['audio_bytes']` (bytes) ← sin cambio de interfaz para `cmd_audio`
- `GenerateVideoTool.execute()` encola `'core.agent.infrastructure.jobs.video_pexels_job'` ← consistente con nueva función en jobs.py
- `_tts(text, voice)` en `media_tools.py` ← importada en `video_pexels_job` como `from core.agent.infrastructure.tools.media_tools import _tts`
- `_send_telegram_video(chat_id, video_bytes)` ← reutilizada en `video_pexels_job`, ya existe en jobs.py
