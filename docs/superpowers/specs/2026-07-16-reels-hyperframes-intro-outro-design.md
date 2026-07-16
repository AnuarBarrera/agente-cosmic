# Reels: Portada/Contraportada con HyperFrames (Parte B) — Diseño

## Contexto

Parte A (shots cortos de imagen, construida y verificada 2026-07-16) dejó el
reel en 18s (8s Veo + 5×2s Imagen), 6s más corto que el diseño original de
24s. Al revisar un reel real de la Parte A, Anuar notó que el audio (TTS)
se corta abruptamente en el segundo 18. Causa raíz confirmada en el código:
el guion pide narraciones de "~15-20 segundos hablados" (rango calibrado
para el reel de 24s original, con margen), y `_assemble_reel` corta el
audio final exactamente en `-t {duration}` sin compensar si la narración
dura más que el video. Con el cuerpo en 18s, una narración de 19-20s se
corta a media palabra.

Esta Parte B resuelve ambos problemas a la vez: agrega portada (3s) y
contraportada (3s) usando HyperFrames — devolviendo el reel a 24s totales
(el margen original de la narración) y agregando intro/outro de marca con
mejor tipografía y movimiento, la idea 3 ya guardada en memoria desde el
2026-07-15.

## Decisiones de producto (Anuar, explícitas)

- **Estructura de tiempo:** portada 3s + cuerpo (Veo 8s + 5 shots de imagen
  de 2s = 18s, sin cambios de la Parte A) + contraportada 3s = **24s
  total**.
- **Contenido:** la portada incluye el **hook** (texto + palabra resaltada,
  y el logo de marca si existe); la contraportada incluye el **CTA**
  (texto, y el logo si existe). El hook/CTA dejan de dibujarse sobre el
  cuerpo del video cuando la portada/contraportada existen — quedan
  "horneados" en esos segmentos.
- **Herramienta:** HyperFrames real (CLI de Node.js), no una reutilización
  de Playwright — evaluado y descartado explícitamente en el brainstorm
  anterior (Playwright ya cubre la necesidad de tipografía en el cuerpo;
  HyperFrames se reserva para la animación de marca más rica del
  intro/outro).
- **Sin cacheo por marca:** se regenera portada/contraportada en cada reel
  (Anuar aceptó explícitamente el riesgo de correr Node/Chrome headless en
  cada generación).
- **Fallback:** si portada o contraportada falla (tras 1 reintento cada
  una), se abandonan AMBAS para ese reel — el reel vuelve a la estructura
  de la Parte A (18s, hook/CTA sobre el cuerpo vía el motor de texto
  activo, `REEL_TEXT_OVERLAY_ENGINE`) sin código de fallback nuevo.
- **Narración desde t=0:** para que la Parte B realmente resuelva el corte
  de audio, la narración/música deben sonar durante los 24s completos
  (portada+cuerpo+contraportada), no solo durante el cuerpo.

## Investigación técnica real (no solo documentación — CLI probado en vivo)

- `hyperframes` es un CLI de Node.js (`node --version` requerido: 22+).
  Comando real: `hyperframes render <dir> -c compositions/<archivo>.html
  -o <salida.mp4> --variables-file <json>`.
- **Variables nativas** (mejor que el reemplazo de texto manual usado hoy
  para Playwright): `data-composition-variables` en `<html>` declara el
  esquema; `--variables-file` pasa los valores reales en cada render.
  `data-var-text="id"` sustituye el texto de un elemento directamente;
  cada variable escalar se aplica automáticamente como `--{id}` (CSS
  custom property), así que los colores se consumen con `var(--primary_color)`
  sin JS adicional.
- **Confirmado con un render real de prueba** (`hyperframes render . -c
  compositions/portada.html -o renders/p.mp4 --variables-file vars.json`):
  produce un MP4 real, duración exacta a `data-duration`, variables de
  texto y color aplicadas correctamente. 2 composiciones standalone
  (`compositions/portada.html`, `compositions/contraportada.html`) en un
  mismo proyecto, cada una con su propio `data-composition-id`, renderizan
  sin conflicto vía `-c`.
