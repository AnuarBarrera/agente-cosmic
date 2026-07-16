# Reels: Portada/Contraportada con HyperFrames (Parte B) — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar portada (3s) y contraportada (3s) generadas con HyperFrames a los reels, devolviendo el total a 24s (resolviendo el corte de audio de la Parte A) y horneando el hook/CTA en esos segmentos con mejor tipografía/movimiento.

**Architecture:** Un proyecto HyperFrames checked-in (`core/content_pipeline/hyperframes_reel/`) con 2 composiciones standalone (`portada.html`, `contraportada.html`), renderizadas vía el CLI de Node.js (instalado en `Dockerfile.worker`) con variables dinámicas (`--variables-file`). `reel_generator.py` orquesta: genera Veo+shots (sin cambios de la Parte A) → intenta portada+contraportada (1 reintento c/u) → si ambas OK, las normaliza a la resolución real y las antepone/agrega a los clips, indicándole a `_assemble_reel` que omita el hook/CTA sobre el cuerpo; si cualquiera falla, el reel cae a la estructura de la Parte A sin cambios.

**Tech Stack:** HyperFrames CLI (Node.js 22+, GSAP), ffmpeg, Vertex AI, Django, pytest.

## Global Constraints

- Duración: portada 3.0s, contraportada 3.0s. Cuerpo (Veo+shots) sin cambios de la Parte A (18s). Total con marca: 24s.
- Herramienta: HyperFrames real, GSAP vendorizado vía npm (sin CDN, sin red en render). `hyperframes` y `gsap` con versiones EXACTAS en `package.json` (sin `^`): `"hyperframes": "0.7.59"`, `"gsap": "3.14.2"`.
- Sin cacheo por marca — se regenera en cada reel.
- Fallback: si portada O contraportada falla tras 1 reintento cada una, se descartan AMBAS — el reel usa la estructura de la Parte A (18s, hook/CTA vía el motor activo) sin excepciones nuevas.
- Narración/música deben sonar desde t=0 del total (ya es el comportamiento actual de `_assemble_reel`, no requiere cambios — solo que `duration` ahora sea 24s en vez de 18s cuando la marca tiene éxito).
- No se cambia `_probe_video_width`, `_generate_video_clips`, `_build_hook_filter_parts`, `_build_cta_filter_parts`, `_render_text_overlay_playwright` — se reutilizan tal cual.

---

## Task 1: Proyecto HyperFrames (Docker + composiciones)

**Files:**
- Create: `core/content_pipeline/hyperframes_reel/package.json`
- Create: `core/content_pipeline/hyperframes_reel/compositions/portada.html`
- Create: `core/content_pipeline/hyperframes_reel/compositions/contraportada.html`
- Create: `core/content_pipeline/hyperframes_reel/assets/Poppins-Bold.ttf` (copia del archivo existente)
- Modify: `Dockerfile.worker`
- Modify: `.gitignore` (agregar `core/content_pipeline/hyperframes_reel/node_modules/`)

**Interfaces:**
- Consumes: nada de tareas anteriores.
- Produces: el proyecto HyperFrames renderizable vía
  `node_modules/.bin/hyperframes render . -c compositions/{portada,contraportada}.html -o <output> --variables-file <json> --fps 24`,
  consumido por la Tarea 2. Variables que cada composición espera:
  - `portada.html`: `hook_before`, `hook_highlight`, `hook_after` (strings), `primary_color`, `text_color` (colores), `logo_url` (string, vacío = sin logo).
  - `contraportada.html`: `cta_text` (string), `primary_color`, `text_color` (colores), `logo_url` (string).

- [ ] **Step 1: Crear `package.json`**

```bash
mkdir -p core/content_pipeline/hyperframes_reel/compositions
mkdir -p core/content_pipeline/hyperframes_reel/assets
```

Crea `core/content_pipeline/hyperframes_reel/package.json`:

```json
{
  "name": "cosmic-reel-branding",
  "private": true,
  "type": "module",
  "dependencies": {
    "hyperframes": "0.7.59",
    "gsap": "3.14.2"
  }
}
```

- [ ] **Step 2: Instalar dependencias y generar el lockfile**

```bash
cd core/content_pipeline/hyperframes_reel
npm install
cd /home/anuarbarrera/agente-cosmic
```

Expected: se crea `package-lock.json` y `node_modules/` (con `node_modules/.bin/hyperframes` y `node_modules/gsap/dist/gsap.min.js` presentes). Verificar:

```bash
ls core/content_pipeline/hyperframes_reel/node_modules/.bin/hyperframes
ls core/content_pipeline/hyperframes_reel/node_modules/gsap/dist/gsap.min.js
```

- [ ] **Step 3: Copiar la fuente Poppins-Bold**

```bash
cp core/content_pipeline/static/content_pipeline/fonts/Poppins-Bold.ttf \
   core/content_pipeline/hyperframes_reel/assets/Poppins-Bold.ttf
```

- [ ] **Step 4: Crear `compositions/portada.html`**

```html
<!doctype html>
<html lang="es"
  data-composition-variables='[
    {"id":"hook_before","type":"string","label":"Hook (antes)","default":""},
    {"id":"hook_highlight","type":"string","label":"Hook (resaltado)","default":""},
    {"id":"hook_after","type":"string","label":"Hook (despues)","default":""},
    {"id":"primary_color","type":"color","label":"Color primario","default":"#1a1a2e"},
    {"id":"text_color","type":"color","label":"Color de texto","default":"#ffffff"},
    {"id":"logo_url","type":"string","label":"Logo","default":""}
  ]'
>
<head>
  <meta charset="UTF-8" />
  <script src="../node_modules/gsap/dist/gsap.min.js"></script>
  <style>
    @font-face {
      font-family: 'Poppins'; font-weight: 900;
      src: url('../assets/Poppins-Bold.ttf') format('truetype');
    }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body { width: 1080px; height: 1920px; overflow: hidden; background: #ffffff; }
    .wrap {
      position: absolute; inset: 0; display: flex; flex-direction: column;
      align-items: center; justify-content: center; padding: 0 80px; gap: 60px;
    }
    #logo { width: 200px; height: 200px; object-fit: contain; }
    #hook {
      font-family: 'Poppins', sans-serif; font-weight: 900; font-size: 72px;
      color: #1a1a2e; text-align: center; line-height: 1.25; max-width: 920px;
    }
    #hook-highlight {
      display: inline-block; color: var(--text_color); background: var(--primary_color);
      padding: 4px 20px; border-radius: 16px;
    }
  </style>
</head>
<body>
  <div id="root" data-composition-id="portada" data-start="0" data-duration="3"
       data-width="1080" data-height="1920">
    <div class="wrap">
      <img id="logo" class="clip" data-start="0" data-duration="3"
           data-var-src="logo_url"
           src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==" />
      <div id="hook" class="clip" data-start="0" data-duration="3"><span id="hook-before" data-var-text="hook_before"></span><span id="hook-highlight" data-var-text="hook_highlight"></span><span id="hook-after" data-var-text="hook_after"></span></div>
    </div>
  </div>
  <script>
    const { logo_url } = window.__hyperframes.getVariables();
    if (!logo_url) {
      document.getElementById('logo').style.display = 'none';
    }

    window.__timelines = window.__timelines || {};
    const tl = gsap.timeline({ paused: true });
    tl.from('#logo', { opacity: 0, scale: 0.7, duration: 0.5, ease: 'back.out(1.7)' }, 0);
    tl.from('#hook', { opacity: 0, y: 30, duration: 0.5, ease: 'power2.out' }, 0.15);
    window.__timelines['portada'] = tl;
  </script>
</body>
</html>
```

