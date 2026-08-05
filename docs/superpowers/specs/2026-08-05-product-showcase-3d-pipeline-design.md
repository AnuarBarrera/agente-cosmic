# Pipeline de producto real: video-showcase 3D vía HyperFrames — Diseño

## Contexto

`ProductReferenceGenerator` (`core/content_pipeline/generators/product_reference_generator.py`,
solo-admin) pelea contra un modelo generativo caro y poco confiable: usa
Gemini para "reimaginar" la foto real de un producto en una escena nueva y
Veo para animarla, con un triage de 5 señales y un QC de escena+3 frames
para intentar contener las alucinaciones (logos inventados, texto
alucinado, objetos deformados) y los rechazos falsos-positivos ya
documentados (HALLAZGO IMG-13: la política de "cualquier marca de agua =
rechazo total" tumbó recientemente el 100% de los análisis de prueba).

Origen de la idea: Anuar investigó **breakoutclips.com** (SaaS de "videos
que rompen el scroll") y confirmó vía su documentación pública
(help.breakoutclips.com) que su ruta principal ("Create without AI") NUNCA
le pide a un modelo generativo que reinvente la foto del usuario — solo la
recorta y la **compone** dentro de una plantilla de animación 3D
pre-construida. La foto real nunca se toca ni se regenera; el "efecto wow"
viene de la plantilla, no de la IA.

Este diseño reemplaza el pipeline actual por ese mismo principio, dentro
del mismo alcance solo-admin.

## Decisiones de producto confirmadas (Anuar, esta sesión)

- **Alcance**: solo `ProductReferenceGenerator`, el pipeline solo-admin de
  "producto real como referencia". El resto del pipeline de contenido
  (tenants reales, sin foto de producto) **no se toca**.
- **Se elimina, no se parchea**: la generación de escena con Gemini
  (`_generate_scene`), la animación con Veo (`_animate_scene`), y todo el
  QC de escena/frames (`_validate_scene`, `ProductQCSchema`,
  `_QC_FRAME_OFFSETS`). No quedan como fallback — se borran.
- **Formato de salida**: solo-video. Se elimina la opción "imagen con
  producto real" (`AnalysisJob.MODE_SAMPLE_PRODUCT_IMAGE`) — igual que
  Breakout Clips, que siempre entrega animación, nunca un "modo estático"
  aparte.
- **Motor de render**: HyperFrames (ya integrado en este repo, ver
  Investigación técnica abajo), con su adaptador de Three.js — animación
  3D real, no motion graphics 2D.
- **v1 = 1 solo template**, 3D procedural con geometría primitiva de
  Three.js (sin sourcing de assets 3D externos) — el objetivo de esta
  primera versión es probar que la tubería completa funciona y que el
  resultado visual se acerca al de Breakout Clips, no construir un catálogo.
- **Piso de seguridad híbrido** (reemplaza los 5 flags del triage actual):
  se sigue rechazando (a) screenshots/capturas de UI y (b) contenido
  sensible (desnudez/violencia/etc.) — pero ya NO se rechaza por marca de
  agua, ni se distingue "producto es texto"/"persona completa"/"ya es
  profesional" (esas 3 señales existían solo para decidir entre
  ENHANCE/REGENERATE, una distinción que desaparece: ahora todo lo que
  pasa el piso de seguridad se anima igual).
- **El piso de seguridad corre en Vision, no en Gemini** (idea de Anuar,
  validada real esta sesión — ver abajo): más barato que una llamada
  generativa, y el cliente Vision ya vive en este proyecto
  (`logo_analyzer.py`, con el fix de `quota_project_id` recién llegado en
  el pull de hoy).
- **Validación empírica obligatoria antes de borrar el pipeline viejo**:
  el primer paso del plan de implementación genera un video real con el
  template nuevo usando una foto de producto real, y Anuar lo juzga
  visualmente antes de que se toque `tasks.py`/se borre código existente.

## Investigación técnica real (no solo documentación)

### HyperFrames ya está integrado en este repo — no es una dependencia nueva

Construido y en producción desde la Parte B de reels (portada/contraportada,
2026-07-16):

- `core/content_pipeline/hyperframes_reel/` — proyecto Node checked-in,
  `package.json` con `hyperframes` (`0.7.59`) y `gsap` (`3.14.2`) en
  **versión exacta, sin rango** (mismo criterio de determinismo del resto
  del pipeline de render), `package-lock.json` checked-in para `npm ci`
  reproducible, `node_modules/` gitignored.
- `Dockerfile.worker` ya instala Node.js 22 (NodeSource) y corre `npm ci`
  en ese directorio — el contenedor `rqworker` (donde corre
  `_generate_product_reference_sample`) ya tiene todo el toolchain
  disponible, cero cambio de Dockerfile necesario para este proyecto.
- Patrón de invocación ya probado en producción real
  (`reel_generator.py:_generate_branded_segment`, líneas 513-547):
  variables a JSON temporal → `subprocess.run([_HYPERFRAMES_BINARY,
  'render', '.', '-c', composition, '-o', output_path, '--variables-file',
  vars_path, '--fps', '24', '--quiet'], cwd=_HYPERFRAMES_PROJECT_DIR,
  check=True, capture_output=True, timeout=...)` → leer el MP4 resultante.
  Excepción → log + `None` (mismo criterio de fallo silencioso-mas-logueado
  que el resto del archivo).
- Métrica reutilizable tal cual: `record_hyperframes_generation(kind: str)`
  (`core/shared/metrics_utils.py:154`) — solo incrementa un contador en
  Redis por `kind`, acepta cualquier string nueva sin cambios.
- Gotchas ya documentados y a respetar: nada de red en render-time (GSAP
  vendorizado vía npm, no CDN), FPS por defecto de HyperFrames es 30 (el
  pipeline usa 24 explícito), binario local
  (`node_modules/.bin/hyperframes`), nunca `npx`.

**Falta agregar**: la dependencia `three` a `package.json` (versión exacta
a fijar en el plan) — hoy el proyecto Node solo tiene `gsap`, usado para
las composiciones 2D de portada/contraportada. No hay adaptador Three.js
instalado todavía.

### Validación real de Vision para el piso de seguridad (2026-08-05)

Se corrieron 3 imágenes reales contra `vision.ImageAnnotatorClient` con
`SAFE_SEARCH_DETECTION` + `LABEL_DETECTION` (mismo cliente ya arreglado en
`a40fcc2`, dentro del contenedor `backend` de este repo):

| Imagen | SafeSearch (adult/violence/racy) | Labels relevantes | Conclusión |
|---|---|---|---|
| `gelatina_marba_1.jpg` — foto real de producto con logo/sticker pequeño de marca | todos VERY_UNLIKELY | Food, Ingredient, Soft drink | Pasa limpio — correcto, es una foto legítima. |
| `gelopaleta_stitch.jpg` — el caso real de HALLAZGO IMG-13 (collage con título+pie de foto en texto grande superpuesto) | todos VERY_UNLIKELY/UNLIKELY | Plastic, Toy, Party Supply — **sin la etiqueta "Screenshot"** | Pasa limpio. Esto es exactamente lo que queremos: el triage viejo de Gemini rechazaba este caso (`is_screenshot_or_ui: true`, falso positivo); Vision NO lo confunde con un screenshot. Confirma que este caso puntual de IMG-13 se resuelve con el cambio de motor, no solo con eliminar el flag de marca de agua. |
| Screenshot sintético (mockup de chat estilo WhatsApp, generado con Playwright para esta prueba) | racy=POSSIBLE (ruido, ignorable), resto UNLIKELY | **"Screenshot" (0.83), "Text" (0.95), "White" (0.97)** | Vision devuelve literalmente la etiqueta `Screenshot` con confianza alta. |

**Conclusión de diseño**: el piso de seguridad completo (screenshot +
contenido sensible) se resuelve con **una sola llamada a Vision** (ambas
features en el mismo `AnnotateImageRequest`), sin ninguna dependencia de
Gemini/Vertex en este pipeline. Esto es una simplificación real, no solo
una preferencia de costo: `ProductShowcaseGenerator` queda sin ningún
cliente de IA generativa — solo Vision (clasificación) + HyperFrames
(render).

**Dato importante, no un blocker**: `gelopaleta_stitch.jpg` (foto
promocional con mucho texto superpuesto) dio `spoof=VERY_LIKELY` en
SafeSearch — el campo `spoof` de Vision está pensado para detectar
"imagen editada tipo meme". Es una **señal a NO usar** para el gate: si la
usáramos para rechazar, recreamos el mismo problema de sobre-rechazo de
HALLAZGO IMG-13 que este rediseño busca eliminar. El gate de screenshot
usa solo la etiqueta `Screenshot` (label detection), nunca `spoof`.

**Pendiente de afinar en el plan, no en el diseño**: el umbral exacto de
confianza para la etiqueta `Screenshot` y si conviene sumar etiquetas
acompañantes (`Software`, `User interface`) se decide con más muestras
reales — la prueba de hoy fue 1 imagen sintética, suficiente para probar
que el **mecanismo** funciona, no para fijar el umbral final.

## Arquitectura

```
foto subida (product_reference_photo, sin cambios en el modelo/upload)
   │
   ▼
[Gate Vision — 1 sola llamada]
   is_screenshot  → rechaza, mensaje ya existente (_describe_triage_rejection)
   is_unsafe      → rechaza, mensaje nuevo de contenido sensible
   ok             ↓
   │
   ▼
enhance_photo_classic()   — SIN CAMBIOS, reutilizado tal cual
   │
   ▼
_generate_showcase()      — NUEVO: arma variables (foto como data URL,
   │                         colores de marca) → invoca HyperFrames
   │                         (compositions/product-showcase.html, adaptador
   │                         Three.js) → MP4
   ▼
extraer poster (ffmpeg, patrón ya existente _extract_frame)
   │
   ▼
subir video + poster a GCS (patrón ya existente _upload_to_storage)
```

Sin ramas (no hay más ENHANCE vs REGENERATE) — un solo camino desde que la
foto pasa el gate de Vision.

## Componentes nuevos/modificados

### Renombrar el archivo y la clase

Casi nada del propósito original ("usa una foto real de producto como
referencia para que Gemini/Veo generen una escena nueva") sobrevive —
mantener el nombre sería engañoso. Se renombra:

- `core/content_pipeline/generators/product_reference_generator.py` →
  `core/content_pipeline/generators/product_showcase_generator.py`
- Clase `ProductReferenceGenerator` → `ProductShowcaseGenerator`
- Se mantiene el nombre del método público `generate_reel()` (mismo
  contrato de retorno `(video_url, poster_url, reason)`) para minimizar el
  cambio en `tasks.py` — sigue produciendo un reel, solo cambia cómo.
- `generate_image()` se elimina por completo (ver decisión de alcance).

### `product_showcase_generator.py` (contenido nuevo)

- Elimina: `_generate_scene`, `_animate_scene`, `_validate_scene`,
  `ProductQCSchema`, `_QC_PROMPT`, `_QC_FRAME_OFFSETS`,
  `_SCENE_PROMPT_TEMPLATE`, `_VIDEO_PROMPT_TEMPLATE`,
  `_REFERENCE_IMAGE_MODEL`, y los imports de `_vertex_client`/
  `_vertex_text_client`/`types` de `google.genai` — este archivo deja de
  depender de Vertex AI por completo.
- Nuevo: `_check_photo_safety(photo_bytes) -> str` — una llamada a
  `vision.ImageAnnotatorClient` (mismo patrón de
  `client_options={'quota_project_id': settings.GOOGLE_CLOUD_PROJECT}` que
  `logo_analyzer.py`) con `SAFE_SEARCH_DETECTION` + `LABEL_DETECTION`.
  Retorna `''` si pasa, o el mensaje de rechazo correspondiente (reutiliza
  el texto de `_describe_triage_rejection` para el caso screenshot; mensaje
  nuevo para contenido sensible). Falla-abierto en excepción (mismo
  criterio que el `_triage` actual: si Vision falla, se deja pasar y se
  loguea warning — no se bloquea el flujo completo por una falla de API).
- Nuevo: `_generate_showcase(enhanced_photo_bytes, primary_color,
  secondary_color) -> bytes | None` — arma el data URL de la foto
  (`base64`, mismo patrón que `{{bg_data_url}}` en
  `image_generator.py:757`), escribe variables a JSON temporal, invoca
  `_HYPERFRAMES_BINARY` sobre `compositions/product-showcase.html` (mismo
  patrón exacto que `_generate_branded_segment`), timeout explícito.
  Reintenta 1 vez en fallo (mismo criterio que el resto del pipeline).
- `generate_reel()` nuevo cuerpo: gate de Vision → `enhance_photo_classic`
  → `_generate_showcase` → `_extract_frame` (poster) → `_upload_to_storage`
  ×2. Mismo manejo de excepción envolvente que hoy (`try/except` genérico
  con mensaje "Ocurrió un error inesperado...").
- Se mantienen tal cual: `enhance_photo_classic` (import, sin cambios),
  `_upload_to_storage`, `_extract_frame`, `_describe_triage_rejection`
  (recortado a solo el caso screenshot).

### `core/content_pipeline/hyperframes_reel/` (proyecto Node existente)

- `package.json`: agregar `three` (versión exacta, sin `^`, a fijar en el
  plan) a `dependencies`. Regenerar `package-lock.json` (checked-in).
- Nueva composición `compositions/product-showcase.html`: root
  standalone, `data-duration` a definir en el plan (5-8s, rango similar al
  clip de Veo que reemplaza), 1080×1920. Escena Three.js procedural: un
  plano/tarjeta con la foto del producto como textura (canvas/data URL),
  rotación sutil + dolly lento de cámara, partículas o glow con el color
  de marca. Variables vía `data-composition-variables`: `photo_data_url`,
  `primary_color`, `secondary_color` — mismo mecanismo de variables nativas
  que ya usan `portada.html`/`contraportada.html` (`data-var-*` y
  `--{id}` como custom property CSS, aplicable también dentro del canvas
  Three.js vía JS leyendo la custom property).

### `core/content_pipeline/tasks.py`

- `_generate_product_reference_sample`: elimina la rama
  `else` (que llama `generate_image`) — el modo `MODE_SAMPLE_PRODUCT_IMAGE`
  deja de tener manejo especial. Import actualizado a
  `ProductShowcaseGenerator`.

### `core/brand_dna/models.py`

- `AnalysisJob.MODE_SAMPLE_PRODUCT_IMAGE` y su entrada en `MODE_CHOICES`
  se eliminan. Requiere migración (`AlterField` sobre `generation_mode`
  para actualizar `choices` — no hay que tocar filas existentes, es solo
  metadata de choices).

### `core/brand_dna/templates/brand_dna/new_analysis.html`

- Se elimina el radio `sample_product_image` (línea 120). Se mantiene
  `sample_product_reel` (línea 122-123) — evaluar si renombrar su etiqueta
  visible (hoy dice "[ADMIN] Reel con producto real"; podría pasar a
  "[ADMIN] Producto real" ya que "reel" deja de ser la única lectura
  posible, pero es un cambio cosmético menor a decidir en el plan).

## Manejo de errores

- Gate de Vision rechaza → mismo contrato de retorno que hoy
  (`('', '', mensaje)`), sin cambios en `tasks.py` más allá del import.
- Gate de Vision falla por excepción (timeout, credencial, etc.) →
  falla-abierto con warning logueado (igual que `_triage` hoy) — no se
  bloquea el pipeline completo por una falla de una API de clasificación
  barata.
- `_generate_showcase` falla (HyperFrames/Three.js truena, timeout) → 1
  reintento, luego `None` → mensaje "No se pudo generar el video. Vuelve a
  intentar." (mismo criterio que los mensajes genéricos ya existentes en
  el archivo).
- Ya no hay QC de escena/frames que pueda rechazar el resultado — al no
  haber generación con IA, no hay nada que auditar después del render (la
  foto real no cambia, la plantilla es determinista).

## Testing

- Tests de `_check_photo_safety`: mockean `vision.ImageAnnotatorClient`,
  casos screenshot/inseguro/limpio/excepción (fail-open).
- Tests de `_generate_showcase`: mockean `subprocess.run` (mismo patrón
  que `test_reel_generator.py` ya usa para `_generate_branded_segment`) —
  verifican el comando construido (ruta del binario, `-c
  compositions/product-showcase.html`, `--variables-file` con el data URL
  y los colores correctos), reintento, `None` en fallo.
- Tests de `generate_reel()`: las 2 rutas de rechazo + la ruta feliz
  completa (con todos los pasos mockeados).
- **Validación empírica real, no delegable a un test automatizado**
  (mismo criterio que la Parte B de HyperFrames): generar un video real
  con una foto de producto real (ej. `.test-photos/gelatina_marba_1.jpg`,
  ya validada contra el gate de Vision en este mismo diseño) y que Anuar
  lo juzgue visualmente — es el primer paso del plan de implementación,
  antes de tocar `tasks.py`/borrar el pipeline viejo. Si el resultado no
  convence, el pipeline actual (Gemini+Veo) sigue intacto y no se pierde
  nada en el intento.

## Nota operativa (efecto colateral encontrado y resuelto esta sesión)

El commit `d16bc0b` (traído por pull, cambia `docker-compose.yml` para
usar la cuenta de servicio de la VM vía metadata server en vez del ADC
personal) rompe la autenticación de GCP en cualquier entorno de
desarrollo que NO sea la VM real (confirmado: este entorno,
`devservermini800`, no tiene metadata server). Se creó
`docker-compose.override.yml` (gitignored, nunca se sube, no afecta la
config de la VM/producción) que restaura el mount del ADC personal solo
para `backend`/`rqworker` en este entorno local. Anuar ya re-autenticó
(`gcloud auth application-default login`) y la validación de Vision de
este mismo diseño corrió con éxito después del fix. No requiere ninguna
acción adicional, pero queda documentado por si se reproduce en otra
máquina de desarrollo.

## Fuera de alcance

- Catálogo de múltiples templates 3D (v1 es 1 solo, ver decisión de
  producto) — evaluar después de validar que el v1 convence visualmente.
- Sourcing de assets 3D externos (Sketchfab/Poly Haven) — v1 es
  procedural con Three.js puro.
- Exponer este modo a tenants reales (hoy y después de este cambio sigue
  siendo solo-admin, gateado por `allows_sample_generation` del plan
  "Admin").
- Tocar el pipeline de contenido normal (`ImageGenerator`, `ReelGenerator`,
  sin foto de producto) — sigue usando Imagen 3/Veo sin cambios.
- Afinar el umbral exacto de la etiqueta `Screenshot` con una muestra
  amplia de casos reales — se hace en el plan/Task 1, con la evidencia de
  hoy como punto de partida, no como valor final.
