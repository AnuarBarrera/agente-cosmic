# Reels: Escenas híbridas Imagen+animación / Veo — Diseño

## Contexto y problema

Anuar reportó alucinación visual en reels reales: un video mostraba manos
"atornillando" un cable eléctrico sin pelar, con movimiento de manos
físicamente incoherente. El negocio del reel SÍ era de electricidad (el tema
tiene sentido), pero la ejecución no — es el patrón conocido de los modelos
de video generativo actuales (Veo incluido): fallan sistemáticamente en
manipulación fina de objetos con las manos (herramientas, cables, tornillos,
cualquier acción de precisión), porque requiere coherencia física entre
frames que estos modelos no garantizan.

Causa raíz encontrada en el código, no solo en el resultado: el prompt de
`reel_script_generator.py` (líneas 32-37) le pide explícitamente a Gemini
que las 3 escenas de video **prefieran** "manos trabajando" — empuja
directo hacia el tipo de toma que más falla en Veo.

Motivación adicional de Anuar, explícita: "mientras más reduzcamos el uso
de Veo reducimos los costos y las alucinaciones" — el costo es un driver
tan real como la calidad. Veo cuesta ~$0.10/seg (~$0.80 por clip de 8s);
Imagen cuesta $0.04/imagen fija — un orden de magnitud menos.

## Decisión de producto (Anuar, explícita)

- De las 3 escenas de un reel, **1 se queda como video real de Veo** (la de
  apertura/ambiente), **2 pasan a ser imagen fija (Imagen) animada con pan/
  zoom** (efecto Ken Burns). No es clasificación automática por escena
  (esa opción se evaluó y se difirió) — es una regla fija por posición.
- La animación de las imágenes fijas se hace con el filtro nativo `zoompan`
  de ffmpeg, **no con HyperFrames**. HyperFrames no reduce costo (ninguna
  de las dos opciones llama una API de pago para animar) y agrega carga de
  cómputo (Chrome headless) al mismo servidor compartido donde ya corren
  gunicorn + 3 rqworkers + ffmpeg — el mismo tipo de riesgo de
  confiabilidad que ya se vivió y se resolvió con Playwright en este mismo
  pipeline (ver `project_cosmic_reels.md`: migración de hook/CTA de
  Playwright a drawtext nativo). HyperFrames se mantiene reservado para la
  idea separada de portada/contraportada, donde su capacidad de animación
  más rica (no el costo) es lo que aporta valor.
- Las escenas que pasan a Imagen fija **ganan permiso para ser más
  detalladas/de precisión** (manos, herramientas, texturas de cerca) — el
  contenido que hoy causa alucinación se redirige al modelo que sí lo
  resuelve bien (una imagen fija no necesita coherencia física en el
  tiempo).

## Arquitectura

```
reel_script_generator.py (guion)
  scene_prompts[0] → "seguro para Veo": plano amplio/ambiente, movimiento
                      de camara, SIN manipulacion precisa de manos
  scene_prompts[1,2] → "para Imagen fija": SI se permite/prefiere detalle
                        de precision (manos, herramientas, texturas)

reel_generator.py (orquestador)
  1. Genera clip de Veo para scene_prompts[0] (Veo real, como hoy)
  2. Prueba (ffprobe) la resolucion/fps REAL del clip de Veo obtenido
     (Veo no garantiza 1080x1920 para aspect_ratio='9:16' - ya documentado
     que veo-3.0-fast-generate-001 devolvio 720x1280 en produccion real)
  3. Para scene_prompts[1] y [2]: genera imagen fija con Imagen
     (aspect_ratio='9:16'), luego la anima con zoompan de ffmpeg
     normalizada EXACTAMENTE a la resolucion/fps medida en el paso 2
  4. Devuelve list[bytes] de hasta 3 clips de 8s, mismo contrato que hoy
     (_assemble_reel, concat -c copy, y el resto del pipeline no cambian)
```

El contrato de salida de `_generate_video_clips` (`list[bytes]`, hasta 3
elementos de 8s c/u, mismo codec/resolucion/fps entre si) no cambia — solo
cambia CÓMO se produce cada elemento de la lista. `_assemble_reel`,
`_probe_video_width` (reutilizado, no reemplazado), el ensamblaje ffmpeg
final, subtítulos, música, narración y overlay de texto **no se tocan**.

## Componentes nuevos

### `reel_script_generator.py` — prompt actualizado

El punto 5 del `_PROMPT` (`scene_prompts`) se reescribe para diferenciar
por índice en vez de una regla única para las 3. Mismo schema de salida
(lista de 3 strings) — solo cambia el contenido de la instrucción:

- `scene_prompts[0]`: "para un GENERADOR DE VIDEO — plano amplio o de
  ambiente con movimiento de camara (push-in, pan, rotacion). NO debe
  incluir manipulacion precisa de objetos con las manos (atornillar,
  cablear, cortar, ensamblar en primer plano) porque el generador de video
  falla en coherencia fisica de manos con herramientas."
- `scene_prompts[1]` y `[2]`: "para un GENERADOR DE IMAGEN FIJA — aqui SI
  se prefiere el detalle de precision: manos trabajando con herramientas,
  texturas de cerca, el oficio en accion, porque es una imagen fija y no
  necesita coherencia fisica en el tiempo."

Las 3 mantienen la regla existente de evitar pantallas/laptops/monitores
con contenido, y terminan con `'no text, no logos, no people speaking to
camera.'`. `_FALLBACK_SCENES` no cambia — ya es genérico/seguro para
cualquiera de los dos usos (ninguna de las 3 describe manos con
herramientas).