- [ ] **Step 5: Crear `compositions/contraportada.html`**

```html
<!doctype html>
<html lang="es"
  data-composition-variables='[
    {"id":"cta_text","type":"string","label":"CTA","default":""},
    {"id":"primary_color","type":"color","label":"Color primario","default":"#1a1a2e"},
    {"id":"text_color","type":"color","label":"Color de texto","default":"#ffffff"},
    {"id":"logo_url","type":"string","label":"Logo","default":""}
  ]'
>
<head>
  <meta charset="UTF-8" />
  <script src="../node_modules/gsap/dist/gsap.min.js"></script>
  <style>
    @font-face {
      font-family: 'Poppins'; font-weight: 900;
      src: url('../assets/Poppins-Bold.ttf') format('truetype');
    }
    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body { width: 1080px; height: 1920px; overflow: hidden; background: #ffffff; }
    .wrap {
      position: absolute; inset: 0; display: flex; flex-direction: column;
      align-items: center; justify-content: center; padding: 0 80px; gap: 60px;
    }
    #logo { width: 200px; height: 200px; object-fit: contain; }
    #cta {
      display: inline-block; font-family: 'Poppins', sans-serif; font-weight: 900;
      font-size: 64px; text-align: center; line-height: 1.25; max-width: 920px;
      color: var(--text_color); background: var(--primary_color);
      padding: 24px 48px; border-radius: 24px;
    }
  </style>
</head>
<body>
  <div id="root" data-composition-id="contraportada" data-start="0" data-duration="3"
       data-width="1080" data-height="1920">
    <div class="wrap">
      <img id="logo" class="clip" data-start="0" data-duration="3"
           data-var-src="logo_url"
           src="data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==" />
      <div id="cta" class="clip" data-start="0" data-duration="3" data-var-text="cta_text"></div>
    </div>
  </div>
  <script>
    const { logo_url } = window.__hyperframes.getVariables();
    if (!logo_url) {
      document.getElementById('logo').style.display = 'none';
    }

    window.__timelines = window.__timelines || {};
    const tl = gsap.timeline({ paused: true });
    tl.from('#logo', { opacity: 0, scale: 0.7, duration: 0.5, ease: 'back.out(1.7)' }, 0);
    tl.from('#cta', { opacity: 0, y: 30, duration: 0.5, ease: 'power2.out' }, 0.15);
    window.__timelines['contraportada'] = tl;
  </script>
</body>
</html>
```

- [ ] **Step 6: Verificar las composiciones con `check` y un render real**

```bash
cd core/content_pipeline/hyperframes_reel
node_modules/.bin/hyperframes check -c compositions/portada.html
node_modules/.bin/hyperframes check -c compositions/contraportada.html
echo '{"hook_before":"Transforma tu ","hook_highlight":"futuro","hook_after":" tecnologico","primary_color":"#1a1a2e","text_color":"#ffffff","logo_url":""}' > /tmp/test-portada-vars.json
node_modules/.bin/hyperframes render . -c compositions/portada.html -o /tmp/test-portada.mp4 --variables-file /tmp/test-portada-vars.json --fps 24
cd /home/anuarbarrera/agente-cosmic
```

Expected: `check` reporta 0 findings en ambas (o solo warnings, no errores) y el render produce `/tmp/test-portada.mp4`. Extraer un frame (`ffmpeg -i /tmp/test-portada.mp4 -vframes 1 /tmp/test-portada-frame.png`) y revisarlo visualmente: debe verse "Transforma tu **futuro** tecnologico" con "futuro" resaltado en caja oscura con texto blanco, sin logo (logo_url vacío).

- [ ] **Step 7: Modificar `Dockerfile.worker` — instalar Node.js y `npm ci`**

Reemplaza:
```dockerfile
RUN apt-get update && apt-get install -y postgresql-client ffmpeg && rm -rf /var/lib/apt/lists/*
```
por:
```dockerfile
RUN apt-get update && apt-get install -y postgresql-client ffmpeg curl && rm -rf /var/lib/apt/lists/*
```

Y después de:
```dockerfile
COPY . .
```
agrega:
```dockerfile

# Node.js 22 + HyperFrames (portada/contraportada de reels, ver
# docs/superpowers/specs/2026-07-16-reels-hyperframes-intro-outro-design.md)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    rm -rf /var/lib/apt/lists/*
RUN cd core/content_pipeline/hyperframes_reel && npm ci
```

- [ ] **Step 8: `.gitignore`**

Agrega esta línea a `.gitignore` (si no existe ya una regla equivalente para `node_modules`):
```
core/content_pipeline/hyperframes_reel/node_modules/
```

- [ ] **Step 9: Build real de la imagen y verificación dentro del contenedor**

```bash
docker compose build rqworker
docker compose run --rm rqworker node --version
docker compose run --rm rqworker bash -c "cd core/content_pipeline/hyperframes_reel && node_modules/.bin/hyperframes render . -c compositions/contraportada.html -o /tmp/test.mp4 --variables-file <(echo '{\"cta_text\":\"Contactanos hoy\",\"primary_color\":\"#1a1a2e\",\"text_color\":\"#ffffff\",\"logo_url\":\"\"}') --fps 24 && ls -la /tmp/test.mp4"
```

Expected: `node --version` imprime v22.x.x, el build no falla, y el render dentro del contenedor produce `/tmp/test.mp4` sin errores (confirma que Node/HyperFrames/GSAP quedaron correctamente instalados en la imagen real, sin depender de red en el render — puedes confirmar además que no hay tráfico de red desconectando temporalmente si quieres, pero no es obligatorio para este paso).

- [ ] **Step 10: Commit**

```bash
git add core/content_pipeline/hyperframes_reel/package.json \
        core/content_pipeline/hyperframes_reel/package-lock.json \
        core/content_pipeline/hyperframes_reel/compositions/portada.html \
        core/content_pipeline/hyperframes_reel/compositions/contraportada.html \
        core/content_pipeline/hyperframes_reel/assets/Poppins-Bold.ttf \
        Dockerfile.worker .gitignore
git commit -m "feat(reels): proyecto HyperFrames para portada/contraportada de marca"
```

---

## Task 2: `_generate_branded_segment` + `_split_highlight` + métricas

**Files:**
- Modify: `core/content_pipeline/generators/reel_generator.py`
- Modify: `core/shared/metrics_utils.py`
- Test: `core/content_pipeline/tests/test_reel_generator.py`

