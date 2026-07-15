# Reels: Shots cortos de imagen (Parte A) — Diseño

## Contexto

El mismo día (2026-07-15) se construyó un pipeline híbrido donde la escena 0 de
un reel se genera con Veo (video real) y las escenas 1-2 con Imagen fija +
animación `zoompan` de ffmpeg, cada una durando 8s (`_VEO_CLIP_DURATION_SECONDS`,
reutilizado como duración de TODOS los clips). Al revisar el resultado real,
Anuar señaló que las imágenes duran demasiado (8s) para el efecto que busca:
como Imagen cuesta ~$0.04/imagen (vs. ~$0.80/clip de Veo), prefiere **más
imágenes, más cortas** (2s cada una) — imitando el ritmo de corte rápido de
publicidad real ("ráfaga de shots").

Esta es la Parte A de un rediseño mayor de reels que también incluye portada/
contraportada con HyperFrames (Parte B) — decidido explícitamente en el
brainstorm dividir en 2 ciclos independientes de spec+plan+implementación,
porque son cambios separables y la Parte B es más grande/riesgosa (herramienta
nueva, Node.js en el contenedor). Esta spec cubre **solo la Parte A**.

## Decisiones de producto (Anuar, explícitas)

- El reel sigue durando ~24s como target final, pero **en esta Parte A el
  total baja a 18s temporalmente** — los 6s restantes los agrega la Parte B
  (portada/contraportada). No se rellena con más shots ahora porque eso
  significaría rehacer el guion y los tests cuando llegue la Parte B.
- La escena de Veo se mantiene en 8s — el plano amplio/con movimiento de
  cámara real se beneficia de más tiempo en pantalla; el ritmo rápido es
  específicamente para las imágenes fijas.
- 5 shots de imagen de 2s cada uno (10s total), cada uno una **imagen
  distinta** generada por Imagen — no se reutiliza una sola imagen con
  distintos encuadres de `zoompan`. Más variedad visual, más fiel al ritmo
  real de un comercial con múltiples tomas, y el costo extra (5 × $0.04 =
  $0.20) sigue siendo marginal frente a Veo.

## Arquitectura

```
reel_script_generator.py (guion)
  scene_prompts[0]   → rol "seguro para Veo" (sin cambios de la Parte
                        anterior: plano amplio/ambiente, sin manos de
                        precision)
  scene_prompts[1-5] → rol "shot de Imagen fija", 5 escenas DISTINTAS,
                        cada una un shot corto e independiente (variedad
                        real entre ellas)

reel_generator.py (orquestador)
  1. Genera clip de Veo para scene_prompts[0] (sin cambios: intento +
     reintento, fallback a Imagen+zoompan a 8s si falla del todo)
  2. Mide resolucion/fps real del clip de Veo (sin cambios,
     _probe_clip_dimensions/_probe_video_dimensions ya existentes)
  3. Para scene_prompts[1] a [5]: genera imagen fija + zoompan, cada una
     de _IMAGE_SHOT_DURATION_SECONDS (2s) en vez de 8s
  4. _assemble_reel ya no calcula duration = len(clips)*8 (formula
     invalida con duraciones mixtas) — mide la duracion REAL del video
     concatenado con ffprobe (_probe_video_duration, nueva)
```

El contrato de `_generate_video_clips` (`list[bytes]`) no cambia — sigue
siendo agnóstico a cuántos clips hay o cuánto dura cada uno. El umbral
`len(clips) < 3` en `generate()` tampoco cambia (con 1 Veo + 5 Imagen, el
mínimo real para no abortar sigue siendo holgado).

## Componentes modificados

### `reel_script_generator.py`

El punto 5 de `_PROMPT` pasa de pedir exactamente 3 `scene_prompts` a pedir
**6**, con roles diferenciados:

- `scene_prompts[0]`: sin cambios respecto al diseño anterior — "para un
  GENERADOR DE VIDEO... plano amplio o de ambiente con movimiento de
  camara... NO debe incluir manipulacion precisa de objetos con las manos".
- `scene_prompts[1]` a `scene_prompts[5]`: "para un GENERADOR DE IMAGEN
  FIJA, cada uno un shot corto e independiente (~2 segundos en el reel
  final) — como una rafaga de tomas distintas en un comercial: detalles
  del producto/servicio, manos trabajando, texturas, ambiente, resultados.
  Los 5 deben mostrar variedad visual real entre si, no la misma
  composicion repetida. Aqui SI se prefiere el detalle de precision
  (manos, herramientas, texturas de cerca) porque cada uno es una imagen
  fija y no necesita coherencia fisica en el tiempo."

