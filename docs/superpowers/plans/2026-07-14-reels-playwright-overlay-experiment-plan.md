# Experimento: overlay Playwright para hook/CTA de reels — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reintroducir Playwright como motor alternativo (detrás de un feature flag) para el overlay de texto de hook/CTA en reels, con fallback automático por elemento a `drawtext` si Playwright se desborda o falla, y una métrica para medir su tasa de fallo bajo carga real de producción.

**Architecture:** Un setting `REEL_TEXT_OVERLAY_ENGINE` controla qué motor usa `ReelGenerator._assemble_reel` para hook y CTA. En modo `'playwright'`, un nuevo método `_render_text_overlay_playwright` renderiza cada elemento a PNG vía Chromium headless, verifica desborde (`scrollWidth` vs `clientWidth`), reintenta una vez con browser fresco, y devuelve `None` (con métrica) si sigue fallando o lanza excepción. `_assemble_reel` compone cada PNG no-`None` con el filtro `overlay` de ffmpeg (escalado al ancho real de Veo); cualquier elemento que devuelva `None` sigue usando las funciones `drawtext` ya existentes. Subtítulos no cambian.

**Tech Stack:** Django, ffmpeg (subprocess), Playwright (ya es dependencia del proyecto, usado por `image_generator.py`), Redis (contador de métricas vía `django_rq`), pytest + `django.test.override_settings`.

## Global Constraints

- Setting `REEL_TEXT_OVERLAY_ENGINE`: default `'drawtext'`, valor experimental `'playwright'` — nunca cambia el comportamiento de producción a menos que se active explícitamente.
- Alcance: **solo hook + CTA**. Los subtítulos siguen siempre en `drawtext`, sin tocar `SubtitleGenerator` ni el bucle de subtítulos en `_assemble_reel`.
- Fallback **por elemento**, no por reel completo: hook y CTA se deciden independientemente.
- Tras 1 reintento con browser fresco (2 intentos totales), si el elemento sigue desbordado o Playwright lanza una excepción, se registra `record_playwright_overlay_fallback(element)` y se usa `drawtext` para ese elemento.
- Templates a restaurar: la versión del commit `9f3c57d^` (última antes del borrado — usa `@font-face` con data URI base64, `position:absolute` con dimensiones fijas). **No** la versión más antigua de `349490c` (`@import` de Google Fonts por red, ya descartada).
- Playwright ya es una dependencia dura instalada del proyecto (`requirements.txt`, ambos Dockerfiles instalan Chromium; `image_generator.py` ya lo usa sin condición para cada imagen de post) — el import va a nivel de módulo en `reel_generator.py`, igual que en `image_generator.py`, no dentro de la función (no hay entornos reales del proyecto sin Playwright instalado que proteger).
- El flag aplica globalmente (todos los reels) cuando se active — el fallback automático es lo que garantiza que ningún reel salga roto mientras se evalúa.
- Fuente reutilizada de `_DRAWTEXT_FONT_PATH` (`static/content_pipeline/fonts/Poppins-Bold.ttf`) — no se agrega ninguna fuente nueva.

---

### Task 1: Setting del flag + métrica de fallback

**Files:**
- Modify: `saas_chatbot/settings.py:176` (agregar línea después de `VERTEX_TTS_MODEL`)
- Modify: `core/shared/metrics_utils.py` (agregar función después de `record_stt_call`, línea 138)
- Test: `core/shared/tests/test_metrics.py`

**Interfaces:**
- Produces: `record_playwright_overlay_fallback(element: str) -> None` en `core/shared/metrics_utils.py` — consumida por Task 2. `settings.REEL_TEXT_OVERLAY_ENGINE: str` — consumida por Task 3.

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `core/shared/tests/test_metrics.py`:

```python
class TestRecordPlaywrightOverlayFallback:
    def test_records_fallback_for_hook(self):
        from core.shared.metrics_utils import record_playwright_overlay_fallback
        increments = {}

        def fake_redis_inc(key, amount=1.0):
            increments[key] = increments.get(key, 0) + amount

        with patch('core.shared.metrics_utils._redis_inc', side_effect=fake_redis_inc):
            record_playwright_overlay_fallback('hook')

        assert increments.get('reel_playwright_fallback_hook_total', 0) == 1

    def test_records_fallback_for_cta(self):
        from core.shared.metrics_utils import record_playwright_overlay_fallback
        increments = {}

        def fake_redis_inc(key, amount=1.0):
            increments[key] = increments.get(key, 0) + amount

        with patch('core.shared.metrics_utils._redis_inc', side_effect=fake_redis_inc):
            record_playwright_overlay_fallback('cta')

        assert increments.get('reel_playwright_fallback_cta_total', 0) == 1
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `docker compose exec -T backend python -m pytest core/shared/tests/test_metrics.py::TestRecordPlaywrightOverlayFallback -v`
Expected: FAIL con `ImportError: cannot import name 'record_playwright_overlay_fallback'`

- [ ] **Step 3: Implementar `record_playwright_overlay_fallback`**

En `core/shared/metrics_utils.py`, agregar inmediatamente después de `record_stt_call` (después de la línea 138, antes de `def _classify_error`):

```python
def record_playwright_overlay_fallback(element: str):
    """Registra que un elemento (hook/cta) cayo de Playwright a drawtext — mide la tasa real de fallo bajo carga de produccion durante el experimento de REEL_TEXT_OVERLAY_ENGINE."""
    _redis_inc(f'reel_playwright_fallback_{element}_total')
```

- [ ] **Step 4: Correr el test y verificar que pasa**

Run: `docker compose exec -T backend python -m pytest core/shared/tests/test_metrics.py::TestRecordPlaywrightOverlayFallback -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Agregar el setting del flag**

En `saas_chatbot/settings.py`, agregar inmediatamente después de la línea 176 (`VERTEX_TTS_MODEL = ...`):

```python
REEL_TEXT_OVERLAY_ENGINE = get_env('REEL_TEXT_OVERLAY_ENGINE', default='drawtext')
```

- [ ] **Step 6: Verificar que el setting carga sin romper Django**

Run: `docker compose exec -T backend python manage.py shell -c "from django.conf import settings; print(settings.REEL_TEXT_OVERLAY_ENGINE)"`
Expected: imprime `drawtext`

- [ ] **Step 7: Commit**

```bash
git add saas_chatbot/settings.py core/shared/metrics_utils.py core/shared/tests/test_metrics.py
git commit -m "feat(reels): flag REEL_TEXT_OVERLAY_ENGINE + metrica de fallback de Playwright"
```

---

### Task 2: Templates restaurados + `_render_text_overlay_playwright`

**Files:**
- Create: `core/content_pipeline/templates/content_pipeline/reel_hook.html`
- Create: `core/content_pipeline/templates/content_pipeline/reel_cta.html`
- Modify: `core/content_pipeline/generators/reel_generator.py`
- Test: `core/content_pipeline/tests/test_reel_generator.py`

**Interfaces:**
- Consumes: `record_playwright_overlay_fallback(element: str)` de `core/shared/metrics_utils.py` (Task 1).
- Produces: `ReelGenerator._render_text_overlay_playwright(self, text: str, highlight_word: str, style: str, primary_color: str, cta_text: str = '') -> bytes | None` — consumida por Task 3. `style` es `'hook'` o `'cta'`.

- [ ] **Step 1: Restaurar `reel_hook.html`**

Crear `core/content_pipeline/templates/content_pipeline/reel_hook.html`:

```html
<!DOCTYPE html>
<html>
<head>
<style>
  @font-face {
    font-family: 'Poppins'; font-weight: 900; src: url('{{font_path}}') format('truetype');
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 1080px; height: 1920px; background: transparent; overflow: hidden; }
  .wrap {
    position: absolute; top: 0; left: 0; width: 1080px; height: 1920px;
    display: flex; flex-direction: column;
    align-items: center; justify-content: flex-start; padding-top: 220px;
  }
  .hook {
    font-family: 'Poppins', sans-serif; font-weight: 900; font-size: 64px;
    color: #ffffff; text-align: center; line-height: 1.2; width: 760px; min-width: 0;
    overflow-wrap: break-word; word-break: break-word;
    text-shadow: 0 4px 24px rgba(0,0,0,0.55), 0 2px 6px rgba(0,0,0,0.7);
  }
  .highlight {
    position: relative; display: inline-block; color: #1a1a2e; padding: 4px 18px;
  }
  .highlight::before {
    content: ''; position: absolute; inset: 6px -6px; background: {{primary_color}};
    border-radius: 14px; transform: rotate(-1.5deg); z-index: -1;
    box-shadow: 0 6px 18px rgba(0,0,0,0.35);
  }
</style>
</head>
<body>
  <div class="wrap">
    <div class="hook">{{hook_html}}</div>
  </div>
</body>
</html>
```

- [ ] **Step 2: Restaurar `reel_cta.html`**

Crear `core/content_pipeline/templates/content_pipeline/reel_cta.html`:

```html
<!DOCTYPE html>
<html>
<head>
<style>
  @font-face {
    font-family: 'Poppins'; font-weight: 900; src: url('{{font_path}}') format('truetype');
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 1080px; height: 1920px; background: transparent; overflow: hidden; }
  .wrap {
    position: absolute; top: 0; left: 0; width: 1080px; height: 1920px;
    display: flex; flex-direction: column;
    align-items: center; justify-content: center;
  }
  .cta {
    position: relative; font-family: 'Poppins', sans-serif; font-weight: 900; font-size: 54px;
    color: #1a1a2e; text-align: center; line-height: 1.2; padding: 14px 44px;
    width: 760px; min-width: 0; overflow-wrap: break-word; word-break: break-word;
  }
  .cta::before {
    content: ''; position: absolute; inset: 0; background: {{primary_color}};
    border-radius: 20px; z-index: -1;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
  }
</style>
</head>
<body>
  <div class="wrap">
    <div class="cta">{{cta_text}}</div>
  </div>
</body>
</html>
```

Nota: `justify-content: center` en `.wrap` de `reel_cta.html` ya refleja el reposicionamiento al centro hecho en `d1da333` (para no chocar con subtítulos) — es la versión correcta a restaurar, no la original de `349490c` que tenía `justify-content: flex-end`.

- [ ] **Step 3: Escribir los tests que fallan**

Agregar al final de `core/content_pipeline/tests/test_reel_generator.py`:

```python
class TestRenderTextOverlayPlaywright:
    def _make_mock_playwright(self, screenshot_bytes: bytes, evaluate_side_effect: list):
        mock_page = MagicMock()
        mock_page.screenshot.return_value = screenshot_bytes
        mock_page.evaluate.side_effect = evaluate_side_effect
        mock_browser = MagicMock()
        mock_browser.new_page.return_value = mock_page
        mock_pw_ctx = MagicMock()
        mock_pw_ctx.chromium.launch.return_value = mock_browser
        mock_pw = MagicMock()
        mock_pw.__enter__ = MagicMock(return_value=mock_pw_ctx)
        mock_pw.__exit__ = MagicMock(return_value=False)
        return mock_pw, mock_page

    def test_returns_screenshot_when_no_overflow(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_png = b'fake-hook-png'
        # 3 evaluate() por intento: fonts.ready, offsetHeight, scrollWidth-clientWidth.
        # Solo el 3er valor importa (overflow_px); 0 = sin desborde.
        mock_pw, mock_page = self._make_mock_playwright(fake_png, [None, None, 0])

        with patch('core.content_pipeline.generators.reel_generator.sync_playwright', return_value=mock_pw), \
             patch('core.content_pipeline.generators.reel_generator.record_playwright_overlay_fallback') as mock_metric:
            result = gen._render_text_overlay_playwright(
                'Descubre algo nuevo', 'nuevo', 'hook', '#002951',
            )

        assert result == fake_png
        mock_metric.assert_not_called()
        html_arg = mock_page.set_content.call_args[0][0]
        assert '<span class="highlight">nuevo</span>' in html_arg
        assert '#002951' in html_arg
        assert '{{font_path}}' not in html_arg

    def test_retries_once_and_succeeds_on_second_attempt(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_png = b'fake-hook-png'
        # 1er intento: overflow_px=10 (desborda). 2do intento: overflow_px=0 (ok).
        mock_pw, mock_page = self._make_mock_playwright(fake_png, [None, None, 10, None, None, 0])

        with patch('core.content_pipeline.generators.reel_generator.sync_playwright', return_value=mock_pw), \
             patch('core.content_pipeline.generators.reel_generator.record_playwright_overlay_fallback') as mock_metric:
            result = gen._render_text_overlay_playwright(
                'Descubre algo nuevo', 'nuevo', 'hook', '#002951',
            )

        assert result == fake_png
        mock_metric.assert_not_called()
        assert mock_pw.__enter__.call_count == 2

    def test_returns_none_and_records_fallback_after_second_overflow(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_png = b'fake-hook-png'
        # Ambos intentos desbordan (overflow_px=10 las 2 veces).
        mock_pw, mock_page = self._make_mock_playwright(fake_png, [None, None, 10, None, None, 10])

        with patch('core.content_pipeline.generators.reel_generator.sync_playwright', return_value=mock_pw), \
             patch('core.content_pipeline.generators.reel_generator.record_playwright_overlay_fallback') as mock_metric:
            result = gen._render_text_overlay_playwright(
                'Descubre algo nuevo', 'nuevo', 'hook', '#002951',
            )

        assert result is None
        mock_metric.assert_called_once_with('hook')

    def test_returns_none_and_records_fallback_on_exception(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')

        with patch('core.content_pipeline.generators.reel_generator.sync_playwright',
                    side_effect=Exception('chromium crashed')), \
             patch('core.content_pipeline.generators.reel_generator.record_playwright_overlay_fallback') as mock_metric:
            result = gen._render_text_overlay_playwright(
                '', '', 'cta', '#002951', cta_text='Compra ahora',
            )

        assert result is None
        mock_metric.assert_called_once_with('cta')

    def test_cta_style_injects_cta_text_not_hook(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_png = b'fake-cta-png'
        mock_pw, mock_page = self._make_mock_playwright(fake_png, [None, None, 0])

        with patch('core.content_pipeline.generators.reel_generator.sync_playwright', return_value=mock_pw), \
             patch('core.content_pipeline.generators.reel_generator.record_playwright_overlay_fallback'):
            result = gen._render_text_overlay_playwright(
                '', '', 'cta', '#002951', cta_text='Compra ahora',
            )

        assert result == fake_png
        html_arg = mock_page.set_content.call_args[0][0]
        assert 'Compra ahora' in html_arg
        assert 'class="hook"' not in html_arg
```

- [ ] **Step 4: Correr los tests y verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py::TestRenderTextOverlayPlaywright -v`
Expected: FAIL con `AttributeError: 'ReelGenerator' object has no attribute '_render_text_overlay_playwright'`

- [ ] **Step 5: Implementar `_render_text_overlay_playwright`**

En `core/content_pipeline/generators/reel_generator.py`, modificar el bloque de imports (líneas 1-18) — agregar `html as _html` y `re` a las importaciones estándar, `sync_playwright` a nivel de módulo, y `record_playwright_overlay_fallback` a la tupla ya importada de `metrics_utils`:

```python
import base64
import html as _html
import logging
import os
import re
import subprocess
import tempfile
import time
import google.genai as genai
from google.genai import types
from google.cloud import storage
from django.conf import settings
from playwright.sync_api import sync_playwright
from PIL import ImageFont
from core.shared.metrics import GCS_OPERATIONS
from core.shared.metrics_utils import (
    track_external_api, record_tokens, record_veo_generation,
    record_lyria_generation, record_tts_generation,
    record_playwright_overlay_fallback,
)
from core.shared.rate_limiter import call_with_429_retry
from core.content_pipeline.generators.subtitle_generator import SubtitleGenerator
```

Agregar estas constantes inmediatamente después de `_DRAWTEXT_FONT_PATH` (después de la línea 37, antes de `_HOOK_FONTSIZE = 64`):

```python
_VIDEO_HEIGHT = 1920  # alto fijo del canvas de Playwright (viewport 1080x1920)