- **Riesgo de red identificado:** el proyecto scaffolded por defecto carga
  GSAP desde `cdn.jsdelivr.net` — la propia documentación de HyperFrames
  advierte contra "render-time network fetches for required assets:
  inline or pre-bundle them". Confirmado que instalar `gsap` como
  dependencia npm normal y referenciarlo por ruta relativa
  (`../node_modules/gsap/dist/gsap.min.js`) en vez del CDN funciona
  igual de bien sin depender de red en el render.
- **`npx` vs binario local:** `npx hyperframes@<version>` resuelve el
  paquete por red en cada invocación (lento, punto de fallo). Para
  producción: `hyperframes` como dependencia fija de `package.json`
  (versión exacta, sin rango — mismo criterio de determinismo que el
  resto del pipeline de render), instalada una vez con `npm ci` al
  construir la imagen Docker, invocada luego como binario local
  (`node_modules/.bin/hyperframes`), sin red en cada render.
- **FPS por defecto es 30, no 24** (el resto del pipeline usa 24 o el fps
  real medido de Veo) — hay que normalizar explícitamente.

## Arquitectura

```
core/content_pipeline/hyperframes_reel/     (nuevo, checked-in al repo)
  package.json          — hyperframes + gsap, versiones EXACTAS (sin ^)
  package-lock.json     — checked-in, para npm ci reproducible
  node_modules/          — gitignored, instalado en el Dockerfile
  compositions/
    portada.html         — hook + logo opcional, 1080x1920, 3s
    contraportada.html   — CTA + logo opcional, 1080x1920, 3s

Dockerfile.worker (modificado)
  + instalar Node.js 22 (NodeSource) + `npm ci` en hyperframes_reel/
  (backend NO lo necesita — no genera reels)

reel_generator.py (orquestador, modificado)
  1. Genera clip de Veo (escena 0) — sin cambios
  2. Mide resolucion/fps real (sin cambios, _probe_clip_dimensions)
  3. Genera 5 shots de Imagen+zoompan — sin cambios de la Parte A
  4. NUEVO: intenta generar portada + contraportada via HyperFrames
     (1 reintento cada una)
     - Si AMBAS tienen exito: normaliza cada una (ffmpeg scale) a la
       resolucion/fps real medida, las antepone/agrega a la lista de
       clips, y le indica a _assemble_reel que NO dibuje hook/CTA sobre
       el cuerpo (ya estan horneados)
     - Si CUALQUIERA falla: se descartan ambas, clips = [veo, shot1..5]
       (igual que Parte A), _assemble_reel dibuja hook/CTA como hoy
```

## Componentes nuevos

### Composiciones HyperFrames

- `compositions/portada.html`: root `data-duration="3"` `data-width="1080"`
  `data-height="1920"`. Variables: `hook_text` (string), `highlight_word`
  (string, para aplicar estilo de resaltado igual que hoy en
  `reel_hook.html`), `primary_color` (color), `text_color` (color,
  calculado en Python con la MISMA `_readable_text_color()` de HALLAZGO 69
  — no se recalcula en JS), `logo_url` (string, vacío si no hay logo).
  Un script de inicialización (leído UNA vez, no animado por frame, per
  `variables-and-media.md`) oculta el elemento del logo si `logo_url`
  llega vacío. GSAP vendorizado, sin red en render.
- `compositions/contraportada.html`: mismo patrón, variables `cta_text`,
  `primary_color`, `text_color`, `logo_url`. `data-duration="3"`.

### `reel_generator.py`

- Nueva constante `_BRANDED_SEGMENT_DURATION_SECONDS = 3.0` y
  `_HYPERFRAMES_PROJECT_DIR` (ruta al proyecto checked-in).