`_FALLBACK_SCENES` crece de 3 a 6 entradas genéricas (mismo criterio que
las 3 actuales: seguras para cualquiera de los 2 roles). El código que
valida `len(scene_prompts) != 3` pasa a validar `!= 6`.

### `reel_generator.py`

- Nueva constante `_IMAGE_SHOT_DURATION_SECONDS = 2.0`, junto a
  `_VEO_CLIP_DURATION_SECONDS = 8`.
- `_generate_still_scene_clip(prompt, width, height, fps, duration=...)`
  gana el parámetro `duration` (hoy usa el default de
  `_animate_still_to_clip`, que es `_VEO_CLIP_DURATION_SECONDS`) — se le
  pasa explícitamente `_IMAGE_SHOT_DURATION_SECONDS` para las escenas 1-5,
  y `_VEO_CLIP_DURATION_SECONDS` para el caso de fallback de la escena 0
  (cuando Veo falla del todo y esa escena también se genera vía Imagen).
- `_generate_video_clips` actualizado: itera `scene_prompts[1:]` (ahora 5
  elementos, antes 2) pasando `duration=_IMAGE_SHOT_DURATION_SECONDS` en
  cada llamada a `_generate_still_scene_clip`.
- Nueva función module-level `_probe_video_duration(video_path: str) ->
  float` (mismo patrón que `_probe_video_width`, vía `ffprobe
  -show_entries format=duration`).
- `_assemble_reel`: la línea `duration = len(clips) *
  _VEO_CLIP_DURATION_SECONDS` se reemplaza por `duration =
  _probe_video_duration(concat_path)`, llamada justo después del paso de
  concat (mismo punto donde ya se llama `_probe_video_width(concat_path)`
  para el ancho). El resto de `_assemble_reel` (cálculo de `cta_start`,
  filtros de hook/CTA/subtítulos, ensamblaje final) no cambia — todos ya
  consumen la variable `duration`, no `len(clips)` directamente.

## Manejo de errores

Sin cambios respecto al diseño anterior — mismo patrón de reintento (1
intento + 1 reintento) para Veo e Imagen, mismo fallback de escena 0 a
Imagen si Veo falla del todo, mismo umbral de abortar el reel si quedan
menos de 3 clips totales.

## Testing

- `test_reel_script_generator.py`: el test que verifica el prompt
  diferenciado por rol se actualiza a `scene_prompts[0]` (Veo) y
  `scene_prompts[1]`...`scene_prompts[5]` (Imagen) en vez de solo
  `[0]`/`[1]`/`[2]`. Los tests que verifican el conteo de escenas
  (`len(result['scene_prompts']) == 3`) pasan a `== 6`.
- `test_reel_generator.py`:
  - `TestGenerateVideoClips`: los 3 tests de orquestación (feliz, fallback
    de Veo, fallo de Imagen) se actualizan a una lista de 6 `scene_prompts`
    y verifican que las 5 llamadas a `_generate_still_scene_clip`/
    `_animate_still_to_clip` reciben `duration=2.0` (no 8.0).
  - Nueva clase `TestProbeVideoDuration` (mismo patrón que
    `TestProbeVideoDimensions`): mockea `subprocess.run` para `ffprobe`,
    verifica que se parsea correctamente el `stdout` de
    `format=duration`.
  - `TestAssembleReel`: el helper compartido `_fake_ffmpeg_run` (usado por
    todos los tests de esta clase) necesita distinguir la llamada de
    `ffprobe` para ancho (`stream=width`) de la nueva llamada de
    `ffprobe` para duración (`format=duration`) — hoy responde un `stdout`
    fijo para cualquier `ffprobe`. Se actualiza para inspeccionar el
    comando y responder distinto según cuál de las 2 sea. Los asserts que
    dependían de `duration=24` (ej. ventanas `enable='between(t,21.0,24.0)'`
    del CTA) se actualizan al nuevo valor de duración fijado por el mock
    (recomendado: seguir usando 24.0 en el mock para no tener que tocar
    esos asserts, ya que el valor real ahora lo decide el mock, no
    `len(clips)`).

## Fuera de alcance (Parte B, spec/plan separados)

- Portada y contraportada con HyperFrames.
- Reubicar el hook/CTA dentro de la portada/contraportada (siguen
  renderizándose con `drawtext` sobre el cuerpo del reel, sin cambios, en
  esta Parte A).
- Cualquier cambio a la duración total objetivo de 24s — esta Parte A dejaría
  el reel en 18s hasta que la Parte B se implemente.