_OVERLAY_TEMPLATE_MAP = {'hook': 'reel_hook.html', 'cta': 'reel_cta.html'}


def _load_font_data_uri() -> str:
    with open(_DRAWTEXT_FONT_PATH, 'rb') as f:
        encoded = base64.b64encode(f.read()).decode('ascii')
    return f'data:font/ttf;base64,{encoded}'


_OVERLAY_FONT_DATA_URI = _load_font_data_uri()
```

Agregar este método a la clase `ReelGenerator`, inmediatamente antes de `_generate_video_clips` (antes de la línea 226 actual, después del docstring/comentario `_VEO_SAFE_CONSTRAINTS`):

```python
    def _render_text_overlay_playwright(self, text: str, highlight_word: str,
                                         style: str, primary_color: str,
                                         cta_text: str = '') -> bytes | None:
        try:
            template_path = os.path.normpath(os.path.join(
                os.path.dirname(__file__), '..', 'templates', 'content_pipeline',
                _OVERLAY_TEMPLATE_MAP[style],
            ))
            with open(template_path) as f:
                html = f.read()
            html = html.replace('{{primary_color}}', primary_color)
            html = html.replace('{{font_path}}', _OVERLAY_FONT_DATA_URI)

            if style == 'hook':
                escaped = _html.escape(text)
                if highlight_word:
                    escaped_word = _html.escape(highlight_word)
                    pattern = re.compile(re.escape(escaped_word), re.IGNORECASE)
                    escaped = pattern.sub(f'<span class="highlight">{escaped_word}</span>', escaped, count=1)
                html = html.replace('{{hook_html}}', escaped)
            else:
                html = html.replace('{{cta_text}}', _html.escape(cta_text))

            selector = '.hook' if style == 'hook' else '.cta'
            for attempt in range(2):
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(
                        headless=True,
                        args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
                    )
                    page = browser.new_page(viewport={'width': 1080, 'height': 1920})
                    page.set_content(html, wait_until='load')
                    page.evaluate('document.fonts.ready')
                    page.evaluate('document.body.offsetHeight')
                    page.wait_for_timeout(300)
                    overflow_px = page.evaluate(
                        f"() => {{ const el = document.querySelector('{selector}'); "
                        f"return el.scrollWidth - el.clientWidth; }}"
                    )
                    overflows = overflow_px is not None and overflow_px > 2
                    png_bytes = page.screenshot(omit_background=True)
                    browser.close()
                if not overflows:
                    return png_bytes
                logger.warning(f"Overlay Playwright '{style}' se salio del cuadro (intento {attempt + 1})")
        except Exception as e:
            logger.warning(f"Overlay Playwright '{style}' fallo con excepcion (cae a drawtext): {e}")

        record_playwright_overlay_fallback(style)
        return None
```

- [ ] **Step 6: Correr los tests y verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py::TestRenderTextOverlayPlaywright -v`
Expected: PASS (5 passed)

- [ ] **Step 7: Correr toda la suite de reels para verificar que nada se rompió**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -v`
Expected: todos los tests existentes siguen en PASS (el método nuevo no se invoca desde ningún lado todavía)

- [ ] **Step 8: Commit**

```bash
git add core/content_pipeline/templates/content_pipeline/reel_hook.html \
        core/content_pipeline/templates/content_pipeline/reel_cta.html \
        core/content_pipeline/generators/reel_generator.py \
        core/content_pipeline/tests/test_reel_generator.py