**Interfaces:**
- Consumes: el proyecto de la Tarea 1 (ruta fija, no necesita que el proyecto
  exista para que los tests unitarios pasen — se mockea `subprocess.run`).
- Produces: `_split_highlight(text: str, highlight_word: str) -> tuple[str, str, str]`
  (module-level), `ReelGenerator._generate_branded_segment(kind: str, hook_text: str,
  highlight_word: str, tag_cta: str, primary_color: str, logo_url: str) -> bytes | None`,
  `record_hyperframes_generation(kind: str)`, `record_hyperframes_fallback()` —
  consumidos por la Tarea 3.

- [ ] **Step 1: Escribir los tests que fallan**

Agrega a `core/shared/tests/test_metrics.py`, siguiendo EXACTAMENTE el
mismo patrón que la clase existente `TestRecordPlaywrightOverlayFallback`
en ese mismo archivo (captura los incrementos en un diccionario vía
`side_effect`, no `assert_called_once_with` — así es como ya se prueban
las demás funciones de `metrics_utils.py` en este archivo):

```python
class TestRecordHyperframesGeneration:
    def test_records_generation_for_portada(self):
        from core.shared.metrics_utils import record_hyperframes_generation
        increments = {}

        def fake_redis_inc(key, amount=1.0):
            increments[key] = increments.get(key, 0) + amount

        with patch('core.shared.metrics_utils._redis_inc', side_effect=fake_redis_inc):
            record_hyperframes_generation('portada')

        assert increments.get('reel_hyperframes_portada_total', 0) == 1

    def test_records_generation_for_contraportada(self):
        from core.shared.metrics_utils import record_hyperframes_generation
        increments = {}

        def fake_redis_inc(key, amount=1.0):
            increments[key] = increments.get(key, 0) + amount

        with patch('core.shared.metrics_utils._redis_inc', side_effect=fake_redis_inc):
            record_hyperframes_generation('contraportada')

        assert increments.get('reel_hyperframes_contraportada_total', 0) == 1


class TestRecordHyperframesFallback:
    def test_records_fallback(self):
        from core.shared.metrics_utils import record_hyperframes_fallback
        increments = {}

        def fake_redis_inc(key, amount=1.0):
            increments[key] = increments.get(key, 0) + amount

        with patch('core.shared.metrics_utils._redis_inc', side_effect=fake_redis_inc):
            record_hyperframes_fallback()

        assert increments.get('reel_hyperframes_fallback_total', 0) == 1
```

(Si el archivo no tiene ya `from unittest.mock import patch` importado,
agrégalo al inicio.)

Agrega a `core/content_pipeline/tests/test_reel_generator.py`, después de
`class TestReadableTextColor` (usa el mismo patrón de esa clase):

```python
class TestSplitHighlight:
    def test_splits_around_highlight_word(self):
        from core.content_pipeline.generators.reel_generator import _split_highlight
        before, highlight, after = _split_highlight('Descubre algo nuevo', 'algo')
        assert before == 'Descubre '
        assert highlight == 'algo'
        assert after == ' nuevo'

    def test_case_insensitive_match(self):
        from core.content_pipeline.generators.reel_generator import _split_highlight
        before, highlight, after = _split_highlight('Descubre ALGO nuevo', 'algo')
        assert highlight == 'ALGO'

    def test_returns_full_text_as_before_when_word_not_found(self):
        from core.content_pipeline.generators.reel_generator import _split_highlight
        before, highlight, after = _split_highlight('Descubre algo nuevo', 'inexistente')
        assert before == 'Descubre algo nuevo'
        assert highlight == ''
        assert after == ''

    def test_returns_full_text_as_before_when_no_highlight_word(self):
        from core.content_pipeline.generators.reel_generator import _split_highlight
        before, highlight, after = _split_highlight('Descubre algo nuevo', '')
        assert before == 'Descubre algo nuevo'
        assert highlight == ''
        assert after == ''
```

Agrega esta clase nueva, después de `TestAnimateStillToClip` (mismo
patrón de mock de `subprocess.run` que esa clase):

```python
class TestGenerateBrandedSegment:
    def test_portada_builds_variables_and_renders(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-portada-mp4'

        captured = {}

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[cmd.index('--variables-file') + 1]) as f:
                captured['variables'] = json.load(f)
            captured['cmd'] = cmd
            captured['cwd'] = kwargs.get('cwd')
            output_path = cmd[cmd.index('-o') + 1]
            with open(output_path, 'wb') as f:
                f.write(fake_output)
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=fake_run), \
             patch('core.content_pipeline.generators.reel_generator.record_hyperframes_generation') as mock_record:
            result = gen._generate_branded_segment(
                'portada', 'Descubre algo nuevo', 'algo', 'Compra ahora', '#1a1a2e', '',
            )

        assert result == fake_output
        assert captured['variables'] == {
            'hook_before': 'Descubre ', 'hook_highlight': 'algo', 'hook_after': ' nuevo',
            'primary_color': '#1a1a2e', 'text_color': 'white', 'logo_url': '',
        }
        assert '-c' in captured['cmd']
        assert captured['cmd'][captured['cmd'].index('-c') + 1] == 'compositions/portada.html'
        assert '--fps' in captured['cmd']
        assert captured['cmd'][captured['cmd'].index('--fps') + 1] == '24'
        mock_record.assert_called_once_with('portada')

    def test_contraportada_builds_variables_and_renders(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-contraportada-mp4'

        captured = {}

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[cmd.index('--variables-file') + 1]) as f:
                captured['variables'] = json.load(f)
            captured['cmd'] = cmd
            output_path = cmd[cmd.index('-o') + 1]
            with open(output_path, 'wb') as f:
                f.write(fake_output)
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=fake_run), \
             patch('core.content_pipeline.generators.reel_generator.record_hyperframes_generation'):
            result = gen._generate_branded_segment(
                'contraportada', 'Descubre algo nuevo', 'algo', 'Compra ahora', '#1a1a2e', 'https://logo.png',
            )

        assert result == fake_output
        assert captured['variables'] == {
            'cta_text': 'Compra ahora', 'primary_color': '#1a1a2e',
            'text_color': 'white', 'logo_url': 'https://logo.png',
        }
        assert captured['cmd'][captured['cmd'].index('-c') + 1] == 'compositions/contraportada.html'

    def test_returns_none_on_subprocess_error(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=subprocess.CalledProcessError(1, 'hyperframes')):
            result = gen._generate_branded_segment(
                'portada', 'Descubre algo nuevo', 'algo', 'Compra ahora', '#1a1a2e', '',
            )
        assert result is None
```

