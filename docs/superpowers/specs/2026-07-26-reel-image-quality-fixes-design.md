# Correcciones de calidad en imagen/reel: color de fallback, negative-prompt, contraste de subtítulos y prompts de escenas

## Objetivo

Anuar generó y revisó con Gemini 3.6 (multimodal) 7 reels reales (Trajes Dayian, Yonderly
Creativa, Clínica Fiore, Gelatinas Marba, Las Ahumadas Grill, Alitas Jessy, Crepas
Cielito) — algunos enviados a prospección, otros de las pruebas del cambio a pago de
hoy. El reporte de Gemini identificó ~15 hallazgos en 6 categorías. Un fork de
investigación cruzó cada uno contra el código real y lo ya resuelto (HALLAZGO 68/69/70/
73/77/78) para evitar duplicar trabajo. Este spec cubre los hallazgos NUEVOS y
confirmados en código, agrupados con los puntos 2 y 3 ya pendientes en
`ultimosCambios.md` (mismo código, misma naturaleza de problema).

## Fuera de alcance (decisiones ya tomadas, no se tocan aquí)

- **Portada estática del reel** (punto 4c): Anuar decidió explícitamente mantenerla sin
  cambios — "hace que el video parezca un comercial... hasta no tener evidencia de Meta
  no hagamos un cambio."
- **Ritmo/estructura rígida** (cortes cada 2.5s, misma duración siempre): ya es diseño
  deliberado y validado (HALLAZGO 70) — Gemini no tenía ese contexto al señalarlo.
- **Zoom/macro agresivo**: es un template deliberado (`_REEL_SCENE_TEMPLATES`), no se
  elimina el concepto — se cubre indirectamente en la sección F (coherencia de estilo).
- **Contraste de hook/CTA**: ya tiene `_readable_text_color` (HALLAZGO 69/70) — si los 7
  videos analizados son anteriores a ese fix, no es una regresión real. No se toca aquí.
- **CTAs débiles** (copywriting): calidad de contenido generado, no un bug de código —
  fuera de alcance de este spec de calidad visual.

## Decisiones confirmadas

### A. Color de fondo cuando no hay paleta de marca — pool de 5, no un solo valor

Hoy 3 ubicaciones usan `colors[0] if colors else '#e94560'` — el mismo rojo/coral que
usa Cosmic en sus propios correos, cayendo como "marca de agua" involuntaria. Anuar
pidió variabilidad: un pool de 5 colores del que se elige uno por generación (no un
color fijo neutro):

```python
_FALLBACK_COLOR_POOL = ['#e94560', '#3ED694', '#8B5CF6', '#F5A9C7', '#FFFFFF']
# rojo/coral actual, verde menta, morado, rosa claro, blanco
```

**Consistencia dentro de un mismo reel**: `reel_generator.py` tiene 2 sitios que derivan
`primary_color` del mismo `colors` (línea 770 dentro de `_assemble_reel`, línea 920
dentro de `generate()`) — si cada uno eligiera un color al azar de forma independiente,
la portada/CTA podría quedar de un color y las escenas de imagen de otro, dentro del
MISMO reel. Fix: resolver el fallback UNA sola vez al inicio de `generate()` (el único
punto de entrada público), reemplazando la lista `colors` vacía por
`[random.choice(_FALLBACK_COLOR_POOL)]` antes de derivar `primary_color` — así
`_assemble_reel` (que recibe `colors` como parámetro y ya hace `colors[0] if colors else
...`) hereda el mismo color resuelto sin necesitar su propio sorteo.

`image_generator.py:692` (posts single/carousel, generador completamente independiente
de un reel específico) hace su propio `random.choice(_FALLBACK_COLOR_POOL)` sin
necesidad de coordinarse con nada más.

### B. Negative-prompt de Imagen — separado por contexto (decisión explícita de Anuar)

Hoy `image_generator.py` no manda **ningún** `negative_prompt` a Imagen 3 (confirmado:
`GenerateImagesConfig` en `_generate_with_vertex` sin ese parámetro). `reel_generator.py`
sí tiene uno (`_VEO_SAFE_CONSTRAINTS`, HALLAZGO 73) pero solo cubre texto/logos/UI —
nada de anatomía, plástico, o precisión de producto, que es exactamente lo que Gemini
encontró (manos deformadas, comida con textura plástica, "Torta de Picaña" mostrando una
Tortilla Española).

Anuar eligió **listas separadas por contexto** (no una compartida) — más control fino si
algún día divergen, aceptando el costo de mantener 2 listas sincronizadas:

- **Nueva constante en `image_generator.py`**, aplicada a `_generate_with_vertex` (único
  choke point de `generate()` y `generate_carousel()`, cubre ambos):
  ```python
  _IMAGE_NEGATIVE_PROMPT = (
      "Deformed hands, extra fingers, fused fingers, mutated hands, distorted anatomy, "
      "plastic skin, oversaturated glossy texture, unrealistic reflections, incorrect "
      "product, wrong menu item, blurry, low quality."
  )
  ```
- **`_VEO_SAFE_CONSTRAINTS` de `reel_generator.py` ampliada** (mismo bloque de texto/
  logos que ya tiene, agregando los mismos términos de anatomía/plástico/producto) — se
  sigue usando tal cual en los 2 sitios que ya la consumen (`_generate_single_clip` para
  Veo, `_generate_scene_still` para Imagen), sin cambiar la mecánica de cómo se pasa.

### C. Contraste de subtítulos — caja semitransparente, no solo cambiar el color

Los subtítulos de narración (`reel_generator.py`, el único texto del reel que corre
SIEMPRE, fuera del `if REEL_TEXT_OVERLAY_ENGINE == 'playwright'`) tienen
`fontcolor=white` hardcodeado, sin ninguna relación con `_readable_text_color`. A
diferencia del hook/CTA (que se pintan sobre una caja de color sólido de marca),
los subtítulos se dibujan directo sobre el video real, sin caja — contrastar contra
`primary_color` no tendría sentido ahí porque no hay relación entre el color de marca y
lo que se ve detrás en ese frame exacto. Se sigue la propia sugerencia de Gemini: una
caja semitransparente negra detrás del texto, que garantiza contraste sin importar el
contenido del video.

### D. Safe zone de subtítulos — subir el margen 15%

`subtitle_y_offset = int(300 * scale)` hoy deja los subtítulos a ~15.6% del borde
inferior de un canvas de 1920px. Gemini pide subir 15% más — se interpreta como
incrementar el valor de la constante en 15%: `300 → 345`.

### E. `_wrap_text` — palabra huérfana en la última línea

Word-wrap greedy por conteo de caracteres (compartido por hook, CTA y subtítulos) puede
dejar una palabra corta sola en la última línea (ej. "...merece lucir su" con "su" solo).
Fix: si la última línea queda con una sola palabra, se fusiona con la línea anterior en
vez de quedar huérfana — aceptando que esa línea combinada pueda exceder ligeramente
`max_chars`, mejor que una palabra sola.

### F. `scene_prompts` — de "proceso de fabricación" a "experiencia del cliente"

La instrucción #5 del prompt de `reel_script_generator.py` (`_PROMPT`) pide hoy, para
las 5 tomas de imagen fija: *"detalles del producto/servicio, **manos trabajando**,
texturas, ambiente, resultados"*. La frase "manos trabajando" es la que dispara
imaginería industrial genérica inventada (punto 2 original — negocio de ropa mostrando
maquinaria que no se usa en confección real) porque el modelo no tiene datos reales del
proceso de fabricación de ESE negocio específico.

**Decisión de Anuar** (reformulación del problema, no captura de datos nueva): en vez de
pedir una pregunta nueva en el formulario (fricción, más tiempo de respuesta) o dejar
que Gemini infiera el proceso (riesgo de alucinación — exactamente el problema actual),
se cambia el ENFOQUE de la instrucción — de "cómo se hace" a "cómo se siente usarlo".
Sustituir "manos trabajando" por algo como *"el cliente disfrutando o recibiendo el
resultado, la sensación de satisfacción, el momento de uso"* — elimina la necesidad de
datos de proceso real sin introducir fricción ni alucinación, porque ya no se le pide al
modelo que invente algo que no sabe.

Junto con esto (mismo prompt, mismo archivo — cubre también el hallazgo de "incoherencia
de estilo visual entre tomas", ej. foto realista → render 3D → alineadores en un reel
dental), se agrega una instrucción de coherencia fotográfica: las 5 tomas deben
compartir un mismo estilo (todas fotorrealistas, o todas un mismo estilo consistente),
sin mezclar fotorrealismo con render 3D/ilustración entre tomas del mismo reel.

## Diseño técnico

### A. Pool de colores de fallback

`core/content_pipeline/generators/image_generator.py` — agregar constante de módulo y
modificar la línea 692:

```python
_FALLBACK_COLOR_POOL = ['#e94560', '#3ED694', '#8B5CF6', '#F5A9C7', '#FFFFFF']
```

```python
# antes: primary = colors[0] if colors else '#e94560'
primary = colors[0] if colors else random.choice(_FALLBACK_COLOR_POOL)
```