git commit -m "feat(reels): restaurar overlay de texto via Playwright con fallback a None si se desborda"
```

---

### Task 3: Wiring del flag en `_assemble_reel`

**Files:**
- Modify: `core/content_pipeline/generators/reel_generator.py`
- Test: `core/content_pipeline/tests/test_reel_generator.py`

**Interfaces:**
- Consumes: `ReelGenerator._render_text_overlay_playwright(...)` (Task 2), `settings.REEL_TEXT_OVERLAY_ENGINE` (Task 1), `_build_hook_filter_parts`/`_build_cta_filter_parts`/`_VIDEO_WIDTH`/`_VIDEO_HEIGHT`/`_HOOK_END_SECONDS` (ya existentes).
- Produces: comportamiento final de `_assemble_reel` — no expone nuevas funciones públicas, es el punto de integración final del experimento.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `core/content_pipeline/tests/test_reel_generator.py` (después de la clase `TestAssembleReel` existente, antes de `TestExtractPosterFrame`):

```python
class TestAssembleReelPlaywrightEngine:
    @override_settings(REEL_TEXT_OVERLAY_ENGINE='drawtext')
    def test_drawtext_engine_never_calls_playwright_render(self, tmp_path):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=_fake_ffmpeg_run(fake_output)), \
             patch.object(gen, '_render_text_overlay_playwright') as mock_render:
            gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                script=_FAKE_SCRIPT_FOR_ASSEMBLE, colors=['#1a1a2e'],
            )
        mock_render.assert_not_called()

    @override_settings(REEL_TEXT_OVERLAY_ENGINE='playwright')
    def test_playwright_engine_composes_both_pngs_via_overlay(self, tmp_path):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=_fake_ffmpeg_run(fake_output)) as mock_run, \
             patch.object(gen, '_render_text_overlay_playwright',
                           side_effect=[b'hook-png-bytes', b'cta-png-bytes']) as mock_render:
            gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                script=_FAKE_SCRIPT_FOR_ASSEMBLE, colors=['#1a1a2e'],
            )

        assert mock_render.call_count == 2
        hook_call, cta_call = mock_render.call_args_list
        assert hook_call.args == ('Descubre algo nuevo', 'nuevo', 'hook', '#1a1a2e')
        assert cta_call.args == ('', '', 'cta', '#1a1a2e')
        assert cta_call.kwargs == {'cta_text': 'Compra ahora'}

        overlay_cmd = mock_run.call_args_list[2].args[0]
        assert overlay_cmd.count('-i') == 3  # concat + hook.png + cta.png
        filter_complex = overlay_cmd[overlay_cmd.index('-filter_complex') + 1]
        assert filter_complex.count('overlay=0:0') == 2
        assert "text='nuevo'" not in filter_complex  # hook via PNG, no drawtext
        assert "text='Compra ahora'" not in filter_complex  # cta via PNG, no drawtext
        map_idx = overlay_cmd.index('-map')
        assert overlay_cmd[map_idx + 1] == '[ctaout]'

    @override_settings(REEL_TEXT_OVERLAY_ENGINE='playwright')
    def test_playwright_engine_falls_back_to_drawtext_per_element(self, tmp_path):
        # Hook se desbordo en Playwright (devuelve None) -> cae a drawtext.
        # CTA si funciono en Playwright -> se compone via overlay de PNG.
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=_fake_ffmpeg_run(fake_output)) as mock_run, \
             patch.object(gen, '_render_text_overlay_playwright',
                           side_effect=[None, b'cta-png-bytes']):
            gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                script=_FAKE_SCRIPT_FOR_ASSEMBLE, colors=['#1a1a2e'],
            )

        overlay_cmd = mock_run.call_args_list[2].args[0]
        assert overlay_cmd.count('-i') == 2  # concat + cta.png (hook no genero PNG)
        filter_complex = overlay_cmd[overlay_cmd.index('-filter_complex') + 1]
        assert "text='nuevo'" in filter_complex  # hook cayo a drawtext
        assert filter_complex.count('overlay=0:0') == 1  # solo cta via PNG
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py::TestAssembleReelPlaywrightEngine -v`
Expected: FAIL — `test_drawtext_engine_never_calls_playwright_render` puede pasar de casualidad (drawtext ya es el default y el metodo nunca se llama), pero `test_playwright_engine_composes_both_pngs_via_overlay` y `test_playwright_engine_falls_back_to_drawtext_per_element` deben fallar porque `settings.REEL_TEXT_OVERLAY_ENGINE` aun no se lee en `_assemble_reel` — `mock_render.call_count == 0` en vez de `2`.

- [ ] **Step 3: Modificar `_assemble_reel`**

En `core/content_pipeline/generators/reel_generator.py`, dentro de `_assemble_reel`, reemplazar este bloque (líneas 369-376 actuales):

```python
            filter_parts, last_label = _build_hook_filter_parts(
                script['hook_text'], script['highlight_word'], primary_color, '0:v',
                video_width=video_width, scale=scale,
            )
            cta_parts, last_label = _build_cta_filter_parts(
                script['tag_cta'], primary_color, last_label, cta_start, duration, scale=scale,
            )
            filter_parts += cta_parts