- Nuevo método `_generate_branded_segment(kind: str, text: str,
  highlight_word: str, primary_color: str, logo_url: str) -> bytes | None`
  — construye el JSON de variables (incluye `text_color` calculado con
  `_readable_text_color(primary_color)`), lo escribe a un archivo
  temporal, invoca el binario local de HyperFrames vía `subprocess.run`
  con `-c compositions/{portada,contraportada}.html` según `kind`, timeout
  explícito, 1 reintento si falla. Lee el MP4 resultante.
- `_generate_video_clips` (o un nuevo método orquestador que lo envuelve)
  gana la lógica: después de generar Veo+shots, intenta portada+contraportada;
  si ambas OK, las normaliza (mismo patrón `_probe_video_dimensions` +
  ffmpeg scale ya usado) y las antepone/agrega a la lista de clips.
- `_assemble_reel` gana un parámetro (ej. `skip_hook_cta_overlay: bool`)
  que, cuando es `True`, omite por completo la rama de
  `_build_hook_filter_parts`/`_build_cta_filter_parts`/`_render_text_overlay_playwright`
  — el resto (subtítulos, música, narración) no cambia.
- `generate()` gana parámetros `logo_url: str = ''` (ya que hoy no recibe
  `brand_dna` ni logo) — el call site en `tasks.py::_generate_post_media`
  pasa `brand_dna.logo_url`.

### Métricas

Nueva métrica `record_hyperframes_generation(kind)` (mismo patrón que
`record_playwright_overlay_fallback`) para medir tasa de éxito/fallback de
portada/contraportada en producción real — visibilidad necesaria dado que
es la primera vez que se corre Node/Chrome headless en este pipeline desde
el punto de vista de confiabilidad.

## Manejo de errores

- Cada segmento (portada, contraportada) se intenta 1 vez + 1 reintento
  (mismo patrón que Veo/Imagen).
- Si CUALQUIERA de los dos falla tras su reintento: se descartan AMBOS
  (no tiene sentido un reel con solo portada o solo contraportada) — el
  reel cae a la estructura de la Parte A completa, sin excepciones nuevas
  que propagar, `generate()` sigue su flujo normal.
- Timeout explícito por render (HyperFrames + Chrome headless puede ser
  lento en frío) — valor a determinar empíricamente en el plan, con logs
  suficientes para diagnosticar si el fallback se activa seguido en
  producción real.

## Testing

- Tests de `_generate_branded_segment`: mockean `subprocess.run` (mismo
  patrón que el resto del pipeline — no se invoca HyperFrames real en
  tests), verifican el comando construido (ruta del binario, `-c`,
  `--variables-file` con el contenido JSON correcto incluyendo
  `text_color` calculado), reintento, y `None` en fallo.
- Tests de orquestación: ambas ramas (éxito → clips incluyen portada+contraportada,
  `skip_hook_cta_overlay=True`; fallo → clips como Parte A,
  `skip_hook_cta_overlay=False`).
- Tests de `_assemble_reel` con `skip_hook_cta_overlay=True`: verifican
  que NO se llama a `_build_hook_filter_parts`/`_build_cta_filter_parts`/
  `_render_text_overlay_playwright`.
- Verificación real end-to-end (no delegada, mismo patrón que Partes
  anteriores): generar un reel real completo, confirmar duración exacta
  24.0s vía `ffprobe`, revisar visualmente portada/contraportada (logo
  presente y ausente — probar ambos casos) y que el audio ya no se corta.

## Fuera de alcance

- Cachear portada/contraportada por marca (explícitamente descartado por
  Anuar en el brainstorm).
- Animaciones más allá de lo que las composiciones iniciales definan —
  iterar sobre el diseño visual es una mejora posterior, no bloqueante
  para esta Parte B.
- Cambiar la duración del cuerpo (Veo/shots) — se hereda tal cual de la
  Parte A.