Verifica que `test_reel_generator.py` tenga `import json` y `import subprocess`
al inicio del archivo — si no los tiene, agrégalos junto a las demás
importaciones de `unittest.mock`.

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -k "SplitHighlight or GenerateBrandedSegment" -v`
Run: `docker compose exec -T backend python -m pytest core/shared/tests/test_metrics.py -k "Hyperframes" -v`
Expected: FAIL — ninguna de estas funciones/métodos existe todavía.

- [ ] **Step 3: Agregar `record_hyperframes_generation` y `record_hyperframes_fallback`**

En `core/shared/metrics_utils.py`, justo después de la función
`record_playwright_overlay_fallback` existente:

```python
def record_hyperframes_generation(kind: str):
    """Registra una generacion exitosa de portada/contraportada via HyperFrames."""
    _redis_inc(f'reel_hyperframes_{kind}_total')


def record_hyperframes_fallback():
    """Registra que portada/contraportada fallaron y el reel cayo a la estructura sin marca (Parte A)."""
    _redis_inc('reel_hyperframes_fallback_total')
```

- [ ] **Step 4: Actualizar imports en `reel_generator.py`**

Cambia:
```python
import base64
import html as _html
import logging
import os
import re
import subprocess
import tempfile
import time
```
por:
```python
import base64
import html as _html
import json
import logging
import os
import re
import subprocess
import tempfile
import time
```

Y cambia:
```python
from core.shared.metrics_utils import (
    track_external_api, record_tokens, record_veo_generation,
    record_lyria_generation, record_tts_generation,
    record_playwright_overlay_fallback, record_imagen_generation,
)
```
por:
```python
from core.shared.metrics_utils import (
    track_external_api, record_tokens, record_veo_generation,
    record_lyria_generation, record_tts_generation,
    record_playwright_overlay_fallback, record_imagen_generation,
    record_hyperframes_generation, record_hyperframes_fallback,
)
```

- [ ] **Step 5: Agregar constantes de HyperFrames**

Después de la línea `_DEFAULT_CLIP_FPS = 24.0  # usado solo cuando no hay clip real de Veo del cual medir fps`:

```python
_BRANDED_SEGMENT_DURATION_SECONDS = 3.0
_HYPERFRAMES_PROJECT_DIR = os.path.normpath(os.path.join(
    os.path.dirname(__file__), '..', 'hyperframes_reel',
))
_HYPERFRAMES_BINARY = os.path.join(_HYPERFRAMES_PROJECT_DIR, 'node_modules', '.bin', 'hyperframes')
_HYPERFRAMES_TIMEOUT_SECONDS = 120
```

- [ ] **Step 6: Agregar `_split_highlight` (module-level)**

Justo después de la función `_readable_text_color` existente (antes de
`_write_tmp_png`):

```python
def _split_highlight(text: str, highlight_word: str) -> tuple[str, str, str]:
    if not highlight_word:
        return text, '', ''
    idx = text.lower().find(highlight_word.lower())
    if idx == -1:
        return text, '', ''
    before = text[:idx]
    highlight = text[idx:idx + len(highlight_word)]
    after = text[idx + len(highlight_word):]
    return before, highlight, after
```

- [ ] **Step 7: Agregar `_generate_branded_segment` a la clase `ReelGenerator`**

Justo después del método `_generate_still_scene_clip` (antes de
`_generate_video_clips`):

```python
    def _generate_branded_segment(self, kind: str, hook_text: str, highlight_word: str,
                                   tag_cta: str, primary_color: str, logo_url: str) -> bytes | None:
        text_color = _readable_text_color(primary_color)
        if kind == 'portada':
            before, highlight, after = _split_highlight(hook_text, highlight_word)
            variables = {
                'hook_before': before, 'hook_highlight': highlight, 'hook_after': after,
                'primary_color': primary_color, 'text_color': text_color, 'logo_url': logo_url,
            }
            composition = 'compositions/portada.html'
        else:
            variables = {
                'cta_text': tag_cta, 'primary_color': primary_color,
                'text_color': text_color, 'logo_url': logo_url,
            }
            composition = 'compositions/contraportada.html'

        with tempfile.TemporaryDirectory() as tmp:
            vars_path = os.path.join(tmp, 'vars.json')
            with open(vars_path, 'w') as f:
                json.dump(variables, f)
            output_path = os.path.join(tmp, 'output.mp4')
            try:
                subprocess.run(
                    [_HYPERFRAMES_BINARY, 'render', '.', '-c', composition,
                     '-o', output_path, '--variables-file', vars_path, '--fps', '24', '--quiet'],
                    cwd=_HYPERFRAMES_PROJECT_DIR, check=True, capture_output=True,
                    timeout=_HYPERFRAMES_TIMEOUT_SECONDS,
                )
            except Exception as e:
                logger.warning(f"HyperFrames {kind} generation failed: {e}")
                return None
            record_hyperframes_generation(kind)
            with open(output_path, 'rb') as f:
                return f.read()
```

- [ ] **Step 8: Correr los tests para verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -k "SplitHighlight or GenerateBrandedSegment" -v`
Run: `docker compose exec -T backend python -m pytest core/shared/tests/test_metrics.py -k "Hyperframes" -v`
Expected: todos en PASS.

- [ ] **Step 9: Commit**

```bash
git add core/content_pipeline/generators/reel_generator.py core/shared/metrics_utils.py \
        core/content_pipeline/tests/test_reel_generator.py core/shared/tests/test_metrics.py
