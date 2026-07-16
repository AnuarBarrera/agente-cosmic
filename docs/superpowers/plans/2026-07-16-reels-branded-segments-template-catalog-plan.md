# Catálogo de templates para portada/contraportada de reels — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar la portada/contraportada actual de los reels (fondo blanco plano + logo) por un catálogo de 3 templates deterministas con movimiento profesional (panel-wipe, kinetic-typography, dynamic-background), elegidos por IA según el guion, con tipografía compartida con las imágenes de esa semana.

**Architecture:** 3 pares de composiciones HTML/GSAP nuevas (portada+contraportada por template) reemplazan las 2 composiciones actuales. Un nuevo método `_choose_reel_template` (mismo patrón que `_choose_template_for_image`) elige el template una vez por reel. `_FONT_PRESETS`/`_choose_font_preset` se mueven de `image_generator.py` a un módulo compartido `core/shared/font_presets.py` para que reels e imágenes usen exactamente el mismo sistema y el mismo seed semanal. `logo_url` se elimina de la cadena `tasks.py`→`generate()`→`_generate_clips_with_branding()`→`_generate_branded_segment()`.

**Tech Stack:** HyperFrames (Node.js + GSAP + Chrome headless), Django, google-genai (Vertex AI/Gemini), pytest.

## Global Constraints

- Rutas a assets vendorizados dentro de las composiciones DEBEN ser relativas a la raíz del proyecto HyperFrames SIN `../` (ej. `node_modules/gsap/dist/gsap.min.js`, `assets/Poppins-Bold.ttf`). Con `../`, HyperFrames reescribe silenciosamente la ruta a un CDN externo (`cdn.jsdelivr.net`), rompiendo el objetivo de cero dependencia de red al renderizar.
- Ninguna composición debe usar `Math.random()` sin semilla, `repeat: -1`, relojes de render, ni animar `display`/`visibility` cruda (solo `opacity`/`transform`/colores/`gsap.autoAlpha`).
- El texto del hook/CTA debe ir en un contenedor de flujo de bloque normal con `max-width` fijo (NO `display:flex` en la fila de texto) — un contenedor flex sin `flex-wrap` no envuelve línea y el texto se desborda del canvas de 1080px a fuentes grandes (bug real confirmado visualmente en el brainstorm).
- Cada composición registra exactamente 1 timeline GSAP (`gsap.timeline({ paused: true })`) de forma síncrona, en `window.__timelines['<data-composition-id>']`.
- El root de cada composición necesita tamaño explícito (`data-width="1080" data-height="1920"`, `html,body{width:1080px;height:1920px}`) y `data-duration="3"`.

---

## Task 1: Sistema de fuentes compartido + fuentes vendorizadas

**Files:**
- Create: `core/shared/font_presets.py`
- Create: `core/shared/tests/test_font_presets.py`
- Modify: `core/content_pipeline/generators/image_generator.py:1-48`
- Create: `core/content_pipeline/hyperframes_reel/assets/PlayfairDisplay-Bold.ttf`
- Create: `core/content_pipeline/hyperframes_reel/assets/SpaceGrotesk-Bold.ttf`
- Create: `core/content_pipeline/hyperframes_reel/assets/BebasNeue-Regular.ttf`
- Create: `core/content_pipeline/hyperframes_reel/assets/DMSans-Bold.ttf`

**Interfaces:**
- Produces: `core.shared.font_presets.FONT_PRESETS: list[dict]` (cada dict tiene `font_family`/`font_import`), `core.shared.font_presets.choose_font_preset(seed: str) -> dict`. Tareas posteriores (Task 5) importan `choose_font_preset` directo desde `core.shared.font_presets`.

- [ ] **Step 1: Crear el módulo compartido**

Crea `core/shared/font_presets.py`:

```python
import hashlib
import random

# Tipografías reales via Google Fonts, compartidas entre las imagenes/carrusel
# (image_generator.py) y la portada/contraportada de reels (reel_generator.py)
# para que ambos usen exactamente el mismo catalogo y el mismo seed semanal.
FONT_PRESETS = [
    {'font_family': "'Poppins', sans-serif", 'font_import': 'Poppins:wght@400;600;700;900'},
    {'font_family': "'Playfair Display', serif", 'font_import': 'Playfair+Display:wght@400;600;700;900'},
    {'font_family': "'Space Grotesk', sans-serif", 'font_import': 'Space+Grotesk:wght@400;500;600;700'},
    {'font_family': "'Bebas Neue', sans-serif", 'font_import': 'Bebas+Neue'},
    {'font_family': "'DM Sans', sans-serif", 'font_import': 'DM+Sans:wght@400;500;700'},
]


def choose_font_preset(seed: str) -> dict:
    """Elige una fuente de forma determinista a partir de `seed` (el job_id del
    calendario) en vez de puramente al azar — asi las 7 imagenes de una misma
    semana Y la portada/contraportada del reel de esa semana usan la MISMA
    fuente (consistencia de marca), incluso si el usuario regenera un solo
    post despues (mismo seed => mismo preset)."""
    if not seed:
        return random.choice(FONT_PRESETS)
    digest = hashlib.sha256(seed.encode()).hexdigest()
    idx = int(digest, 16) % len(FONT_PRESETS)
    return FONT_PRESETS[idx]
```

- [ ] **Step 2: Escribir los tests del módulo nuevo**

Crea `core/shared/tests/test_font_presets.py`:

```python
class TestChooseFontPreset:
    def test_same_seed_always_returns_same_preset(self):
        from core.shared.font_presets import choose_font_preset
        first = choose_font_preset('job-123')
        second = choose_font_preset('job-123')
        assert first == second

    def test_empty_seed_does_not_raise(self):
        from core.shared.font_presets import choose_font_preset, FONT_PRESETS
        result = choose_font_preset('')
        assert result in FONT_PRESETS

    def test_different_seeds_can_return_different_presets(self):
        from core.shared.font_presets import choose_font_preset
        seeds = [f'job-{i}' for i in range(20)]
        results = {choose_font_preset(s)['font_family'] for s in seeds}
        assert len(results) > 1
```

- [ ] **Step 3: Correr los tests nuevos**

Run: `docker compose exec -T backend python -m pytest core/shared/tests/test_font_presets.py -v`
Expected: 3 passed.

- [ ] **Step 4: `image_generator.py` importa desde el módulo compartido en vez de definir localmente**

En `core/content_pipeline/generators/image_generator.py`, localiza el bloque (líneas ~26-48):

```python
# Tipografías reales via Google Fonts (antes: font-family: Arial hardcodeado en los
# 3 templates HTML). Solo varía la fuente — el color de acento/botón sigue viniendo
# de la paleta real de la marca (primary_color/button_color), no de estos presets.
_FONT_PRESETS = [
    {'font_family': "'Poppins', sans-serif", 'font_import': 'Poppins:wght@400;600;700;900'},
    {'font_family': "'Playfair Display', serif", 'font_import': 'Playfair+Display:wght@400;600;700;900'},
    {'font_family': "'Space Grotesk', sans-serif", 'font_import': 'Space+Grotesk:wght@400;500;600;700'},
    {'font_family': "'Bebas Neue', sans-serif", 'font_import': 'Bebas+Neue'},
    {'font_family': "'DM Sans', sans-serif", 'font_import': 'DM+Sans:wght@400;500;700'},
]


def _choose_font_preset(seed: str) -> dict:
    """Elige una fuente de forma determinista a partir de `seed` (el job_id del
    calendario) en vez de puramente al azar — así las 7 imagenes de una misma
    semana usan la MISMA fuente (consistencia de marca, ver H35), incluso si el
    usuario regenera un solo post despues (nueva instancia de ImageGenerator,
    pero mismo seed => mismo preset)."""
    if not seed:
        return random.choice(_FONT_PRESETS)
    digest = hashlib.sha256(seed.encode()).hexdigest()
    idx = int(digest, 16) % len(_FONT_PRESETS)
    return _FONT_PRESETS[idx]
```

