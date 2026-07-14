# Experimento: overlay de texto de reels vía Playwright (hook + CTA) — Diseño

## Contexto

El pipeline de Reels de Agente Cosmic (`core/content_pipeline/generators/reel_generator.py`)
genera hook y CTA con `drawtext` nativo de ffmpeg desde el 2026-07-13
(`9f3c57d`), tras eliminar por completo un overlay previo vía Playwright+HTML
que se desbordaba del cuadro en producción real de forma no reproducible en
pruebas aisladas, pese a 10+ mitigaciones (fuente local, fuente embebida
base64, `position:absolute`, reflow forzado, verificación de desborde vía
`scrollWidth`/`clientWidth` con reintento). Ver `hallazgos.txt` HALLAZGO 53.

Después de esa migración se encontró una segunda causa raíz, independiente de
Playwright: `veo-3.0-fast-generate-001` entrega video real de 720×1280, no
1080×1920 como asumía todo el sizing del overlay (HALLAZGO 54, `8a07c2b`) —
ya corregido vía `_probe_video_width()` + escalado proporcional. Es probable
que ambas causas estuvieran confundidas entre sí durante el debugging
original.

Anuar pidió evaluar en una sesión futura si Playwright, ahora con el fix de
escala ya aplicado, da mejor calidad tipográfica/visual (kerning, contorno,
resaltado de palabra) que `drawtext` — sabiendo que el problema de métricas
de Chromium bajo carga (documentado en el comentario de
`reel_generator.py:28-34`) es una causa aparte que este experimento no
garantiza haber resuelto, solo permite volver a medir.

## Decisiones de diseño (ya validadas con Anuar)

- **Criterio de éxito**: calidad visual claramente superior a `drawtext` en
  reels reales — no solo "no se desborda". Si se ve igual o marginalmente
  mejor, no vale la pena la fragilidad adicional.
- **Alcance**: solo hook + CTA. Los subtítulos siguen siempre en `drawtext`
  sin cambios — son texto corto/simple donde la tipografía real aporta menos.
- **Método de comparación**: feature flag en el pipeline real (no un script
  de comparación aparte), para probar bajo la condición exacta que hacía
  fallar a Playwright la primera vez: el proceso corriendo varios minutos
  junto a Veo/Lyria/TTS antes de renderizar el overlay.
- **Alcance del flag**: global — todos los reels (testers reales incluidos)
  usan el engine activo mientras se evalúa. Viable porque el fallback
  automático (ver abajo) garantiza que ningún reel sale roto.
- **Manejo de fallo**: si Playwright se desborda tras 1 reintento con browser
  fresco, ese elemento específico (hook o CTA) se genera con `drawtext` en su
  lugar — fallback por elemento, no por reel completo. Se acepta el trade-off
  de que un mismo reel podría mezclar hook en Playwright y CTA en drawtext (o
  viceversa) si solo uno de los dos falla — es infrecuente y, si aparece, es
  en sí mismo una señal útil sobre la confiabilidad de Playwright bajo carga.
- **Dato objetivo a recolectar**: tasa de fallback de Playwright a drawtext
  bajo carga real de producción — el dato que nunca se pudo confirmar la
  primera vez porque Playwright se eliminó antes de instrumentarlo. Es
  diagnóstico, no el criterio de éxito (el criterio es calidad visual,
  juzgada por Anuar viendo reels reales).

## Arquitectura

Un nuevo setting `REEL_TEXT_OVERLAY_ENGINE` (env var, default `'drawtext'`,
valor experimental `'playwright'`) controla qué motor usa `ReelGenerator`
para hook y CTA dentro de `_assemble_reel`. Cuando el engine es
`'playwright'`, cada elemento pasa por: render Playwright → verificación de
desborde (`scrollWidth` vs `clientWidth`, mismo método que la última versión
pre-migración) → si desborda, 1 reintento con browser fresco → si sigue
desbordando, se devuelve `None` y `_assemble_reel` genera **ese elemento**
con las funciones `drawtext` existentes (`_build_hook_filter_parts` /
`_build_cta_filter_parts`), sin duplicar esa lógica. Cada fallback incrementa
una métrica nueva. El paso de subtítulos no cambia.

## Componentes

### `saas_chatbot/settings.py`

Agregar junto a `VERTEX_VIDEO_MODEL`/`VERTEX_MUSIC_MODEL` (línea ~174):

```python
REEL_TEXT_OVERLAY_ENGINE = os.environ.get('REEL_TEXT_OVERLAY_ENGINE', 'drawtext')
```

### Templates restaurados

Restaurar desde el último commit antes de su borrado (`git show 9f3c57d^:core/content_pipeline/templates/content_pipeline/reel_hook.html` y `reel_cta.html`,
commit `9f3c57d^`) — ya incluyen todas las mitigaciones probadas (fuente vía
`@font-face` con `{{font_path}}` como data URI, `position:absolute` con
dimensiones fijas 1080×1920, `overflow-wrap: break-word`). **No** restaurar
la versión más antigua de `349490c` (`@import` de Google Fonts por red — ya
descartada por lenta/no confiable bajo carga):

- `core/content_pipeline/templates/content_pipeline/reel_hook.html`
- `core/content_pipeline/templates/content_pipeline/reel_cta.html`

### `core/content_pipeline/generators/reel_generator.py`

**Nuevas constantes**, junto a `_DRAWTEXT_FONT_PATH`:

```python
_TEMPLATE_MAP = {'hook': 'reel_hook.html', 'cta': 'reel_cta.html'}
_VIDEO_HEIGHT = 1920  # alto fijo del canvas de Playwright (viewport 1080x1920)


def _load_font_data_uri() -> str:
    with open(_DRAWTEXT_FONT_PATH, 'rb') as f:
        encoded = base64.b64encode(f.read()).decode('ascii')
    return f'data:font/ttf;base64,{encoded}'


_OVERLAY_FONT_DATA_URI = _load_font_data_uri()
```

(Reutiliza `_DRAWTEXT_FONT_PATH`, que ya apunta a
`static/content_pipeline/fonts/Poppins-Bold.ttf` — no se agrega ninguna
fuente nueva.)

**Nuevo método** en `ReelGenerator`, restaurado de la última versión
pre-migración y adaptado para devolver `None` en vez de aceptar un resultado
desbordado:

```python
def _render_text_overlay_playwright(self, text: str, highlight_word: str,
                                     style: str, primary_color: str,
                                     cta_text: str = '') -> bytes | None:
    from playwright.sync_api import sync_playwright
    import html as _html
    import re

    try:
        template_path = os.path.normpath(os.path.join(
            os.path.dirname(__file__), '..', 'templates', 'content_pipeline',
            _TEMPLATE_MAP[style],
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

Import nuevo: `from core.shared.metrics_utils import record_playwright_overlay_fallback`
(agregar a la tupla de imports ya existente de `metrics_utils` en la cabecera
del archivo). El import de `playwright.sync_api` va dentro del método (no a
nivel de módulo) para que el flag en `'drawtext'` (el default, y el único
usado en tests que no mockean Playwright) no requiera que el paquete
`playwright` ni sus browsers estén instalados en cada entorno. El
`try/except` envuelve todo el cuerpo (incluidas las 2 iteraciones del
reintento) para que un fallo duro (Chromium no arranca, timeout) caiga por el
mismo camino de `record_playwright_overlay_fallback` + `return None` que un
desborde confirmado, sin lanzar la excepción hacia `_assemble_reel`.

**Cambios en `_assemble_reel`** — reemplazar el bloque que construye
`filter_parts`/`cta_parts` (líneas 369-376 actuales) por:

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

`scaled_w`/`scaled_h` escalan el PNG (siempre renderizado a 1080×1920 fijo
por el viewport de Playwright) al ancho real del video detectado por
`_probe_video_width()`, calculados en Python igual que ya se hace para
`fontsize`/`box_borderw` en `_build_hook_filter_parts` — mismo mecanismo de
escalado, para que el fix de HALLAZGO 54 aplique también a Playwright. Como
el PNG se renderiza con `omit_background=True` (fondo transparente), el
`scale` de ffmpeg preserva el canal alfa sin flags adicionales. `_write_tmp_png(tmp, name, data)`
es un helper de una línea (`open(path,'wb').write(data); return path`) para
no repetir el patrón ya usado para `clip_paths`. El comando `subprocess.run`
final que invoca ffmpeg debe anteponer `extra_inputs` a la lista de
argumentos (después del `-i concat_path` inicial) para que los índices `1:v`,
`2:v` de los filtros correspondan.

### `core/shared/metrics_utils.py`

Nueva función, mismo patrón que `record_lyria_generation()`:

```python
def record_playwright_overlay_fallback(element: str):
    _redis_inc(f'reel_playwright_fallback_{element}_total')
```

## Manejo de errores

Si Playwright lanza una excepción dura (Chromium no arranca, timeout, etc.)
en vez de detectar un desborde, se captura con `try/except` alrededor de todo
`_render_text_overlay_playwright`, se loguea como warning, se registra el
mismo `record_playwright_overlay_fallback(style)` y se devuelve `None` — el
mismo camino que un desborde confirmado. El flag es aditivo: activarlo nunca
puede hacer fallar un reel que hoy funciona con `drawtext`.

## Testing

- Tests de `_render_text_overlay_playwright` mockeando `sync_playwright`
  (adaptar los que existían en `f766dbc`/`9f3c57d^`): caso feliz (sin
  desborde, devuelve PNG del primer intento), desborde en el primer intento
  con éxito en el reintento, desborde en ambos intentos (devuelve `None` +
  verifica llamada a `record_playwright_overlay_fallback`), excepción dura
  (devuelve `None` + misma métrica).
- Test de `_assemble_reel` con `REEL_TEXT_OVERLAY_ENGINE='drawtext'`
  (default): confirma que el comportamiento actual no cambia — mismo
  `filter_complex` que hoy, sin inputs PNG extra.
- Test de `_assemble_reel` con `REEL_TEXT_OVERLAY_ENGINE='playwright'`
  mockeando `_render_text_overlay_playwright`: un caso donde ambos (hook,
  CTA) devuelven PNG (verifica los `-i` extra y los filtros `overlay`/`scale2ref`
  en el `subprocess.run`), y un caso donde uno devuelve `None` (verifica que
  ese elemento cae al filtro `drawtext` correspondiente mientras el otro usa
  overlay de PNG) — cubre el trade-off de mezcla de engines validado con
  Anuar.
- Sin llamadas reales a Playwright ni a APIs en la suite (mocks siempre).
  Como ya es el patrón establecido en este pipeline, después de implementar
  se activa el flag en `'playwright'` de forma global en el entorno de
  producción real (Veo/Lyria/TTS corriendo, la condición que originalmente
  hacía fallar a Playwright) y se generan varios reels reales de testers
  para: (a) que Anuar juzgue la calidad visual contra reels ya generados con
  `drawtext`, y (b) leer `record_playwright_overlay_fallback` para conocer la
  tasa de fallo real bajo carga — ninguna prueba con mocks detectó los bugs
  reales anteriores de este pipeline.