```

por:

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

Luego, un poco más abajo en el mismo método, reemplazar la llamada a `subprocess.run` que construye `overlay_path` (la que usa `'-i', concat_path,` seguido de `'-filter_complex'`):

```python
            overlay_path = os.path.join(tmp, 'overlay.mp4')
            subprocess.run(
                ['ffmpeg', '-y', '-i', concat_path,
                 '-filter_complex', filter_complex,
                 '-map', f'[{last_label}]', '-t', str(duration), '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                 overlay_path],
                check=True, capture_output=True,
            )
```

por:

```python
            overlay_path = os.path.join(tmp, 'overlay.mp4')
            subprocess.run(
                ['ffmpeg', '-y', '-i', concat_path] + extra_inputs +
                ['-filter_complex', filter_complex,
                 '-map', f'[{last_label}]', '-t', str(duration), '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                 overlay_path],
                check=True, capture_output=True,
            )
```

Finalmente, agregar el helper `_write_tmp_png` a nivel de módulo, junto a `_hex_to_ffmpeg_color` (después de la línea 91 actual):

```python
def _write_tmp_png(tmp_dir: str, filename: str, data: bytes) -> str:
    path = os.path.join(tmp_dir, filename)
    with open(path, 'wb') as f:
        f.write(data)
    return path
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py::TestAssembleReelPlaywrightEngine -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Correr toda la suite del generador de reels**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -v`
Expected: todos los tests pasan, incluidos los existentes de `TestAssembleReel` (deben seguir pasando sin modificacion — el engine default sigue siendo `drawtext`)

- [ ] **Step 6: Recrear contenedores para que el flag y el codigo nuevo esten activos**

Run: `docker compose up -d --force-recreate --no-deps --scale rqworker=3 backend rqworker`

Nota: `DEBUG=False` cachea templates y codigo — sin este paso el flag `REEL_TEXT_OVERLAY_ENGINE` y el metodo nuevo no estaran disponibles en produccion aunque el commit ya exista.

- [ ] **Step 7: Commit**

```bash
git add core/content_pipeline/generators/reel_generator.py core/content_pipeline/tests/test_reel_generator.py
git commit -m "feat(reels): wiring del flag REEL_TEXT_OVERLAY_ENGINE en _assemble_reel"
```

---

## Verificación manual post-implementación (no automatizable en CI)

Después de que las 3 tareas estén commiteadas y los contenedores recreados, el criterio de éxito del experimento (calidad visual, no solo ausencia de desborde) requiere verificación humana con datos reales:

1. Activar el flag en el entorno real: `REEL_TEXT_OVERLAY_ENGINE=playwright` en las variables de entorno de `backend`/`rqworker`, luego `docker compose up -d --force-recreate --no-deps --scale rqworker=3 backend rqworker`.
2. Generar varios reels reales de testers (con Veo/Lyria/TTS corriendo, la condición exacta que hacía fallar a Playwright originalmente).
3. Anuar revisa los reels resultantes y juzga la calidad visual del hook/CTA contra reels ya generados con `drawtext`.
4. Leer el contador `reel_playwright_fallback_hook_total` / `reel_playwright_fallback_cta_total` en Redis (`docker compose exec -T backend python -c "import django_rq; r = django_rq.get_connection('default'); print(r.get('reel_playwright_fallback_hook_total'), r.get('reel_playwright_fallback_cta_total'))"`) para conocer la tasa de fallback real bajo carga — el dato que nunca se pudo confirmar la primera vez.
5. Decisión final: si la calidad es claramente superior, evaluar promover `'playwright'` a default; si no, revertir el flag a `'drawtext'` (el código queda igual de disponible para retomar más adelante).