Reemplázalo por (mismos nombres `_FONT_PRESETS`/`_choose_font_preset` re-exportados, para que el resto del archivo y sus tests existentes no cambien):

```python
# Tipografías reales via Google Fonts — definidas en core/shared/font_presets.py
# (compartido con la portada/contraportada de reels, ver reel_generator.py).
from core.shared.font_presets import FONT_PRESETS as _FONT_PRESETS, choose_font_preset as _choose_font_preset
```

Esta línea reemplaza TODO el bloque de arriba (la lista `_FONT_PRESETS` y la función `_choose_font_preset` completas). No queda ninguna definición local de `_FONT_PRESETS`/`_choose_font_preset` en `image_generator.py` — solo el import.

Verifica si `hashlib` y/o `random` quedaron sin otro uso en `image_generator.py` tras este cambio (`grep -n "hashlib\.\|random\." core/content_pipeline/generators/image_generator.py` — excluyendo la línea de import que acabas de borrar). Si `hashlib` ya no se usa en ningún otro lado del archivo, elimina su `import hashlib` del bloque de imports al inicio del archivo. Si `random` se sigue usando (ej. en `_choose_template_for_image` para el fallback `random.choice(self._TEMPLATES)`), déjalo.

- [ ] **Step 5: Correr la suite de image_generator para confirmar que nada se rompió**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_image_generator.py -v 2>&1 | tail -40`
Expected: todos los tests (incluidos `TestChooseFontPreset` y `test_injects_font_family_into_html`) siguen en PASS sin haberlos modificado — confirman que el re-export mantiene el comportamiento idéntico.

- [ ] **Step 6: Vendorizar las 4 fuentes faltantes**

Solo `Poppins-Bold.ttf` está vendorizado hoy en `core/content_pipeline/hyperframes_reel/assets/`. Descarga las 4 fuentes restantes desde Google Fonts (licencia SIL Open Font License — libres de redistribuir) usando el endpoint de descarga por familia:

```bash
mkdir -p /tmp/fonts_download && cd /tmp/fonts_download

curl -L -o playfair.zip "https://fonts.google.com/download?family=Playfair%20Display"
unzip -o playfair.zip -d playfair
# Busca el archivo estatico de mayor peso disponible (Black/900). Puede estar
# en la raiz del zip o en un subdirectorio static/. Ejemplo de busqueda:
find playfair -iname "*black*.ttf"

curl -L -o spacegrotesk.zip "https://fonts.google.com/download?family=Space%20Grotesk"
unzip -o spacegrotesk.zip -d spacegrotesk
find spacegrotesk -iname "*bold*.ttf"

curl -L -o bebasneue.zip "https://fonts.google.com/download?family=Bebas%20Neue"
unzip -o bebasneue.zip -d bebasneue
find bebasneue -iname "*regular*.ttf"

curl -L -o dmsans.zip "https://fonts.google.com/download?family=DM%20Sans"
unzip -o dmsans.zip -d dmsans
find dmsans -iname "*bold*.ttf"
```

Copia el archivo estático encontrado de cada familia (el de mayor peso/negrita disponible) al destino final con el nombre EXACTO indicado (si la familia es una variable font y el `find` no encuentra un archivo estático discreto, busca dentro de un subdirectorio `static/`):

```bash
cp /tmp/fonts_download/playfair/<archivo-black-encontrado>.ttf \
   /home/anuarbarrera/agente-cosmic/core/content_pipeline/hyperframes_reel/assets/PlayfairDisplay-Bold.ttf
cp /tmp/fonts_download/spacegrotesk/<archivo-bold-encontrado>.ttf \
   /home/anuarbarrera/agente-cosmic/core/content_pipeline/hyperframes_reel/assets/SpaceGrotesk-Bold.ttf
cp /tmp/fonts_download/bebasneue/<archivo-regular-encontrado>.ttf \
   /home/anuarbarrera/agente-cosmic/core/content_pipeline/hyperframes_reel/assets/BebasNeue-Regular.ttf
cp /tmp/fonts_download/dmsans/<archivo-bold-encontrado>.ttf \
   /home/anuarbarrera/agente-cosmic/core/content_pipeline/hyperframes_reel/assets/DMSans-Bold.ttf
```

- [ ] **Step 7: Verificar que los 4 archivos son fuentes TrueType válidas**

Run:
```bash
file core/content_pipeline/hyperframes_reel/assets/PlayfairDisplay-Bold.ttf \
     core/content_pipeline/hyperframes_reel/assets/SpaceGrotesk-Bold.ttf \
     core/content_pipeline/hyperframes_reel/assets/BebasNeue-Regular.ttf \
     core/content_pipeline/hyperframes_reel/assets/DMSans-Bold.ttf
```
Expected: las 4 líneas reportan `TrueType Font data` (o `OpenType font data` — ambos formatos son válidos para `@font-face` con `format('truetype')`). Si alguna reporta `Zip archive` o `ASCII text` (HTML de error de descarga), la descarga falló — repetir el Step 6 para esa familia con una URL/nombre de familia corregido.

- [ ] **Step 8: Commit**

```bash
git add core/shared/font_presets.py core/shared/tests/test_font_presets.py \
        core/content_pipeline/generators/image_generator.py \
        core/content_pipeline/hyperframes_reel/assets/PlayfairDisplay-Bold.ttf \
        core/content_pipeline/hyperframes_reel/assets/SpaceGrotesk-Bold.ttf \
        core/content_pipeline/hyperframes_reel/assets/BebasNeue-Regular.ttf \
        core/content_pipeline/hyperframes_reel/assets/DMSans-Bold.ttf
git commit -m "feat(reels): mover sistema de fuentes a modulo compartido + vendorizar 4 fuentes para HyperFrames"
```

---

## Task 2: Template `panel-wipe` (portada + contraportada)

**Files:**
- Create: `core/content_pipeline/hyperframes_reel/compositions/portada-panel-wipe.html`
- Create: `core/content_pipeline/hyperframes_reel/compositions/contraportada-panel-wipe.html`

**Interfaces:**
- Consumes: fuentes vendorizadas de Task 1 (`assets/Poppins-Bold.ttf`, `assets/PlayfairDisplay-Bold.ttf`, `assets/SpaceGrotesk-Bold.ttf`, `assets/BebasNeue-Regular.ttf`, `assets/DMSans-Bold.ttf`).
- Produces: 2 archivos de composición que Task 5 invoca vía `compositions/portada-panel-wipe.html`/`compositions/contraportada-panel-wipe.html`, con variables `hook_before`/`hook_highlight`/`hook_after`/`primary_color`/`text_color`/`font_family` (portada) y `cta_text`/`primary_color`/`text_color`/`font_family` (contraportada).

Diseño ya aprobado y verificado con render real en el brainstorm (boceto `compositions/drafts/panel-wipe.html`, borrado en este task — ver Step 3). Se generaliza: colores hardcodeados → variables CSS, texto hardcodeado → `data-var-text`, se agrega `font_family`, se elimina cualquier logo.

- [ ] **Step 1: Crear `portada-panel-wipe.html`**

```html
<!doctype html>
<html lang="es"
  data-composition-variables='[
    {"id":"hook_before","type":"string","label":"Hook (antes)","default":""},
    {"id":"hook_highlight","type":"string","label":"Hook (resaltado)","default":""},
    {"id":"hook_after","type":"string","label":"Hook (despues)","default":""},
    {"id":"primary_color","type":"color","label":"Color primario","default":"#1a1a2e"},
    {"id":"text_color","type":"color","label":"Color de texto","default":"#ffffff"},
    {"id":"font_family","type":"string","label":"Fuente","default":"Poppins, sans-serif"}
  ]'