git commit -m "feat(reels): _generate_branded_segment invoca HyperFrames para portada/contraportada"
```

---

## Task 3: Orquestación — `_generate_clips_with_branding` + `_assemble_reel` + `generate()`

**Files:**
- Modify: `core/content_pipeline/generators/reel_generator.py`
- Modify: `core/content_pipeline/tasks.py`
- Test: `core/content_pipeline/tests/test_reel_generator.py`

**Interfaces:**
- Consumes: `_generate_branded_segment` (Tarea 2), `_generate_video_clips`/
  `_probe_clip_dimensions` (existentes, sin cambios).
- Produces: `ReelGenerator._generate_clips_with_branding(scene_prompts: list[str],
  hook_text: str, highlight_word: str, tag_cta: str, primary_color: str,
  logo_url: str) -> tuple[list[bytes], bool]` (segundo valor = `True` si la
  marca se aplicó). `_assemble_reel` gana el parámetro
  `skip_hook_cta_overlay: bool = False`. `generate()` gana el parámetro
  `logo_url: str = ''`.

- [ ] **Step 1: Escribir los tests que fallan**

En `core/content_pipeline/tests/test_reel_generator.py`, agrega esta clase
nueva después de `TestGenerateBrandedSegment`:

```python
class TestGenerateClipsWithBranding:
    def test_branding_success_prepends_and_appends_normalized_segments(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_video_clips', return_value=[b'v', b's1', b's2', b's3', b's4', b's5']), \
             patch.object(gen, '_probe_clip_dimensions', return_value=(720, 1280, 24.0)), \
             patch.object(gen, '_generate_branded_segment', side_effect=[b'portada-raw', b'contra-raw']) as mock_branded, \
             patch.object(gen, '_normalize_branded_segment', side_effect=[b'portada-norm', b'contra-norm']) as mock_norm:
            clips, has_branding = gen._generate_clips_with_branding(
                ['scene 1', 'scene 2'], 'Hook', 'word', 'CTA', '#1a1a2e', 'https://logo.png',
            )

        assert has_branding is True
        assert clips == [b'portada-norm', b'v', b's1', b's2', b's3', b's4', b's5', b'contra-norm']
        assert mock_branded.call_args_list == [
            call('portada', 'Hook', 'word', 'CTA', '#1a1a2e', 'https://logo.png'),
            call('contraportada', 'Hook', 'word', 'CTA', '#1a1a2e', 'https://logo.png'),
        ]
        assert mock_norm.call_args_list == [
            call(b'portada-raw', 720, 1280, 24.0),
            call(b'contra-raw', 720, 1280, 24.0),
        ]

    def test_falls_back_when_portada_fails_completely(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_video_clips', return_value=[b'v', b's1', b's2', b's3', b's4', b's5']), \
             patch.object(gen, '_probe_clip_dimensions', return_value=(720, 1280, 24.0)), \
             patch.object(gen, '_generate_branded_segment', return_value=None) as mock_branded, \
             patch.object(gen, '_normalize_branded_segment') as mock_norm, \
             patch('core.content_pipeline.generators.reel_generator.record_hyperframes_fallback') as mock_fallback:
            clips, has_branding = gen._generate_clips_with_branding(
                ['scene 1', 'scene 2'], 'Hook', 'word', 'CTA', '#1a1a2e', '',
            )

        assert has_branding is False
        assert clips == [b'v', b's1', b's2', b's3', b's4', b's5']
        mock_norm.assert_not_called()
        mock_fallback.assert_called_once()
        # 2 intentos de portada (1 + reintento) antes de rendirse
        assert mock_branded.call_count == 2

    def test_skips_branding_attempt_when_body_has_fewer_than_3_clips(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_video_clips', return_value=[b'v']), \
             patch.object(gen, '_generate_branded_segment') as mock_branded:
            clips, has_branding = gen._generate_clips_with_branding(
                ['scene 1'], 'Hook', 'word', 'CTA', '#1a1a2e', '',
            )

        assert clips == [b'v']
        assert has_branding is False
        mock_branded.assert_not_called()
```

Agrega `call` a la lista de imports de `unittest.mock` si no está ya (ya
se agregó en el plan de la Parte A — confirma que sigue presente).

Agrega esta clase nueva para `_normalize_branded_segment` después de
`TestGenerateClipsWithBranding`:

```python
class TestNormalizeBrandedSegment:
    def test_builds_scale_command_with_exact_dimensions(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'normalized-mp4'

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[-1], 'wb') as f:
                f.write(fake_output)
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=fake_run) as mock_run:
            result = gen._normalize_branded_segment(b'raw-mp4', 720, 1280, 24.0)

        assert result == fake_output
        cmd = mock_run.call_args.args[0]
        assert cmd[0] == 'ffmpeg'
        vf_idx = cmd.index('-vf')
        assert cmd[vf_idx + 1] == 'scale=720:1280'
        r_idx = cmd.index('-r')
        assert cmd[r_idx + 1] == '24.0'
```

Ahora actualiza `class TestAssembleReel` — agrega estos 2 tests nuevos
(después de `test_omits_subtitle_filters_when_no_subtitles`):

```python
    @override_settings(REEL_TEXT_OVERLAY_ENGINE='drawtext')
    def test_skip_hook_cta_overlay_without_subtitles_uses_plain_map(self, tmp_path):
        # skip_hook_cta_overlay=True + sin subtitulos: filter_parts queda
        # vacio, no debe armarse -filter_complex (romperia -map '[0:v]' sin
        # ningun filtro que defina esa etiqueta) — usa -map 0:v directo.
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=_fake_ffmpeg_run(fake_output)) as mock_run, \
             patch('core.content_pipeline.generators.reel_generator._build_hook_filter_parts') as mock_hook, \
             patch('core.content_pipeline.generators.reel_generator._build_cta_filter_parts') as mock_cta:
            gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                script=_FAKE_SCRIPT_FOR_ASSEMBLE, colors=['#1a1a2e'],
                skip_hook_cta_overlay=True,
            )

        mock_hook.assert_not_called()
        mock_cta.assert_not_called()
        overlay_cmd = mock_run.call_args_list[3].args[0]
        assert '-filter_complex' not in overlay_cmd
        map_idx = overlay_cmd.index('-map')
        assert overlay_cmd[map_idx + 1] == '0:v'

    @override_settings(REEL_TEXT_OVERLAY_ENGINE='drawtext')
    def test_skip_hook_cta_overlay_with_subtitles_keeps_filter_complex(self, tmp_path):
        # skip_hook_cta_overlay=True + CON subtitulos: filter_parts no queda
        # vacio (los subtitulos si aportan filtros) — sigue el camino normal
        # de -filter_complex/-map '[subN]'.
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'
        subtitles = [{'text': 'Hola.', 'start': 0.0, 'end': 1.0}]

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=_fake_ffmpeg_run(fake_output)) as mock_run:
            gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                script=_FAKE_SCRIPT_FOR_ASSEMBLE, colors=['#1a1a2e'],
                subtitles=subtitles, skip_hook_cta_overlay=True,
            )

        overlay_cmd = mock_run.call_args_list[3].args[0]
        filter_complex_idx = overlay_cmd.index('-filter_complex')
        filter_complex = overlay_cmd[filter_complex_idx + 1]
        assert "text='Hola.'" in filter_complex
        map_idx = overlay_cmd.index('-map')
        assert overlay_cmd[map_idx + 1] == '[sub0]'