`core/content_pipeline/generators/reel_generator.py` — agregar la misma constante de
módulo (duplicada a propósito, sin módulo compartido entre generators hoy — mantener
sincronizada si se decide cambiar la paleta en el futuro), y modificar `generate()`
(línea ~918-920):

```python
def generate(self, script: dict, colors: list[str], filename_prefix: str) -> tuple[str, str]:
    try:
        colors = colors or [random.choice(_FALLBACK_COLOR_POOL)]
        primary_color = colors[0]
        clips, has_branding = self._generate_clips_with_branding(
            script['scene_prompts'], script['hook_text'], script['highlight_word'],
            script['tag_cta'], primary_color, filename_prefix,
        )
        ...
```

`_assemble_reel` (línea 770, `primary_color = colors[0] if colors else '#e94560'`) NO se
modifica — como `colors` ya llega resuelto (nunca vacío) desde `generate()`, esa rama
`else` queda como fallback defensivo que en la práctica nunca se activa.

### B. Negative-prompt ampliado

`core/content_pipeline/generators/image_generator.py` — agregar constante de módulo y
modificar `_generate_with_vertex` (línea 729-742):

```python
_IMAGE_NEGATIVE_PROMPT = (
    "Deformed hands, extra fingers, fused fingers, mutated hands, distorted anatomy, "
    "plastic skin, oversaturated glossy texture, unrealistic reflections, incorrect "
    "product, wrong menu item, blurry, low quality."
)
```

```python
def _generate_with_vertex(self, prompt: str) -> bytes:
    client = _vertex_client()
    model = settings.VERTEX_IMAGE_MODEL
    if 'imagen' in model:
        with track_external_api('imagen3', operation='image_generate'):
            resp = client.models.generate_images(
                model=model,
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1,
                    aspect_ratio='1:1',
                    negative_prompt=_IMAGE_NEGATIVE_PROMPT,
                    labels=vertex_labels(),
                ),
            )
        ...
```

`core/content_pipeline/generators/reel_generator.py` — ampliar `_VEO_SAFE_CONSTRAINTS`
(línea 349-355), agregando los mismos términos sin quitar los existentes:

```python
_VEO_SAFE_CONSTRAINTS = (
    "Absolutely NO text, NO letters, NO words, NO numbers, NO captions, NO subtitles, "
    "NO UI elements, NO icons, NO logos, NO play buttons, NO video player overlays, "
    "NO readable screen content anywhere in the image or video. "
    "If a screen or monitor appears, it must be blank, off, or showing only abstract "
    "blurred light — never legible text or interface elements. "
    "NO deformed hands, NO extra or fused fingers, NO mutated hands, NO distorted "
    "anatomy, NO plastic-looking skin or food, NO oversaturated glossy textures, NO "
    "unrealistic reflections, NO incorrect or mismatched product."
)
```

No se toca `_generate_single_clip` ni `_generate_scene_still` — ambos ya consumen
`self._VEO_SAFE_CONSTRAINTS` tal cual, la ampliación aplica automáticamente a los 2.

### C. Caja semitransparente en subtítulos

`core/content_pipeline/generators/reel_generator.py`, dentro del loop de subtítulos
(línea 828-832):

```python
filter_parts.append(
    f"[{last_label}]drawtext=fontfile={_DRAWTEXT_FONT_PATH}:textfile={textfile}:"
    f"fontcolor=white:fontsize={subtitle_fontsize}:borderw=3:bordercolor=black:"
    f"box=1:boxcolor=black@0.5:boxborderw=10:"
    f"x=(w-text_w)/2:y=h-{subtitle_y_offset}:"
    f"enable='between(t,{sub['start']},{sub['end']})'[{next_label}]"
)
```

Único cambio: la línea `box=1:boxcolor=black@0.5:boxborderw=10:` nueva, insertada entre
`bordercolor=black:` y `x=...`. `fontcolor=white`/`borderw=3:bordercolor=black` se
quedan igual (siguen funcionando bien sobre la caja nueva).

### D. Safe zone

`core/content_pipeline/generators/reel_generator.py`, línea 824:

```python
# antes: subtitle_y_offset = int(300 * scale)
subtitle_y_offset = int(345 * scale)
```

### E. Fix de `_wrap_text`

`core/content_pipeline/generators/reel_generator.py`, función completa (líneas 131-146):

```python
def _wrap_text(text: str, max_chars: int = 22) -> str:
    if len(text) <= max_chars:
        return text
    words = text.split()
    lines = []
    current = ''
    for word in words:
        candidate = f'{current} {word}'.strip()
        if len(candidate) > max_chars and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    # Evita dejar una sola palabra huerfana en la ultima linea (ej. "...su" solo) —
    # se fusiona con la linea anterior aunque exceda max_chars ligeramente.
    if len(lines) >= 2 and ' ' not in lines[-1]:
        lines[-2] = f'{lines[-2]} {lines[-1]}'
        lines.pop()
    return '\n'.join(lines)
```