>
<head>
  <meta charset="UTF-8" />
  <script src="node_modules/gsap/dist/gsap.min.js"></script>
  <style>
    @font-face { font-family: 'Poppins'; font-weight: 900; src: url('assets/Poppins-Bold.ttf') format('truetype'); }
    @font-face { font-family: 'Playfair Display'; font-weight: 900; src: url('assets/PlayfairDisplay-Bold.ttf') format('truetype'); }
    @font-face { font-family: 'Space Grotesk'; font-weight: 700; src: url('assets/SpaceGrotesk-Bold.ttf') format('truetype'); }
    @font-face { font-family: 'Bebas Neue'; font-weight: 400; src: url('assets/BebasNeue-Regular.ttf') format('truetype'); }
    @font-face { font-family: 'DM Sans'; font-weight: 700; src: url('assets/DMSans-Bold.ttf') format('truetype'); }

    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body { width: 1080px; height: 1920px; overflow: hidden; background: #1a1a2e; }

    .panel {
      position: absolute; left: 0; top: 460px; width: 1080px; height: 1000px;
      transform-origin: left center;
    }
    .panel-thin { background: var(--text_color); opacity: 0.12; top: 440px; height: 1040px; z-index: 1; }
    .panel-main { background: var(--primary_color); z-index: 2; }

    #hook {
      position: relative; z-index: 10;
      font-family: var(--font_family); font-weight: 900; font-size: 72px;
      color: var(--text_color); text-align: center; line-height: 1.25;
      max-width: 900px; margin: 0 auto; top: 460px;
      display: flex; align-items: center; justify-content: center; height: 1000px;
    }
    #hook-highlight { text-decoration: underline; text-underline-offset: 10px; text-decoration-thickness: 6px; }
  </style>
</head>
<body>
  <div id="root" data-composition-id="portada-panel-wipe" data-start="0" data-duration="3"
       data-width="1080" data-height="1920">
    <div class="panel panel-thin"></div>
    <div class="panel panel-main"></div>
    <div id="hook" class="clip" data-start="0" data-duration="3">
      <span data-var-text="hook_before"></span><span id="hook-highlight" data-var-text="hook_highlight"></span><span data-var-text="hook_after"></span>
    </div>
  </div>
  <script>
    window.__timelines = window.__timelines || {};
    const tl = gsap.timeline({ paused: true });

    tl.set('.panel', { scaleX: 0 });
    tl.to('.panel-thin', { scaleX: 1, duration: 0.8, ease: 'power3.inOut' }, 0);
    tl.to('.panel-main', { scaleX: 1, duration: 0.8, ease: 'power3.inOut' }, 0.1);
    tl.from('#hook', { opacity: 0, x: -50, duration: 0.8, ease: 'power2.out' }, 0.9);

    window.__timelines['portada-panel-wipe'] = tl;
  </script>
</body>
</html>
```

- [ ] **Step 2: Crear `contraportada-panel-wipe.html`**

```html
<!doctype html>
<html lang="es"
  data-composition-variables='[
    {"id":"cta_text","type":"string","label":"CTA","default":""},
    {"id":"primary_color","type":"color","label":"Color primario","default":"#1a1a2e"},
    {"id":"text_color","type":"color","label":"Color de texto","default":"#ffffff"},
    {"id":"font_family","type":"string","label":"Fuente","default":"Poppins, sans-serif"}
  ]'
>
<head>
  <meta charset="UTF-8" />
  <script src="node_modules/gsap/dist/gsap.min.js"></script>
  <style>
    @font-face { font-family: 'Poppins'; font-weight: 900; src: url('assets/Poppins-Bold.ttf') format('truetype'); }
    @font-face { font-family: 'Playfair Display'; font-weight: 900; src: url('assets/PlayfairDisplay-Bold.ttf') format('truetype'); }
    @font-face { font-family: 'Space Grotesk'; font-weight: 700; src: url('assets/SpaceGrotesk-Bold.ttf') format('truetype'); }
    @font-face { font-family: 'Bebas Neue'; font-weight: 400; src: url('assets/BebasNeue-Regular.ttf') format('truetype'); }
    @font-face { font-family: 'DM Sans'; font-weight: 700; src: url('assets/DMSans-Bold.ttf') format('truetype'); }

    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body { width: 1080px; height: 1920px; overflow: hidden; background: #1a1a2e; }

    .panel {
      position: absolute; left: 0; top: 460px; width: 1080px; height: 1000px;
      transform-origin: left center;
    }
    .panel-thin { background: var(--text_color); opacity: 0.12; top: 440px; height: 1040px; z-index: 1; }
    .panel-main { background: var(--primary_color); z-index: 2; }

    #cta {
      position: relative; z-index: 10;
      font-family: var(--font_family); font-weight: 900; font-size: 72px;
      color: var(--text_color); text-align: center; line-height: 1.25;
      max-width: 900px; margin: 0 auto; top: 460px;
      display: flex; align-items: center; justify-content: center; height: 1000px;
    }
  </style>
</head>
<body>
  <div id="root" data-composition-id="contraportada-panel-wipe" data-start="0" data-duration="3"
       data-width="1080" data-height="1920">
    <div class="panel panel-thin"></div>
    <div class="panel panel-main"></div>
    <div id="cta" class="clip" data-start="0" data-duration="3" data-var-text="cta_text"></div>
  </div>
  <script>
    window.__timelines = window.__timelines || {};
    const tl = gsap.timeline({ paused: true });

    tl.set('.panel', { scaleX: 0 });
    tl.to('.panel-thin', { scaleX: 1, duration: 0.8, ease: 'power3.inOut' }, 0);
    tl.to('.panel-main', { scaleX: 1, duration: 0.8, ease: 'power3.inOut' }, 0.1);
    tl.from('#cta', { opacity: 0, x: -50, duration: 0.8, ease: 'power2.out' }, 0.9);

    window.__timelines['contraportada-panel-wipe'] = tl;
  </script>
</body>
</html>
```

- [ ] **Step 3: Eliminar los archivos obsoletos que estos reemplazan**

```bash
rm -rf core/content_pipeline/hyperframes_reel/compositions/drafts
```

(`compositions/portada.html`/`compositions/contraportada.html` — los actuales de producción — se eliminan en el Task 4, junto con el último template, para que el pipeline nunca quede sin al menos 1 composición de portada/contraportada funcional entre tasks.)

- [ ] **Step 4: Render real de verificación (sin costo de API — solo Node/Chrome local)**

```bash
cd core/content_pipeline/hyperframes_reel
./node_modules/.bin/hyperframes render . -c compositions/portada-panel-wipe.html \
  -o /tmp/verify-portada-panel-wipe.mp4 --fps 24 \
  --variables-file <(echo '{"hook_before":"Innovación ","hook_highlight":"tecnológica","hook_after":", paso a paso","primary_color":"#e94560","text_color":"white","font_family":"'"'"'Poppins'"'"', sans-serif"}')
```

Expected: STATUS de salida sin errores, sin líneas `[INFO] [Compiler] Rewriting missing`. Extrae un frame a mitad de la animación y confírmalo visualmente:

```bash
ffmpeg -y -ss 2 -i /tmp/verify-portada-panel-wipe.mp4 -frames:v 1 /tmp/verify-portada-panel-wipe.png
```

Repite el mismo render+verificación para `contraportada-panel-wipe.html` con variables `{"cta_text":"Compra ahora","primary_color":"#e94560","text_color":"white","font_family":"'Poppins', sans-serif"}`.

Reporta en tu STATUS si el texto se ve completo (sin desbordar el cuadro) y legible sobre el panel — si no, ajusta `font-size`/`max-width` en el HTML antes de continuar.

- [ ] **Step 5: Commit**

```bash
git add core/content_pipeline/hyperframes_reel/compositions/portada-panel-wipe.html \
        core/content_pipeline/hyperframes_reel/compositions/contraportada-panel-wipe.html