```

Ahora actualiza `class TestGenerate` (4 tests existentes) — el mock cambia
de `_generate_video_clips` a `_generate_clips_with_branding`, y su
`return_value` pasa de `list[bytes]` a `tuple[list[bytes], bool]`.
Reemplaza los 4 tests completos:

```python
class TestGenerate:
    def test_returns_video_and_poster_urls_on_success(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_clips_with_branding', return_value=([b'c1', b'c2', b'c3'], False)) as mock_clips, \
             patch.object(gen, '_generate_music', return_value=b'music'), \
             patch.object(gen, '_generate_narration', return_value=b'narration'), \
             patch('core.content_pipeline.generators.reel_generator.SubtitleGenerator') as mock_sub_gen, \
             patch.object(gen, '_assemble_reel', return_value=b'final-mp4') as mock_assemble, \
             patch.object(gen, '_extract_poster_frame', return_value=b'poster-png'), \
             patch.object(gen, '_upload_video_to_storage', return_value='https://storage.test/reel.mp4') as mock_up_video, \
             patch.object(gen, '_upload_to_storage', return_value='https://storage.test/poster.png') as mock_up_poster:
            mock_sub_gen.return_value.generate.return_value = [{'text': 'Hola.', 'start': 0.0, 'end': 1.0}]
            video_url, poster_url = gen.generate(_FAKE_SCRIPT, ['#1a1a2e'], 'job1-day1')

        assert video_url == 'https://storage.test/reel.mp4'
        assert poster_url == 'https://storage.test/poster.png'
        mock_clips.assert_called_once_with(
            _FAKE_SCRIPT['scene_prompts'], _FAKE_SCRIPT['hook_text'], _FAKE_SCRIPT['highlight_word'],
            _FAKE_SCRIPT['tag_cta'], '#1a1a2e', '',
        )
        mock_up_video.assert_called_once_with(b'final-mp4', 'job1-day1')
        mock_up_poster.assert_called_once_with(b'poster-png', 'job1-day1-poster')
        mock_assemble.assert_called_once_with(
            [b'c1', b'c2', b'c3'], b'music', b'narration', _FAKE_SCRIPT, ['#1a1a2e'],
            [{'text': 'Hola.', 'start': 0.0, 'end': 1.0}],
            skip_hook_cta_overlay=False,
        )

    def test_passes_logo_url_and_skip_flag_when_branding_succeeds(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_clips_with_branding', return_value=([b'p', b'c1', b'c2', b'c3', b'c'], True)) as mock_clips, \
             patch.object(gen, '_generate_music', return_value=None), \
             patch.object(gen, '_generate_narration', return_value=None), \
             patch.object(gen, '_assemble_reel', return_value=b'final-mp4') as mock_assemble, \
             patch.object(gen, '_extract_poster_frame', return_value=b'poster-png'), \
             patch.object(gen, '_upload_video_to_storage', return_value='url1'), \
             patch.object(gen, '_upload_to_storage', return_value='url2'):
            gen.generate(_FAKE_SCRIPT, ['#1a1a2e'], 'job1-day1', logo_url='https://logo.png')

        mock_clips.assert_called_once_with(
            _FAKE_SCRIPT['scene_prompts'], _FAKE_SCRIPT['hook_text'], _FAKE_SCRIPT['highlight_word'],
            _FAKE_SCRIPT['tag_cta'], '#1a1a2e', 'https://logo.png',
        )
        assert mock_assemble.call_args.kwargs['skip_hook_cta_overlay'] is True

    def test_skips_subtitle_generation_when_narration_fails(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_clips_with_branding', return_value=([b'c1', b'c2', b'c3'], False)), \
             patch.object(gen, '_generate_music', return_value=None), \
             patch.object(gen, '_generate_narration', return_value=None), \
             patch('core.content_pipeline.generators.reel_generator.SubtitleGenerator') as mock_sub_gen, \
             patch.object(gen, '_assemble_reel', return_value=b'final-mp4') as mock_assemble, \
             patch.object(gen, '_extract_poster_frame', return_value=b'poster-png'), \
             patch.object(gen, '_upload_video_to_storage', return_value='url1'), \
             patch.object(gen, '_upload_to_storage', return_value='url2'):
            gen.generate(_FAKE_SCRIPT, ['#1a1a2e'], 'job1-day1')

        mock_sub_gen.return_value.generate.assert_not_called()
        assembled_args = mock_assemble.call_args.args
        assert assembled_args[-1] == []

    def test_returns_empty_strings_when_fewer_than_3_clips_generated(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_clips_with_branding', return_value=([b'c1', b'c2'], False)):
            video_url, poster_url = gen.generate(_FAKE_SCRIPT, ['#1a1a2e'], 'job1-day1')
        assert (video_url, poster_url) == ('', '')

    def test_returns_empty_strings_when_assembly_raises(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_clips_with_branding', return_value=([b'c1', b'c2', b'c3'], False)), \
             patch.object(gen, '_generate_music', return_value=None), \
             patch.object(gen, '_generate_narration', return_value=None), \
             patch.object(gen, '_assemble_reel', side_effect=Exception('ffmpeg error')):
            video_url, poster_url = gen.generate(_FAKE_SCRIPT, ['#1a1a2e'], 'job1-day1')
        assert (video_url, poster_url) == ('', '')
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -v 2>&1 | tail -80`
Expected: FAIL — `_generate_clips_with_branding`/`_normalize_branded_segment`
no existen, `_assemble_reel` no acepta `skip_hook_cta_overlay`, `generate()`
sigue llamando `_generate_video_clips` directo.

- [ ] **Step 3: Agregar `_normalize_branded_segment`**

Justo después de `_generate_branded_segment` (agregada en la Tarea 2):

```python
    def _normalize_branded_segment(self, segment_bytes: bytes, width: int, height: int, fps: float) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            input_path = os.path.join(tmp, 'input.mp4')
            with open(input_path, 'wb') as f:
                f.write(segment_bytes)
            output_path = os.path.join(tmp, 'output.mp4')
            subprocess.run(
                ['ffmpeg', '-y', '-i', input_path,
                 '-vf', f'scale={width}:{height}', '-r', str(fps),
                 '-c:v', 'libx264', '-pix_fmt', 'yuv420p', output_path],
                check=True, capture_output=True,
            )
            with open(output_path, 'rb') as f:
                return f.read()
```

- [ ] **Step 4: Agregar `_generate_clips_with_branding`**

Justo después de `_normalize_branded_segment`:

```python
    def _generate_clips_with_branding(self, scene_prompts: list[str], hook_text: str,
                                       highlight_word: str, tag_cta: str, primary_color: str,
                                       logo_url: str) -> tuple[list[bytes], bool]:
        clips = self._generate_video_clips(scene_prompts)
        if len(clips) < 3:
            return clips, False

        width, height, fps = self._probe_clip_dimensions(clips[0])

        portada = self._generate_branded_segment(
            'portada', hook_text, highlight_word, tag_cta, primary_color, logo_url,
        )
        if portada is None:
            portada = self._generate_branded_segment(
                'portada', hook_text, highlight_word, tag_cta, primary_color, logo_url,
            )  # 1 reintento

        contraportada = self._generate_branded_segment(
            'contraportada', hook_text, highlight_word, tag_cta, primary_color, logo_url,
        )
        if contraportada is None:
            contraportada = self._generate_branded_segment(
                'contraportada', hook_text, highlight_word, tag_cta, primary_color, logo_url,
            )  # 1 reintento

        if portada is None or contraportada is None:
            logger.warning("Portada o contraportada fallaron tras reintento, reel sin marca (estructura Parte A)")
            record_hyperframes_fallback()
            return clips, False

        portada_normalized = self._normalize_branded_segment(portada, width, height, fps)
        contraportada_normalized = self._normalize_branded_segment(contraportada, width, height, fps)
        return [portada_normalized] + clips + [contraportada_normalized], True
```

- [ ] **Step 5: `_assemble_reel` gana `skip_hook_cta_overlay`**

Cambia la firma:
```python
    def _assemble_reel(self, clips: list[bytes], music: bytes | None, narration: bytes | None,
                        script: dict, colors: list[str], subtitles: list[dict] | None = None) -> bytes:
```
por:
```python
    def _assemble_reel(self, clips: list[bytes], music: bytes | None, narration: bytes | None,
                        script: dict, colors: list[str], subtitles: list[dict] | None = None,
                        skip_hook_cta_overlay: bool = False) -> bytes:
```

Envuelve el bloque completo de hook/CTA (desde `hook_png = cta_png = None`
hasta el final del `else: cta_parts, last_label = _build_cta_filter_parts(...)`)
en un `if not skip_hook_cta_overlay:`. El bloque actual es:

```python
            hook_png = cta_png = None
            if settings.REEL_TEXT_OVERLAY_ENGINE == 'playwright':
                hook_png = self._render_text_overlay_playwright(
                    script['hook_text'], script['highlight_word'], 'hook', primary_color,
                )
                cta_png = self._render_text_overlay_playwright(
                    '', '', 'cta', primary_color, cta_text=script['tag_cta'],
                )

            scaled_w = max(1, int(_VIDEO_WIDTH * scale))
            scaled_h = max(1, int(_VIDEO_HEIGHT * scale))
            extra_inputs = []
            filter_parts = []
            last_label = '0:v'

            if hook_png is not None:
                extra_inputs += ['-i', _write_tmp_png(tmp, 'hook.png', hook_png)]
                idx = len(extra_inputs) // 2
                filter_parts.append(
                    f"[{idx}:v]scale={scaled_w}:{scaled_h}[hookscaled];"
                    f"[{last_label}][hookscaled]overlay=0:0:enable='between(t,0,{_HOOK_END_SECONDS})'[hookout]"
                )
                last_label = 'hookout'
            else:
                filter_parts_h, last_label = _build_hook_filter_parts(
                    script['hook_text'], script['highlight_word'], primary_color, last_label,
                    video_width=video_width, scale=scale,
                )
                filter_parts += filter_parts_h

            if cta_png is not None:
                extra_inputs += ['-i', _write_tmp_png(tmp, 'cta.png', cta_png)]
                idx = len(extra_inputs) // 2
                filter_parts.append(
                    f"[{idx}:v]scale={scaled_w}:{scaled_h}[ctascaled];"
                    f"[{last_label}][ctascaled]overlay=0:0:enable='between(t,{cta_start},{duration})'[ctaout]"
                )
                last_label = 'ctaout'
            else:
                cta_parts, last_label = _build_cta_filter_parts(
                    script['tag_cta'], primary_color, last_label, cta_start, duration, scale=scale,
                )
                filter_parts += cta_parts
```

Reemplázalo por (idéntico contenido, con la guarda nueva y la
inicialización de variables que quedaban fuera del bloque movida arriba
para que sigan existiendo cuando se salta el bloque):

```python
            extra_inputs = []
            filter_parts = []
            last_label = '0:v'

            if not skip_hook_cta_overlay:
                scaled_w = max(1, int(_VIDEO_WIDTH * scale))
                scaled_h = max(1, int(_VIDEO_HEIGHT * scale))
                hook_png = cta_png = None
                if settings.REEL_TEXT_OVERLAY_ENGINE == 'playwright':
                    hook_png = self._render_text_overlay_playwright(
                        script['hook_text'], script['highlight_word'], 'hook', primary_color,
                    )
                    cta_png = self._render_text_overlay_playwright(
                        '', '', 'cta', primary_color, cta_text=script['tag_cta'],
                    )

                if hook_png is not None:
                    extra_inputs += ['-i', _write_tmp_png(tmp, 'hook.png', hook_png)]
                    idx = len(extra_inputs) // 2
                    filter_parts.append(
                        f"[{idx}:v]scale={scaled_w}:{scaled_h}[hookscaled];"
                        f"[{last_label}][hookscaled]overlay=0:0:enable='between(t,0,{_HOOK_END_SECONDS})'[hookout]"
                    )
                    last_label = 'hookout'
                else:
                    filter_parts_h, last_label = _build_hook_filter_parts(
                        script['hook_text'], script['highlight_word'], primary_color, last_label,
                        video_width=video_width, scale=scale,
                    )
                    filter_parts += filter_parts_h

                if cta_png is not None:
                    extra_inputs += ['-i', _write_tmp_png(tmp, 'cta.png', cta_png)]
                    idx = len(extra_inputs) // 2
                    filter_parts.append(
                        f"[{idx}:v]scale={scaled_w}:{scaled_h}[ctascaled];"
                        f"[{last_label}][ctascaled]overlay=0:0:enable='between(t,{cta_start},{duration})'[ctaout]"
                    )
                    last_label = 'ctaout'
                else:
                    cta_parts, last_label = _build_cta_filter_parts(
                        script['tag_cta'], primary_color, last_label, cta_start, duration, scale=scale,
                    )
                    filter_parts += cta_parts
```

`extra_inputs`, `filter_parts`, `last_label` quedan definidas fuera del
`if` porque el bucle de subtítulos (justo después de este bloque, sin
cambios) las sigue necesitando sin importar el valor de
`skip_hook_cta_overlay`. `scaled_w`/`scaled_h` solo se usan dentro del
bloque de hook/CTA, por eso quedan definidas adentro del `if`.

**Caso límite nuevo que este cambio introduce:** si `skip_hook_cta_overlay=True`
Y no hay subtítulos (`subtitles` vacío o `None`), `filter_parts` queda
completamente vacío — algo que antes de este cambio nunca pasaba (hook y
CTA siempre aportaban al menos 1 filtro cada uno). Con `filter_parts`
vacío, `-filter_complex ''` combinado con `-map '[0:v]'` (sintaxis de
salida de filtro, no de stream de entrada) rompe el comando de ffmpeg —
no hay ningún filtro que defina la etiqueta `[0:v]`. Localiza el bloque
existente justo después del bucle de subtítulos (sin cambios hasta ahora):

```python
            filter_complex = ';'.join(filter_parts)
            overlay_path = os.path.join(tmp, 'overlay.mp4')
            subprocess.run(
                ['ffmpeg', '-y', '-i', concat_path] + extra_inputs +
                ['-filter_complex', filter_complex,
                 '-map', f'[{last_label}]', '-t', str(duration), '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                 overlay_path],
                check=True, capture_output=True,
            )
```

Reemplázalo por:

```python
            overlay_path = os.path.join(tmp, 'overlay.mp4')
            if filter_parts:
                filter_complex = ';'.join(filter_parts)
                overlay_cmd = (
                    ['ffmpeg', '-y', '-i', concat_path] + extra_inputs +
                    ['-filter_complex', filter_complex,
                     '-map', f'[{last_label}]', '-t', str(duration), '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                     overlay_path]
                )
            else:
                # skip_hook_cta_overlay=True y sin subtitulos: ningun filtro que
                # aplicar. -map 0:v (sin corchetes) referencia el stream de video
                # de entrada directo, sin depender de una etiqueta de filter_complex
                # que no existiria.
                overlay_cmd = (
                    ['ffmpeg', '-y', '-i', concat_path] + extra_inputs +
                    ['-map', '0:v', '-t', str(duration), '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                     overlay_path]
                )
            subprocess.run(overlay_cmd, check=True, capture_output=True)
```

- [ ] **Step 6: `generate()` usa la nueva orquestación**

Reemplaza el método completo:
```python
    def generate(self, script: dict, colors: list[str], filename_prefix: str) -> tuple[str, str]:
        try:
            clips = self._generate_video_clips(script['scene_prompts'])
            if len(clips) < 3:
                logger.warning(f"Reel abortado: solo {len(clips)}/3 clips de Veo generados")
                return '', ''

            music = self._generate_music(script['music_mood'])
            narration = self._generate_narration(script['narration_script'])
            subtitles = []
            if narration is not None:
                subtitles = SubtitleGenerator().generate(narration, script['narration_script'])

            final_video = self._assemble_reel(clips, music, narration, script, colors, subtitles)
            poster = self._extract_poster_frame(final_video)

            video_url = self._upload_video_to_storage(final_video, filename_prefix)
            poster_url = self._upload_to_storage(poster, f'{filename_prefix}-poster')
            return video_url, poster_url
        except Exception as e:
            logger.error(f"ReelGenerator.generate error: {e}")
            return '', ''
```
por:
```python
    def generate(self, script: dict, colors: list[str], filename_prefix: str,
                 logo_url: str = '') -> tuple[str, str]:
        try:
            primary_color = colors[0] if colors else '#e94560'
            clips, has_branding = self._generate_clips_with_branding(
                script['scene_prompts'], script['hook_text'], script['highlight_word'],
                script['tag_cta'], primary_color, logo_url,
            )
            if len(clips) < 3:
                logger.warning(f"Reel abortado: solo {len(clips)}/3 clips de Veo generados")
                return '', ''

            music = self._generate_music(script['music_mood'])
            narration = self._generate_narration(script['narration_script'])
            subtitles = []
            if narration is not None:
                subtitles = SubtitleGenerator().generate(narration, script['narration_script'])

            final_video = self._assemble_reel(
                clips, music, narration, script, colors, subtitles,
                skip_hook_cta_overlay=has_branding,
            )
            poster = self._extract_poster_frame(final_video)

            video_url = self._upload_video_to_storage(final_video, filename_prefix)
            poster_url = self._upload_to_storage(poster, f'{filename_prefix}-poster')
            return video_url, poster_url
        except Exception as e:
            logger.error(f"ReelGenerator.generate error: {e}")
            return '', ''
```

- [ ] **Step 7: `tasks.py` pasa `logo_url`**

En `core/content_pipeline/tasks.py`, cambia:
```python
        video_url, poster_url = reel_gen.generate(script=script, colors=kwargs.get('colors', []), filename_prefix=filename)
```
por:
```python
        video_url, poster_url = reel_gen.generate(
            script=script, colors=kwargs.get('colors', []), filename_prefix=filename,
            logo_url=brand_dna.logo_url if brand_dna else '',
        )
```

- [ ] **Step 8: Correr los tests para verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -v`
Expected: todos en PASS.

- [ ] **Step 9: Correr la suite completa del proyecto**

Run: `docker compose exec -T backend python -m pytest`
Expected: todos en PASS (incluye `core/content_pipeline/tests/test_tasks.py`,
que puede tener tests existentes sobre `_generate_post_media`/reels —
revisar si alguno necesita `brand_dna.logo_url` en su fixture/mock; si un
test usa un `Mock()`/`MagicMock()` para `brand_dna` sin `logo_url`
configurado, `Mock().logo_url` devuelve otro Mock automáticamente, no
falla, pero si algún test usa una instancia REAL de `BrandDNA` sin ese
campo poblado, `logo_url` ya tiene `default=''` a nivel de modelo — no
debería requerir cambios).

- [ ] **Step 10: Commit**

```bash
git add core/content_pipeline/generators/reel_generator.py core/content_pipeline/tasks.py \
        core/content_pipeline/tests/test_reel_generator.py
git commit -m "feat(reels): orquestar portada/contraportada con fallback a la estructura Parte A"
```

---

## Task 4: Verificación real end-to-end (no delegar a agente externo — la ejecuta el controlador de esta sesión)

Mismo patrón que las verificaciones reales anteriores — gasta cuota real de
Veo/Imagen y corre Node/Chrome headless real dentro del contenedor.

- [ ] **Step 1: Levantar el stack con el código e imagen nuevos**

```bash
docker compose build rqworker
docker compose up -d --force-recreate --no-deps --scale rqworker=3 backend rqworker
```

- [ ] **Step 2: Generar un guion + reel real de punta a punta, con y sin logo**

```bash
docker compose exec -T backend python manage.py shell -c "
from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
from core.content_pipeline.generators.reel_generator import ReelGenerator
from core.brand_dna.models import BrandDNA

brand = BrandDNA.objects.filter(business_name__isnull=False).exclude(business_name='').first()
script = ReelScriptGenerator().generate(
    {'caption': 'Mira como transformamos este proyecto de principio a fin'}, brand,
)
print('hook_text:', script['hook_text'])
print('tag_cta:', script['tag_cta'])
video_url, poster_url = ReelGenerator(bucket_name='agente-cosmic-assets').generate(
    script, brand.primary_colors or ['#1a1a2e'], 'verify-hyperframes-reel',
    logo_url=brand.logo_url,
)
print('logo_url usado:', repr(brand.logo_url))
print('video_url:', video_url)
"
```

Expected: `video_url` no vacío.

- [ ] **Step 3: Verificar el MP4 resultante con `ffprobe`**

```bash
ffprobe -v error -show_entries stream=codec_type,width,height,r_frame_rate -show_entries format=duration -of default=noprint_wrappers=0 <archivo-descargado>.mp4
```

Expected: duración total EXACTA de 24.000000s (3+8+10+3), 1 solo stream de
video uniforme (misma resolución/fps en toda la duración — confirma que la
normalización de portada/contraportada funcionó igual que con los shots de
Imagen).

- [ ] **Step 4: Revisar visualmente**

Extraer frames de la portada (t≈1.5s), el cuerpo (ya verificado en Partes
anteriores), y la contraportada (t≈22.5s). Confirmar: hook con palabra
resaltada legible en la portada, CTA legible en la contraportada, logo
visible SI la marca de prueba tiene `logo_url` (o ausente sin dejar hueco
si no lo tiene), sin overflow de texto, transición de corte limpia hacia/desde
el cuerpo. Confirmar que el audio (narración) ya NO se corta antes del final.

- [ ] **Step 5: Confirmar en los logs que no hubo errores**

```bash
docker compose logs backend rqworker --since 10m | grep -i "hyperframes\|error\|traceback" | grep -v "INFO\|DeprecationWarning"
```

Expected: sin errores. Si el fallback se activó (portada/contraportada
fallaron), investigar con evidencia real antes de dar la tarea por cerrada
— no asumir que es un flake sin revisar el log de HyperFrames.

- [ ] **Step 6: Documentar el resultado**

Agregar una entrada a `hallazgos.txt` (mismo formato que HALLAZGO 70)
documentando la verificación real: duración exacta lograda, si el audio
dejó de cortarse, y si el fallback se activó alguna vez durante las
pruebas.