### F. Reenfoque de `scene_prompts` + coherencia de estilo

`core/content_pipeline/generators/reel_script_generator.py`, dentro de `_PROMPT`,
instrucción #5 (línea ~55-56 del bloque actual):

```python
# antes:
"cortas e independientes (~2 segundos cada uno en el reel final) — como una rafaga "
"de tomas distintas en un comercial: detalles del producto/servicio, manos "
"trabajando, texturas, ambiente, resultados. Los 5 deben mostrar variedad visual "
"real entre si, no la misma composicion repetida. Aqui SI se prefiere el detalle de "
"precision (manos, herramientas, texturas de cerca) porque cada uno es una imagen "
"fija y no necesita coherencia fisica en el tiempo.\n"
```

```python
# despues:
"cortas e independientes (~2 segundos cada uno en el reel final) — como una rafaga "
"de tomas distintas en un comercial: detalles del producto/servicio, el cliente "
"disfrutando o recibiendo el resultado, la sensacion de satisfaccion, el momento de "
"uso, texturas, ambiente. Los 5 deben mostrar variedad visual real entre si, no la "
"misma composicion repetida, y TODOS deben compartir un mismo estilo fotografico "
"consistente (todas fotorrealistas, o todas el mismo estilo de render/ilustracion — "
"nunca mezclar fotorrealismo con render 3D o ilustracion entre tomas del mismo reel). "
"Evita escenas de proceso de fabricacion o manufactura (maquinaria, herramientas de "
"produccion) salvo que la descripcion del negocio lo mencione explicitamente — sin "
"datos reales del proceso, el modelo inventa imaginaria industrial generica no "
"creible.\n"
```

No se toca `_FALLBACK_SCENES` (líneas 15-21, usadas solo si Gemini falla al generar el
guion completo) — están redactadas de forma genérica y ya no mencionan proceso de
fabricación explícitamente salvo "Hands arranging or presenting the product" (línea 20,
presentación, no manufactura — se deja igual).

## Fuera de alcance (recordatorio)

- `_FALLBACK_SCENES` (fallback de emergencia si Gemini falla) — no se toca, ya es
  genérico y no menciona proceso de fabricación de forma problemática.
- Cualquier cambio a `brand_consistency_qc.py` — el hallazgo del reporte de Gemini
  resultó ser de negative-prompt técnico (Imagen/Veo), no de auditoría semántica de tono
  (que es lo que hace ese módulo) — mecanismos distintos, no se mezclan.
- Contraste de hook/CTA — ya cubierto por HALLAZGO 69/70, verificar en producción si
  sigue siendo un problema antes de reabrir ese trabajo.

## Testing

- **A (pool de colores)**: test que verifica `image_generator.py` elige un color del
  pool cuando `colors=[]` (mockear `random.choice`, verificar que se llama con el pool
  exacto de 5). Test en `reel_generator.py` que verifica `generate()` resuelve `colors`
  vacío a una lista de 1 elemento del pool ANTES de llamar a `_assemble_reel`, y que ese
  mismo color se usa consistentemente (mockear `random.choice`, verificar que
  `_assemble_reel` recibe `colors=[<mismo color mockeado>]`).
- **B (negative-prompt)**: test que verifica `_generate_with_vertex` pasa
  `negative_prompt=_IMAGE_NEGATIVE_PROMPT` en el `GenerateImagesConfig`. Test que
  verifica `_VEO_SAFE_CONSTRAINTS` contiene los términos nuevos (ej. `assert 'deformed
  hands' in _VEO_SAFE_CONSTRAINTS.lower()`).
- **C (caja de subtítulos)**: test que verifica el filtro de subtítulos generado
  contiene `box=1:boxcolor=black@0.5`.
- **D (safe zone)**: test que verifica `subtitle_y_offset == 345` (sin scale) o
  `int(345 * scale)` con un scale de prueba.
- **E (`_wrap_text`)**: test con un texto que reproduzca el caso real ("...merece lucir
  su") — verificar que la última línea NO es una palabra sola de 1-2 caracteres. Test de
  regresión con un texto que SÍ debe partirse en líneas parejas (no romper el
  comportamiento normal).
- **F (scene_prompts)**: test que verifica el prompt final (`_PROMPT.format(...)`)
  contiene la frase de coherencia de estilo y NO contiene "manos trabajando" — mismo
  patrón que otros tests de este archivo que verifican contenido literal del prompt
  armado.