git rm -r core/content_pipeline/hyperframes_reel/compositions/drafts
git commit -m "feat(reels): template panel-wipe parametrizado para portada/contraportada"
```

---

## Task 3: Template `dynamic-background` (portada + contraportada)

**Files:**
- Create: `core/content_pipeline/hyperframes_reel/compositions/portada-dynamic-background.html`
- Create: `core/content_pipeline/hyperframes_reel/compositions/contraportada-dynamic-background.html`

**Interfaces:**
- Mismas variables/consumo que Task 2, distinto `data-composition-id` (`portada-dynamic-background`/`contraportada-dynamic-background`) y tratamiento visual.

Corrige el bug de desborde del boceto original (`compositions/drafts/dynamic-background.html`, ya borrado en Task 2) — el texto pasa de `display:flex` sin wrap a un bloque normal con `max-width`. Fondo neutro oscuro fijo con blobs en `primary_color` a distinta opacidad (sin calcular tonos derivados), texto blanco fijo (no depende de `text_color`, ya que no se apoya sobre un panel de `primary_color` sino sobre el fondo oscuro fijo).

- [ ] **Step 1: Crear `portada-dynamic-background.html`**

```html
<!doctype html>
<html lang="es"
  data-composition-variables='[
    {"id":"hook_before","type":"string","label":"Hook (antes)","default":""},
    {"id":"hook_highlight","type":"string","label":"Hook (resaltado)","default":""},
    {"id":"hook_after","type":"string","label":"Hook (despues)","default":""},
    {"id":"primary_color","type":"color","label":"Color primario","default":"#1a1a2e"},
    {"id":"text_color","type":"color","label":"Color de texto","default":"#ffffff"},
    {"id":"font_family","type":"string","label":"Fuente","default":"Poppins, sans-serif"}
  ]'
>
<head>
  <meta charset="UTF-8" />
  <script src="node_modules/gsap/dist/gsap.min.js"></script>
  <style>
    @font-face { font-family: 'Poppins'; font-weight: 900; src: url('assets/Poppins-Bold.ttf') format('truetype'); }
    @font-face { font-family: 'Playfair Display'; font-weight: 900; src: url('assets/PlayfairDisplay-Bold.ttf') format('truetype'); }
    @font-face { font-family: 'Space Grotesk'; font-weight: 700; src: url('assets/SpaceGrotesk-Bold.ttf') format('truetype'); }
    @font-face { font-family: 'Bebas Neue'; font-weight: 400; src: url('assets/BebasNeue-Regular.ttf') format('truetype'); }
    @font-face { font-family: 'DM Sans'; font-weight: 700; src: url('assets/DMSans-Bold.ttf') format('truetype'); }

    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body { width: 1080px; height: 1920px; overflow: hidden; background: #1a1a2e; }

    .blob { position: absolute; border-radius: 50%; filter: blur(80px); background: var(--primary_color); }
    .blob-1 { width: 800px; height: 800px; top: -100px; left: -200px; opacity: 0.6; }
    .blob-2 { width: 900px; height: 900px; bottom: -200px; right: -100px; opacity: 0.5; }
    .blob-3 { width: 600px; height: 600px; top: 400px; left: 300px; opacity: 0.4; }

    .wrap {
      position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
      padding: 0 90px;
    }
    #hook {
      position: relative; z-index: 10;
      font-family: var(--font_family); font-weight: 900; font-size: 76px;
      color: #ffffff; text-align: center; line-height: 1.3; max-width: 900px;
    }
    #hook-highlight { color: var(--primary_color); }
  </style>
</head>
<body>
  <div id="root" data-composition-id="portada-dynamic-background" data-start="0" data-duration="3"
       data-width="1080" data-height="1920">
    <div class="blob blob-1"></div>
    <div class="blob blob-2"></div>
    <div class="blob blob-3"></div>
    <div class="wrap">
      <div id="hook" class="clip" data-start="0" data-duration="3">
        <span data-var-text="hook_before"></span><span id="hook-highlight" data-var-text="hook_highlight"></span><span data-var-text="hook_after"></span>
      </div>
    </div>
  </div>
  <script>
    window.__timelines = window.__timelines || {};
    const tl = gsap.timeline({ paused: true });

    tl.to('.blob-1', { x: 200, y: 150, scale: 1.1, duration: 3, ease: 'sine.inOut' }, 0);
    tl.to('.blob-2', { x: -300, y: -200, scale: 0.9, duration: 3, ease: 'sine.inOut' }, 0);
    tl.to('.blob-3', { x: 100, y: 300, scale: 1.2, duration: 3, ease: 'sine.inOut' }, 0);
    tl.from('#hook', { opacity: 0, scale: 0.9, duration: 1.5, ease: 'power2.out' }, 0.5);

    window.__timelines['portada-dynamic-background'] = tl;
  </script>
</body>
</html>
```

- [ ] **Step 2: Crear `contraportada-dynamic-background.html`**

```html
<!doctype html>
<html lang="es"
  data-composition-variables='[
    {"id":"cta_text","type":"string","label":"CTA","default":""},
    {"id":"primary_color","type":"color","label":"Color primario","default":"#1a1a2e"},
    {"id":"text_color","type":"color","label":"Color de texto","default":"#ffffff"},
    {"id":"font_family","type":"string","label":"Fuente","default":"Poppins, sans-serif"}
  ]'
>
<head>
  <meta charset="UTF-8" />
  <script src="node_modules/gsap/dist/gsap.min.js"></script>
  <style>
    @font-face { font-family: 'Poppins'; font-weight: 900; src: url('assets/Poppins-Bold.ttf') format('truetype'); }
    @font-face { font-family: 'Playfair Display'; font-weight: 900; src: url('assets/PlayfairDisplay-Bold.ttf') format('truetype'); }
    @font-face { font-family: 'Space Grotesk'; font-weight: 700; src: url('assets/SpaceGrotesk-Bold.ttf') format('truetype'); }
    @font-face { font-family: 'Bebas Neue'; font-weight: 400; src: url('assets/BebasNeue-Regular.ttf') format('truetype'); }
    @font-face { font-family: 'DM Sans'; font-weight: 700; src: url('assets/DMSans-Bold.ttf') format('truetype'); }

    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body { width: 1080px; height: 1920px; overflow: hidden; background: #1a1a2e; }

    .blob { position: absolute; border-radius: 50%; filter: blur(80px); background: var(--primary_color); }
    .blob-1 { width: 800px; height: 800px; top: -100px; left: -200px; opacity: 0.6; }
    .blob-2 { width: 900px; height: 900px; bottom: -200px; right: -100px; opacity: 0.5; }
    .blob-3 { width: 600px; height: 600px; top: 400px; left: 300px; opacity: 0.4; }

    .wrap {
      position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
      padding: 0 90px;
    }
    #cta {
      position: relative; z-index: 10;
      font-family: var(--font_family); font-weight: 900; font-size: 76px;
      color: var(--primary_color); text-align: center; line-height: 1.3; max-width: 900px;
    }
  </style>
</head>
<body>
  <div id="root" data-composition-id="contraportada-dynamic-background" data-start="0" data-duration="3"
       data-width="1080" data-height="1920">
    <div class="blob blob-1"></div>
    <div class="blob blob-2"></div>
    <div class="blob blob-3"></div>
    <div class="wrap">
      <div id="cta" class="clip" data-start="0" data-duration="3" data-var-text="cta_text"></div>
    </div>
  </div>
  <script>
    window.__timelines = window.__timelines || {};
    const tl = gsap.timeline({ paused: true });

    tl.to('.blob-1', { x: 200, y: 150, scale: 1.1, duration: 3, ease: 'sine.inOut' }, 0);
    tl.to('.blob-2', { x: -300, y: -200, scale: 0.9, duration: 3, ease: 'sine.inOut' }, 0);
    tl.to('.blob-3', { x: 100, y: 300, scale: 1.2, duration: 3, ease: 'sine.inOut' }, 0);
    tl.from('#cta', { opacity: 0, scale: 0.9, duration: 1.5, ease: 'power2.out' }, 0.5);

    window.__timelines['contraportada-dynamic-background'] = tl;
  </script>