### `reel_generator.py` — generación de escena fija + animación

Dos métodos nuevos en `ReelGenerator`:

- **`_generate_scene_still(prompt: str) -> bytes | None`** — llama a
  Imagen (`client.models.generate_images`, mismo cliente/patrón que
  `image_generator.py::_generate_with_vertex`, `settings.VERTEX_IMAGE_MODEL`,
  `aspect_ratio='9:16'`, `number_of_images=1`). 1 reintento si falla (mismo
  patrón que otros generadores del pipeline), `None` tras el segundo fallo.
  Métrica: `track_external_api('imagen3', operation='image_generate')` +
  `record_imagen_generation('reel_scene')`.
- **`_animate_still_to_clip(image_bytes: bytes, width: int, height: int,
  fps: float, duration: float = _VEO_CLIP_DURATION_SECONDS) -> bytes`** —
  escribe la imagen a un archivo temporal, corre ffmpeg con el filtro
  `zoompan` (zoom lento 1.0→1.08 o pan sutil, a definir en el plan) escalado
  a `width`x`height` y `fps` exactos, duración `duration` segundos, mismo
  códec de salida (H.264) que produce Veo. Sin llamadas a API — cómputo
  local.
- **`_probe_video_dimensions(video_path: str) -> tuple[int, int, float]`**
  — extiende el patrón de `_probe_video_width` existente para devolver
  también `height` y `fps` (vía `ffprobe`), necesarios para normalizar los
  clips de Imagen+zoompan al clip real de Veo.

`_generate_video_clips(scene_prompts: list[str]) -> list[bytes]` se
reescribe para orquestar: clip 0 vía Veo (como hoy, con su reintento
existente) → probe de dimensiones reales → clips 1 y 2 vía
`_generate_scene_still` + `_animate_still_to_clip`, normalizados a esas
dimensiones. Si el clip de Veo (escena 0) falla tras su reintento, esa
escena también se genera vía Imagen+zoompan en vez de perderse (usando
dimensiones por defecto ya que no hay clip de Veo del cual medir) — el
reel ya no depende de tener necesariamente 1 clip de Veo real, solo de
llegar a 3 clips totales.

## Manejo de errores

- **Falla Veo (escena 0):** 1 reintento (igual que hoy). Si falla dos
  veces, se genera esa escena también como Imagen+zoompan en vez de
  omitirla — preserva el piso de "3 clips o se aborta" sin depender de que
  Veo funcione.
- **Falla Imagen (escenas 1/2):** 1 reintento. Si falla dos veces, se omite
  esa escena — mismo comportamiento de tolerancia a clips faltantes que ya
  existe (`generate()` aborta el reel completo si `len(clips) < 3`, sin
  cambios en ese umbral).
- **Normalización de resolución:** si por alguna razón no hay ningún clip
  de Veo real en el reel (Veo falló en la escena 0 y se sustituyó), los
  clips de Imagen+zoompan usan una resolución por defecto fija (1080x1920,
  el estándar 9:16 que Veo debería dar pero no siempre da) en vez de
  intentar probar un clip que no existe.

## Testing

- `test_reel_script_generator.py`: el guion generado usa el rol correcto
  por índice (test de contenido del prompt enviado a Gemini, no del
  resultado — mismo patrón que los tests existentes de este archivo).
- `test_reel_generator.py`: `_generate_scene_still` (éxito, reintento,
  fallo tras reintento — mismo patrón que `TestGenerateVideoClips`
  existente), `_animate_still_to_clip` (produce un clip con la
  duración/resolución/fps pedidos — verificable vía `ffprobe` sobre el
  resultado, igual que hace `TestExtractPosterFrame`), `_probe_video_dimensions`
  (extiende el test existente de `_probe_video_width`),
  `_generate_video_clips` (orquestación: escena 0 vía Veo mockeado,
  escenas 1-2 vía Imagen+zoompan mockeados, normalización a la resolución
  del clip de Veo; y el caso de fallback cuando Veo falla del todo).
- Tests existentes de `_assemble_reel`, concat, subtítulos, overlay de
  texto: sin cambios, ya que el contrato de `_generate_video_clips`
  (`list[bytes]` de clips uniformes) no cambia.

## Costo estimado por reel

Hoy: 3 × Veo 8s ≈ 3 × $0.80 = **$2.40**.
Con el cambio: 1 × Veo 8s + 2 × Imagen ≈ $0.80 + 2 × $0.04 = **$0.88**
(~63% de reducción), más la eliminación de 2/3 del riesgo de alucinación
de movimiento (solo 1 escena sigue siendo video generado).

## Fuera de alcance (explícitamente diferido)

- **Clasificación automática por escena** (Opción C evaluada durante el
  brainstorm): decidir dinámicamente cuál escena es "segura para Veo" en
  vez de una regla fija por posición. Se difiere hasta validar la regla
  fija en producción real.
- **HyperFrames** para animar las imágenes fijas — se usa `zoompan` nativo
  de ffmpeg en su lugar (ver "Decisión de producto" arriba). HyperFrames
  queda reservado para la idea separada de portada/contraportada
  (`project_quality_roadmap_ideas.md`, idea #3).
- **Reducir aún más las escenas de Veo** (ej. 0 clips de Veo, 100% Imagen)
  — Anuar explícitamente pidió mantener 1 clip real de Veo para que el
  reel no se sienta 100% estático.