</body>
</html>
```

- [ ] **Step 3: Render real de verificación**

Mismo procedimiento que Task 2 Step 4, apuntando a `compositions/portada-dynamic-background.html` y `compositions/contraportada-dynamic-background.html` con las mismas variables de ejemplo. Confirma visualmente que "Innovación tecnológica, paso a paso" (o el texto de ejemplo que uses) NO se desborda del cuadro — el bug original a corregir.

- [ ] **Step 4: Commit**

```bash
git add core/content_pipeline/hyperframes_reel/compositions/portada-dynamic-background.html \
        core/content_pipeline/hyperframes_reel/compositions/contraportada-dynamic-background.html
git commit -m "feat(reels): template dynamic-background parametrizado, corrige desborde de texto del boceto"
```

---

## Task 4: Template `kinetic-typography` (portada + contraportada) + retirar composiciones antiguas

**Files:**
- Create: `core/content_pipeline/hyperframes_reel/compositions/portada-kinetic-typography.html`
- Create: `core/content_pipeline/hyperframes_reel/compositions/contraportada-kinetic-typography.html`
- Delete: `core/content_pipeline/hyperframes_reel/compositions/portada.html`
- Delete: `core/content_pipeline/hyperframes_reel/compositions/contraportada.html`

**Interfaces:**
- Mismas variables que Tasks 2-3, `data-composition-id` `portada-kinetic-typography`/`contraportada-kinetic-typography`.

Corrige el mismo bug de desborde. A diferencia de los otros 2 templates, este preserva el efecto de "palabras entrando en cascada" del boceto original — para lograrlo SIN depender de que `data-var-text` divida el texto en palabras (no lo hace, sustituye el texto completo de un elemento), el script de la composición lee las variables directo vía `window.__hyperframes.getVariables()` (mismo mecanismo ya usado en producción para `logo_url` antes de este plan) y arma los `<span>` de palabras en JavaScript de forma síncrona antes de construir el timeline — determinista, sin async ni red.

- [ ] **Step 1: Crear `portada-kinetic-typography.html`**

```html
<!doctype html>
<html lang="es"
  data-composition-variables='[
    {"id":"hook_before","type":"string","label":"Hook (antes)","default":""},
    {"id":"hook_highlight","type":"string","label":"Hook (resaltado)","default":""},
    {"id":"hook_after","type":"string","label":"Hook (despues)","default":""},
    {"id":"primary_color","type":"color","label":"Color primario","default":"#1a1a2e"},
    {"id":"text_color","type":"color","label":"Color de texto","default":"#ffffff"},
    {"id":"font_family","type":"string","label":"Fuente","default":"Poppins, sans-serif"}
  ]'
>
<head>
  <meta charset="UTF-8" />
  <script src="node_modules/gsap/dist/gsap.min.js"></script>
  <style>
    @font-face { font-family: 'Poppins'; font-weight: 900; src: url('assets/Poppins-Bold.ttf') format('truetype'); }
    @font-face { font-family: 'Playfair Display'; font-weight: 900; src: url('assets/PlayfairDisplay-Bold.ttf') format('truetype'); }
    @font-face { font-family: 'Space Grotesk'; font-weight: 700; src: url('assets/SpaceGrotesk-Bold.ttf') format('truetype'); }
    @font-face { font-family: 'Bebas Neue'; font-weight: 400; src: url('assets/BebasNeue-Regular.ttf') format('truetype'); }
    @font-face { font-family: 'DM Sans'; font-weight: 700; src: url('assets/DMSans-Bold.ttf') format('truetype'); }

    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body { width: 1080px; height: 1920px; overflow: hidden; background: #ffffff; }

    .deco { position: absolute; background: var(--primary_color); }
    .deco-1 { width: 400px; height: 10px; top: 200px; left: -200px; }
    .deco-2 { width: 10px; height: 300px; bottom: -100px; right: 200px; }
    .deco-3 { width: 80px; height: 80px; border-radius: 50%; top: 400px; right: 150px; opacity: 0.5; }

    .wrap { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; padding: 0 90px; }
    #hook-container {
      font-family: var(--font_family); font-weight: 900; font-size: 92px;
      color: #1a1a2e; text-align: center; line-height: 1.3; max-width: 900px;
    }
    #hook-container .word { display: inline-block; }
    #hook-container .word.highlight { color: var(--primary_color); }
  </style>
</head>
<body>
  <div id="root" data-composition-id="portada-kinetic-typography" data-start="0" data-duration="3"
       data-width="1080" data-height="1920">
    <div class="deco deco-1"></div>
    <div class="deco deco-2"></div>
    <div class="deco deco-3"></div>
    <div class="wrap">
      <div id="hook-container" class="clip" data-start="0" data-duration="3"></div>
    </div>
  </div>
  <script>
    const { hook_before, hook_highlight, hook_after } = window.__hyperframes.getVariables();
    const container = document.getElementById('hook-container');
    // Sin espacio en el join: hook_before/hook_after ya traen su propio espacio
    // (o puntuacion pegada, ej. ", paso a paso") desde _split_highlight en Python.
    const fullText = [hook_before, hook_highlight, hook_after].join('').trim().replace(/\s+/g, ' ');
    const highlightWords = new Set(hook_highlight.trim().split(/\s+/).filter(Boolean));
    fullText.split(' ').forEach((word, i, arr) => {
      const span = document.createElement('span');
      // Compara sin puntuacion pegada (ej. "tecnológica," debe resaltar igual
      // que "tecnológica") pero conserva la puntuacion original en pantalla.
      const bareWord = word.replace(/^[¿¡"'([{]+|[.,!?;:"')\]}]+$/g, '');
      span.className = 'word' + (highlightWords.has(bareWord) ? ' highlight' : '');
      span.textContent = word;
      container.appendChild(span);
      if (i < arr.length - 1) container.appendChild(document.createTextNode(' '));
    });

    window.__timelines = window.__timelines || {};
    const tl = gsap.timeline({ paused: true });

    tl.to('.deco-1', { x: 600, duration: 3, ease: 'none' }, 0);
    tl.to('.deco-2', { y: -500, duration: 3, ease: 'none' }, 0);
    tl.to('.deco-3', { scale: 1.5, opacity: 0, duration: 3, ease: 'power1.inOut' }, 0);
    tl.from('#hook-container .word', { y: 80, opacity: 0, duration: 0.6, stagger: 0.08, ease: 'power3.out' }, 0.2);

    window.__timelines['portada-kinetic-typography'] = tl;
  </script>
</body>
</html>
```

- [ ] **Step 2: Crear `contraportada-kinetic-typography.html`**

```html
<!doctype html>
<html lang="es"
  data-composition-variables='[
    {"id":"cta_text","type":"string","label":"CTA","default":""},
    {"id":"primary_color","type":"color","label":"Color primario","default":"#1a1a2e"},
    {"id":"text_color","type":"color","label":"Color de texto","default":"#ffffff"},
    {"id":"font_family","type":"string","label":"Fuente","default":"Poppins, sans-serif"}
  ]'
>
<head>
  <meta charset="UTF-8" />
  <script src="node_modules/gsap/dist/gsap.min.js"></script>
  <style>
    @font-face { font-family: 'Poppins'; font-weight: 900; src: url('assets/Poppins-Bold.ttf') format('truetype'); }
    @font-face { font-family: 'Playfair Display'; font-weight: 900; src: url('assets/PlayfairDisplay-Bold.ttf') format('truetype'); }
    @font-face { font-family: 'Space Grotesk'; font-weight: 700; src: url('assets/SpaceGrotesk-Bold.ttf') format('truetype'); }
    @font-face { font-family: 'Bebas Neue'; font-weight: 400; src: url('assets/BebasNeue-Regular.ttf') format('truetype'); }
    @font-face { font-family: 'DM Sans'; font-weight: 700; src: url('assets/DMSans-Bold.ttf') format('truetype'); }

    * { margin: 0; padding: 0; box-sizing: border-box; }
    html, body { width: 1080px; height: 1920px; overflow: hidden; background: #ffffff; }

    .deco { position: absolute; background: var(--primary_color); }
    .deco-1 { width: 400px; height: 10px; top: 200px; left: -200px; }
    .deco-2 { width: 10px; height: 300px; bottom: -100px; right: 200px; }
    .deco-3 { width: 80px; height: 80px; border-radius: 50%; top: 400px; right: 150px; opacity: 0.5; }

    .wrap { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; padding: 0 90px; }
    #cta-container {
      font-family: var(--font_family); font-weight: 900; font-size: 92px;
      color: var(--primary_color); text-align: center; line-height: 1.3; max-width: 900px;
    }
    #cta-container .word { display: inline-block; }
  </style>
</head>
<body>
  <div id="root" data-composition-id="contraportada-kinetic-typography" data-start="0" data-duration="3"
       data-width="1080" data-height="1920">
    <div class="deco deco-1"></div>
    <div class="deco deco-2"></div>
    <div class="deco deco-3"></div>
    <div class="wrap">
      <div id="cta-container" class="clip" data-start="0" data-duration="3"></div>
    </div>
  </div>
  <script>
    const { cta_text } = window.__hyperframes.getVariables();
    const container = document.getElementById('cta-container');
    cta_text.trim().split(/\s+/).filter(Boolean).forEach((word, i, arr) => {
      const span = document.createElement('span');
      span.className = 'word';
      span.textContent = word;
      container.appendChild(span);
      if (i < arr.length - 1) container.appendChild(document.createTextNode(' '));
    });

    window.__timelines = window.__timelines || {};
    const tl = gsap.timeline({ paused: true });

    tl.to('.deco-1', { x: 600, duration: 3, ease: 'none' }, 0);
    tl.to('.deco-2', { y: -500, duration: 3, ease: 'none' }, 0);
    tl.to('.deco-3', { scale: 1.5, opacity: 0, duration: 3, ease: 'power1.inOut' }, 0);
    tl.from('#cta-container .word', { y: 80, opacity: 0, duration: 0.6, stagger: 0.08, ease: 'power3.out' }, 0.2);

    window.__timelines['contraportada-kinetic-typography'] = tl;
  </script>
</body>
</html>
```

- [ ] **Step 3: Render real de verificación**

Mismo procedimiento que Task 2 Step 4, apuntando a `compositions/portada-kinetic-typography.html` y `compositions/contraportada-kinetic-typography.html`. Presta atención especial a: (a) el texto no se desborda (el bug original), (b) las palabras dentro de `hook_highlight` (ej. "tecnológica") aparecen en `primary_color` — confirma que el `Set` de palabras resaltadas las está marcando correctamente comparando contra el texto de ejemplo que uses.

- [ ] **Step 4: Retirar las composiciones antiguas de producción**

Las 3 nuevas parejas de templates (Tasks 2-4) reemplazan por completo a las 2 composiciones actuales:

```bash
git rm core/content_pipeline/hyperframes_reel/compositions/portada.html \
       core/content_pipeline/hyperframes_reel/compositions/contraportada.html
```

- [ ] **Step 5: Commit**

```bash
git add core/content_pipeline/hyperframes_reel/compositions/portada-kinetic-typography.html \
        core/content_pipeline/hyperframes_reel/compositions/contraportada-kinetic-typography.html
git commit -m "feat(reels): template kinetic-typography parametrizado + retira portada/contraportada.html (reemplazadas por el catalogo de 3 templates)"
```

---

## Task 5: Orquestación en Python — selección de template, fuente compartida, quitar logo

**Files:**
- Modify: `core/content_pipeline/generators/reel_generator.py`
- Modify: `core/content_pipeline/tasks.py`
- Test: `core/content_pipeline/tests/test_reel_generator.py`

**Interfaces:**
- Consumes: `core.shared.font_presets.choose_font_preset` (Task 1), las 6 composiciones `compositions/{portada,contraportada}-{panel-wipe,kinetic-typography,dynamic-background}.html` (Tasks 2-4).
- Produces: `ReelGenerator._choose_reel_template(hook_text: str, tag_cta: str) -> str`. `_generate_branded_segment(kind, hook_text, highlight_word, tag_cta, primary_color, template, font_family) -> bytes | None` (firma nueva, sin `logo_url`). `_generate_clips_with_branding(scene_prompts, hook_text, highlight_word, tag_cta, primary_color, filename_prefix) -> tuple[list[bytes], bool]` (firma nueva, sin `logo_url`, con `filename_prefix`). `generate(script, colors, filename_prefix) -> tuple[str, str]` (pierde el parámetro `logo_url`).

- [ ] **Step 1: Agregar imports y la lista de templates**

En `core/content_pipeline/generators/reel_generator.py`, localiza el bloque de imports (líneas 1-24) y agrega `random` (no está importado hoy):

```python
import base64
import html as _html
import json
import logging
import os
import random
import re
import subprocess
import tempfile
import time
```

Agrega el import del módulo compartido de fuentes, junto a los demás imports de `core.shared`:

```python
from core.shared.font_presets import choose_font_preset
```

Localiza la constante `_VIDEO_WIDTH = 1080` (línea ~36) y agrega justo después:

```python
_REEL_TEMPLATES = ['panel-wipe', 'kinetic-typography', 'dynamic-background']
```

- [ ] **Step 2: Escribir los tests que fallan para `_choose_reel_template`**

En `core/content_pipeline/tests/test_reel_generator.py`, agrega esta clase nueva después de `class TestGenerateSceneStill` (o en cualquier punto del archivo — el orden de clases no importa para pytest):

```python
class TestChooseReelTemplate:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_template_chosen_by_gemini(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"template": "panel-wipe"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._choose_reel_template('Hook de prueba', 'CTA de prueba')
        assert result == 'panel-wipe'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_falls_back_to_random_on_api_error(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator, _REEL_TEMPLATES
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            result = gen._choose_reel_template('Hook', 'CTA')
        assert result in _REEL_TEMPLATES

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_falls_back_to_random_on_invalid_template_name(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator, _REEL_TEMPLATES
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"template": "not-a-real-template"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._choose_reel_template('Hook', 'CTA')
        assert result in _REEL_TEMPLATES
```

- [ ] **Step 3: Correr los tests para verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py::TestChooseReelTemplate -v`
Expected: FAIL — `_choose_reel_template`/`_REEL_TEMPLATES` no existen todavía.

- [ ] **Step 4: Implementar `_choose_reel_template`**

Localiza `_generate_branded_segment` (línea ~391) y agrega este método nuevo justo ANTES:

```python
    def _choose_reel_template(self, hook_text: str, tag_cta: str) -> str:
        """Gemini elige el template de portada/contraportada que mejor calza con
        el tono del guion, en vez de una eleccion aleatoria (mismo patron que
        ImageGenerator._choose_template_for_image)."""
        try:
            client = _vertex_client()
            prompt = (
                "Este es el hook y el CTA de un reel vertical para redes sociales.\n"
                f"Hook: \"{hook_text}\"\n"
                f"CTA: \"{tag_cta}\"\n\n"
                "Elige el template de portada/contraportada que mejor calce con el tono "
                "del mensaje. Responde UNICAMENTE con este JSON (sin markdown):\n"
                '{"template": "panel-wipe" | "kinetic-typography" | "dynamic-background"}\n\n'
                "- 'panel-wipe': paneles solidos que entran deslizandose, estilo noticiero/anuncio "
                "de TV. Ideal para mensajes directos, corporativos, de autoridad.\n"
                "- 'kinetic-typography': palabras que entran en cascada con movimiento, fondo claro "
                "con lineas decorativas. Ideal para mensajes energicos, dinamicos, juveniles.\n"
                "- 'dynamic-background': fondo con formas de color en movimiento continuo, texto "
                "simple. Ideal para mensajes calmados, aspiracionales, elegantes."
            )
            with track_external_api('gemini', operation='reel_template_select'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=prompt,
                )
            record_tokens(resp, operation='reel_template_select',
                          response_preview=resp.text[:200] if resp.text else '')
            raw = resp.text.strip()
            match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
                template = data.get('template', '')
                if template in _REEL_TEMPLATES:
                    logger.info(f"Template de reel seleccionado: {template}")
                    return template
        except Exception as e:
            logger.warning(f"Seleccion de template de reel por IA fallo, usando aleatorio: {e}")
        return random.choice(_REEL_TEMPLATES)
```

- [ ] **Step 5: Correr los tests para verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py::TestChooseReelTemplate -v`
Expected: 3 passed.

- [ ] **Step 6: Reescribir `_generate_branded_segment` (quita `logo_url`, agrega `template`/`font_family`)**

Reemplaza el método completo (líneas ~391-425):

```python
    def _generate_branded_segment(self, kind: str, hook_text: str, highlight_word: str,
                                   tag_cta: str, primary_color: str, template: str,
                                   font_family: str) -> bytes | None:
        text_color = _readable_text_color(primary_color)
        if kind == 'portada':
            before, highlight, after = _split_highlight(hook_text, highlight_word)
            variables = {
                'hook_before': before, 'hook_highlight': highlight, 'hook_after': after,
                'primary_color': primary_color, 'text_color': text_color, 'font_family': font_family,
            }
        else:
            variables = {
                'cta_text': tag_cta, 'primary_color': primary_color,
                'text_color': text_color, 'font_family': font_family,
            }
        composition = f'compositions/{kind}-{template}.html'

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

- [ ] **Step 7: Reescribir `_generate_clips_with_branding` (quita `logo_url`, agrega `filename_prefix`, calcula template/fuente una sola vez)**

Reemplaza el método completo (líneas ~442-479):

```python
    def _generate_clips_with_branding(self, scene_prompts: list[str], hook_text: str,
                                       highlight_word: str, tag_cta: str, primary_color: str,
                                       filename_prefix: str) -> tuple[list[bytes], bool]:
        clips = self._generate_video_clips(scene_prompts)
        if len(clips) < 3:
            return clips, False

        width, height, fps = self._probe_clip_dimensions(clips[0])

        font_seed = filename_prefix.rsplit('-day', 1)[0] if '-day' in filename_prefix else filename_prefix
        font_preset = choose_font_preset(font_seed)
        template = self._choose_reel_template(hook_text, tag_cta)

        portada = self._generate_branded_segment(
            'portada', hook_text, highlight_word, tag_cta, primary_color, template, font_preset['font_family'],
        )
        if portada is None:
            portada = self._generate_branded_segment(
                'portada', hook_text, highlight_word, tag_cta, primary_color, template, font_preset['font_family'],
            )  # 1 reintento

        if portada is None:
            logger.warning("Portada o contraportada fallaron tras reintento, reel sin marca (estructura Parte A)")
            record_hyperframes_fallback()
            return clips, False

        contraportada = self._generate_branded_segment(
            'contraportada', hook_text, highlight_word, tag_cta, primary_color, template, font_preset['font_family'],
        )
        if contraportada is None:
            contraportada = self._generate_branded_segment(
                'contraportada', hook_text, highlight_word, tag_cta, primary_color, template, font_preset['font_family'],
            )  # 1 reintento

        if contraportada is None:
            logger.warning("Portada o contraportada fallaron tras reintento, reel sin marca (estructura Parte A)")
            record_hyperframes_fallback()
            return clips, False

        portada_normalized = self._normalize_branded_segment(portada, width, height, fps)
        contraportada_normalized = self._normalize_branded_segment(contraportada, width, height, fps)
        return [portada_normalized] + clips + [contraportada_normalized], True
```

- [ ] **Step 8: Reescribir `generate()` (quita el parámetro `logo_url`)**

Reemplaza el método completo (líneas ~836-865):

```python
    def generate(self, script: dict, colors: list[str], filename_prefix: str) -> tuple[str, str]:
        try:
            primary_color = colors[0] if colors else '#e94560'
            clips, has_branding = self._generate_clips_with_branding(
                script['scene_prompts'], script['hook_text'], script['highlight_word'],
                script['tag_cta'], primary_color, filename_prefix,
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

- [ ] **Step 9: Actualizar `tasks.py` (deja de pasar `logo_url`)**

En `core/content_pipeline/tasks.py`, localiza (línea ~29-32):

```python
        video_url, poster_url = reel_gen.generate(
            script=script, colors=kwargs.get('colors', []), filename_prefix=filename,
            logo_url=brand_dna.logo_url if brand_dna else '',
        )
```

Reemplázalo por:

```python
        video_url, poster_url = reel_gen.generate(
            script=script, colors=kwargs.get('colors', []), filename_prefix=filename,
        )
```

- [ ] **Step 10: Actualizar `TestGenerateBrandedSegment` (3 tests existentes)**

Reemplaza la clase completa en `test_reel_generator.py`:

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
                'portada', 'Descubre algo nuevo', 'algo', 'Compra ahora', '#1a1a2e',
                'panel-wipe', "'Poppins', sans-serif",
            )

        assert result == fake_output
        assert captured['variables'] == {
            'hook_before': 'Descubre ', 'hook_highlight': 'algo', 'hook_after': ' nuevo',
            'primary_color': '#1a1a2e', 'text_color': 'white', 'font_family': "'Poppins', sans-serif",
        }
        assert '-c' in captured['cmd']
        assert captured['cmd'][captured['cmd'].index('-c') + 1] == 'compositions/portada-panel-wipe.html'
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
                'contraportada', 'Descubre algo nuevo', 'algo', 'Compra ahora', '#1a1a2e',
                'dynamic-background', "'Bebas Neue', sans-serif",
            )

        assert result == fake_output
        assert captured['variables'] == {
            'cta_text': 'Compra ahora', 'primary_color': '#1a1a2e',
            'text_color': 'white', 'font_family': "'Bebas Neue', sans-serif",
        }
        assert captured['cmd'][captured['cmd'].index('-c') + 1] == 'compositions/contraportada-dynamic-background.html'

    def test_returns_none_on_subprocess_error(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=subprocess.CalledProcessError(1, 'hyperframes')):
            result = gen._generate_branded_segment(
                'portada', 'Descubre algo nuevo', 'algo', 'Compra ahora', '#1a1a2e',
                'panel-wipe', "'Poppins', sans-serif",
            )
        assert result is None
```

- [ ] **Step 11: Actualizar `TestGenerateClipsWithBranding` (3 tests existentes)**

Reemplaza la clase completa:

```python
class TestGenerateClipsWithBranding:
    def test_branding_success_prepends_and_appends_normalized_segments(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_video_clips', return_value=[b'v', b's1', b's2', b's3', b's4', b's5']), \
             patch.object(gen, '_probe_clip_dimensions', return_value=(720, 1280, 24.0)), \
             patch.object(gen, '_choose_reel_template', return_value='panel-wipe') as mock_template, \
             patch('core.content_pipeline.generators.reel_generator.choose_font_preset',
                   return_value={'font_family': "'Poppins', sans-serif", 'font_import': 'Poppins'}) as mock_font, \
             patch.object(gen, '_generate_branded_segment', side_effect=[b'portada-raw', b'contra-raw']) as mock_branded, \
             patch.object(gen, '_normalize_branded_segment', side_effect=[b'portada-norm', b'contra-norm']) as mock_norm:
            clips, has_branding = gen._generate_clips_with_branding(
                ['scene 1', 'scene 2'], 'Hook', 'word', 'CTA', '#1a1a2e', 'job1-day1',
            )

        assert has_branding is True
        assert clips == [b'portada-norm', b'v', b's1', b's2', b's3', b's4', b's5', b'contra-norm']
        mock_font.assert_called_once_with('job1')
        mock_template.assert_called_once_with('Hook', 'CTA')
        assert mock_branded.call_args_list == [
            call('portada', 'Hook', 'word', 'CTA', '#1a1a2e', 'panel-wipe', "'Poppins', sans-serif"),
            call('contraportada', 'Hook', 'word', 'CTA', '#1a1a2e', 'panel-wipe', "'Poppins', sans-serif"),
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
             patch.object(gen, '_choose_reel_template', return_value='panel-wipe'), \
             patch('core.content_pipeline.generators.reel_generator.choose_font_preset',
                   return_value={'font_family': "'Poppins', sans-serif", 'font_import': 'Poppins'}), \
             patch.object(gen, '_generate_branded_segment', return_value=None) as mock_branded, \
             patch.object(gen, '_normalize_branded_segment') as mock_norm, \
             patch('core.content_pipeline.generators.reel_generator.record_hyperframes_fallback') as mock_fallback:
            clips, has_branding = gen._generate_clips_with_branding(
                ['scene 1', 'scene 2'], 'Hook', 'word', 'CTA', '#1a1a2e', 'job1-day1',
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
                ['scene 1'], 'Hook', 'word', 'CTA', '#1a1a2e', 'job1-day1',
            )

        assert clips == [b'v']
        assert has_branding is False
        mock_branded.assert_not_called()
```

Nota sobre `mock_font.assert_called_once_with('job1')`: `filename_prefix='job1-day1'` contiene `'-day'`, así que `font_seed = 'job1-day1'.rsplit('-day', 1)[0]` da `'job1'`.

- [ ] **Step 12: Actualizar `TestGenerate` (5 tests: 4 existentes + 1 ya no aplica)**

Reemplaza la clase completa (la firma de `generate()` ya no acepta `logo_url`, y `_generate_clips_with_branding` ya no recibe `logo_url` sino `filename_prefix` como último argumento):

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
            _FAKE_SCRIPT['tag_cta'], '#1a1a2e', 'job1-day1',
        )
        mock_up_video.assert_called_once_with(b'final-mp4', 'job1-day1')
        mock_up_poster.assert_called_once_with(b'poster-png', 'job1-day1-poster')
        mock_assemble.assert_called_once_with(
            [b'c1', b'c2', b'c3'], b'music', b'narration', _FAKE_SCRIPT, ['#1a1a2e'],
            [{'text': 'Hola.', 'start': 0.0, 'end': 1.0}],
            skip_hook_cta_overlay=False,
        )

    def test_passes_skip_flag_when_branding_succeeds(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_clips_with_branding', return_value=([b'p', b'c1', b'c2', b'c3', b'c'], True)), \
             patch.object(gen, '_generate_music', return_value=None), \
             patch.object(gen, '_generate_narration', return_value=None), \
             patch.object(gen, '_assemble_reel', return_value=b'final-mp4') as mock_assemble, \
             patch.object(gen, '_extract_poster_frame', return_value=b'poster-png'), \
             patch.object(gen, '_upload_video_to_storage', return_value='url1'), \
             patch.object(gen, '_upload_to_storage', return_value='url2'):
            gen.generate(_FAKE_SCRIPT, ['#1a1a2e'], 'job1-day1')

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

- [ ] **Step 13: Correr toda la suite de reels**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -v 2>&1 | tail -90`
Expected: todos en PASS.

- [ ] **Step 14: Buscar y actualizar cualquier otro test que llame a `generate()`/`_generate_clips_with_branding` con `logo_url`**

Run: `grep -rn "logo_url" core/content_pipeline/tests/test_tasks.py`

Si algún test de `test_tasks.py` mockea `reel_gen.generate` verificando que reciba `logo_url=...`, actualízalo para que ya no lo espere (la llamada real en `tasks.py` ya no lo pasa, ver Step 9). Si no hay coincidencias, no hay nada que cambiar aquí.

- [ ] **Step 15: Correr la suite completa del proyecto**

Run: `docker compose exec -T backend python -m pytest`
Expected: todos en PASS.

- [ ] **Step 16: Commit**

```bash
git add core/content_pipeline/generators/reel_generator.py core/content_pipeline/tasks.py \
        core/content_pipeline/tests/test_reel_generator.py
git commit -m "feat(reels): seleccion de template por IA + fuente compartida con imagenes + quita logo del pipeline de reels"
```

---

## Task 6: Verificación real end-to-end (no delegar — la ejecuta el controlador de esta sesión)

Mismo patrón que todas las verificaciones reales anteriores de esta sesión — gasta cuota real de Veo/Imagen/Gemini y corre Node/Chrome real dentro del contenedor `rqworker` (el único con HyperFrames instalado, `backend` no).

- [ ] **Step 1: Levantar el stack con el código nuevo**

```bash
docker compose build rqworker
docker compose up -d --force-recreate --no-deps --scale rqworker=3 backend rqworker
```

- [ ] **Step 2: Generar 1 reel real por cada uno de los 3 templates, forzando la elección (sin depender de la IA) para garantizar cobertura de los 3**

Para cada uno de `panel-wipe`, `kinetic-typography`, `dynamic-background`, ejecutar dentro de `rqworker`:

```bash
docker compose exec -T rqworker python manage.py shell -c "
from unittest.mock import patch
from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
from core.content_pipeline.generators.reel_generator import ReelGenerator
from core.brand_dna.models import BrandDNA

brand = BrandDNA.objects.filter(business_name__isnull=False).exclude(business_name='').first()
script = ReelScriptGenerator().generate(
    {'caption': 'Mira como transformamos este proyecto de principio a fin'}, brand,
)
gen = ReelGenerator(bucket_name='agente-cosmic-assets')
with patch.object(gen, '_choose_reel_template', return_value='<TEMPLATE>'):
    video_url, poster_url = gen.generate(script, brand.primary_colors or ['#1a1a2e'], 'verify-template-<TEMPLATE>')
print('hook_text:', script['hook_text'])
print('tag_cta:', script['tag_cta'])
print('video_url:', video_url)
"
```

(reemplazar `<TEMPLATE>` por cada uno de los 3 nombres en las 3 corridas).

- [ ] **Step 3: Verificar cada MP4 con `ffprobe` y revisión visual**

Para cada uno de los 3 videos descargados:

```bash
ffprobe -v error -show_entries stream=codec_type,width,height,r_frame_rate -show_entries format=duration -of default=noprint_wrappers=0 <archivo>.mp4
```

Expected: duración EXACTA de 24.000000s, resolución uniforme. Extraer frames en t≈1.5 (portada) y t≈22.5 (contraportada) de cada uno y confirmar visualmente:
- El hook/CTA completo es legible, sin desbordar el cuadro (el bug original a corregir en los 2 templates que fallaron).
- Sin logo (eliminado de este plan).
- La fuente aplicada corresponde al preset que le tocó según el seed (`choose_font_preset('verify-template-<TEMPLATE>')` — nota: como `filename_prefix` no tiene `-dayN` en este caso, el seed completo es el propio `filename_prefix`; puedes verificar cuál preset le tocó corriendo `python -c "from core.shared.font_presets import choose_font_preset; print(choose_font_preset('verify-template-<TEMPLATE>'))"` dentro del contenedor).
- Contraste correcto entre texto y fondo en los 3 templates.

- [ ] **Step 4: Revisar logs por errores**

```bash
docker compose logs backend rqworker --since 15m | grep -i "hyperframes\|error\|traceback" | grep -v "INFO\|DeprecationWarning"
```

Expected: sin errores. Si el fallback a la estructura Parte A se activó en alguna de las 3 corridas, investigar con evidencia real antes de cerrar la tarea.

- [ ] **Step 5: Documentar el resultado**

Agregar una entrada nueva a `hallazgos.txt` (mismo formato que HALLAZGO 68/69/70/71) documentando: los 3 templates verificados, el fix del bug de desborde de texto, la eliminación del logo, y el resultado de la verificación real (duración, legibilidad, fuente aplicada, sin errores).

```bash
git add hallazgos.txt
git commit -m "docs(reels): HALLAZGO 72 - catalogo de 3 templates verificado en real, quita logo, corrige desborde de texto"
git push origin main
```
