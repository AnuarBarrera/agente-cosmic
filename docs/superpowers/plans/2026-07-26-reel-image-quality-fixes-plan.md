# Correcciones de calidad en imagen/reel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corregir 6 hallazgos de calidad confirmados en la generación de imagen/reel
(color de fallback sin variabilidad, cero negative-prompt en Imagen, subtítulos
ilegibles, subtítulos fuera de la safe zone, palabras huérfanas en el wrap de texto, y
`scene_prompts` que invitan a alucinar procesos de fabricación genéricos).

**Architecture:** 6 cambios quirúrgicos e independientes entre sí sobre 3 archivos ya
existentes (`image_generator.py`, `reel_generator.py`, `reel_script_generator.py`) — sin
archivos nuevos, sin cambios de arquitectura, cada uno con su propia cobertura de test
nueva (ninguno modifica tests existentes, todos confirmados sin conflicto).

**Tech Stack:** Django, Vertex AI (Imagen 3 vía `google.genai`), ffmpeg (drawtext),
pytest — mismo stack que el resto del pipeline de contenido.

## Global Constraints

- Pool de colores de fallback (idéntico en los 2 archivos que lo necesitan):
  `['#e94560', '#3ED694', '#8B5CF6', '#F5A9C7', '#FFFFFF']`.
- `image_generator.py` y `reel_generator.py` ya importan `random` — no agregar el import.
- El negative-prompt de `image_generator.py` y el de `reel_generator.py`
  (`_VEO_SAFE_CONSTRAINTS`) son **listas separadas** — no unificar en un solo lugar
  compartido (decisión explícita de Anuar).
- La caja de subtítulos usa exactamente `box=1:boxcolor=black@0.5:boxborderw=10:` — no
  cambiar la opacidad ni el padding sin que el spec lo pida.
- `subtitle_y_offset`: `300` → `345` (incremento del 15%, no un valor distinto).
- No tocar `_FALLBACK_SCENES` (`reel_script_generator.py`), `_assemble_reel`'s propia
  línea de fallback de color (`reel_generator.py:770`), ni `brand_consistency_qc.py`.
- Ningún test existente debe modificarse — todos los cambios de este plan solo agregan
  cobertura nueva (verificado contra `test_image_generator.py`, `test_reel_generator.py`,
  `test_reel_script_generator.py` antes de escribir este plan).

---

### Task 1: `image_generator.py` — pool de color + negative-prompt

**Files:**
- Modify: `core/content_pipeline/generators/image_generator.py:692` (color),
  `core/content_pipeline/generators/image_generator.py:729-746` (`_generate_with_vertex`)
- Test: `core/content_pipeline/tests/test_image_generator.py`

**Interfaces:**
- Produce: constantes de módulo `_FALLBACK_COLOR_POOL` y `_IMAGE_NEGATIVE_PROMPT` en
  `image_generator.py` — no consumidas por otras tareas de este plan (cada archivo tiene
  las suyas).

- [ ] **Step 1: Confirmar el código actual**

Leer `core/content_pipeline/generators/image_generator.py` líneas 692 y 729-746, y
confirmar que coinciden exactamente con:

```python
        primary = colors[0] if colors else '#e94560'
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
                        labels=vertex_labels(),
                    ),
                )
            if resp.generated_images:
                record_imagen_generation('generate')
                return resp.generated_images[0].image.image_bytes
            raise ValueError("No image returned by Imagen")
        with track_external_api('gemini'):
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=['IMAGE', 'TEXT'],
                    labels=vertex_labels(),
                ),
            )
        record_tokens(resp)
        for part in resp.candidates[0].content.parts:
            if part.inline_data:
                return part.inline_data.data
        raise ValueError("No image returned by Vertex AI")
```

Si difiere, DETENERSE y reportar `NEEDS_CONTEXT`.

- [ ] **Step 2: Escribir los tests que fallan**

Agregar a `core/content_pipeline/tests/test_image_generator.py`, dentro de la clase que
contiene `test_uses_fallback_button_color_when_no_colors` (usar el mismo patrón de mocks
de esa prueba — `_make_mock_playwright`, `patch.object(ImageGenerator,
'_choose_template_for_image', ...)`):

```python
    def test_uses_color_pool_for_primary_color_when_no_colors(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator, _FALLBACK_COLOR_POOL
        gen = ImageGenerator(bucket_name='test-bucket')
        fake_bg = _png_bytes()
        fake_shot = _png_bytes(size=(1080, 1080))
        content = {'headline': 'Título', 'subtitle': 'Subtítulo', 'cta': 'Empieza', 'tag': 'TEST'}
        mock_pw, mock_page = self._make_mock_playwright(fake_shot)

        with patch('core.content_pipeline.generators.image_generator.sync_playwright', return_value=mock_pw), \
             patch.object(ImageGenerator, '_choose_template_for_image', return_value='instagram_post.html'), \
             patch('core.content_pipeline.generators.image_generator.random.choice', return_value='#3ED694') as mock_choice:
            gen._render_html_template(fake_bg, content, [])

        mock_choice.assert_called_once_with(_FALLBACK_COLOR_POOL)
        html_arg = mock_page.set_content.call_args[0][0]
        assert '#3ED694' in html_arg
```

Agregar como función de módulo suelta (sin `self`, sin clase contenedora) en la sección
"Existing tests" cerca de `test_generate_returns_url` (línea 22) — ese es el patrón real
del archivo para tests de `generate()`/`_generate_with_vertex` (funciones de módulo con
`@override_settings`, NO dentro de una clase `Test...`):

```python
@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
)
def test_generate_with_vertex_passes_negative_prompt_for_imagen():
    from core.content_pipeline.generators.image_generator import ImageGenerator, _IMAGE_NEGATIVE_PROMPT
    gen = ImageGenerator(bucket_name='test-bucket')
    mock_client = MagicMock()
    mock_client.models.generate_images.return_value = MagicMock(
        generated_images=[MagicMock(image=MagicMock(image_bytes=b'fake-png'))]
    )
    with patch('core.content_pipeline.generators.image_generator._vertex_client', return_value=mock_client):
        gen._generate_with_vertex('a test prompt')

    call_kwargs = mock_client.models.generate_images.call_args.kwargs
    assert call_kwargs['config'].negative_prompt == _IMAGE_NEGATIVE_PROMPT
```

- [ ] **Step 3: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_image_generator.py -k "color_pool_for_primary or negative_prompt_for_imagen" -v`

Expected: FAIL — `ImportError: cannot import name '_FALLBACK_COLOR_POOL'` / `'_IMAGE_NEGATIVE_PROMPT'`.

- [ ] **Step 4: Implementar los 2 cambios**

Agregar cerca del inicio del archivo (junto a otras constantes de módulo existentes,
después de los imports):

```python
_FALLBACK_COLOR_POOL = ['#e94560', '#3ED694', '#8B5CF6', '#F5A9C7', '#FFFFFF']

_IMAGE_NEGATIVE_PROMPT = (
    "Deformed hands, extra fingers, fused fingers, mutated hands, distorted anatomy, "
    "plastic skin, oversaturated glossy texture, unrealistic reflections, incorrect "
    "product, wrong menu item, blurry, low quality."
)
```

Reemplazar la línea 692:

```python
        primary = colors[0] if colors else random.choice(_FALLBACK_COLOR_POOL)
```

Reemplazar el `GenerateImagesConfig(...)` dentro de `_generate_with_vertex`:

```python
                    config=types.GenerateImagesConfig(
                        number_of_images=1,
                        aspect_ratio='1:1',
                        negative_prompt=_IMAGE_NEGATIVE_PROMPT,
                        labels=vertex_labels(),
                    ),
```

- [ ] **Step 5: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_image_generator.py -v`

Expected: todos los tests del archivo PASAN (los 2 nuevos + los ya existentes sin
ninguna modificación, incluyendo `test_uses_fallback_button_color_when_no_colors` que
sigue intacto porque usa `_pick_button_color`, una función distinta).

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/generators/image_generator.py core/content_pipeline/tests/test_image_generator.py
git commit -m "feat(reel-quality): pool de color de fallback + negative-prompt en image_generator"
```

---

### Task 2: `reel_generator.py` — pool de color consistente + negative-prompt ampliado

**Files:**
- Modify: `core/content_pipeline/generators/reel_generator.py` (método `generate()`,
  líneas ~918-921; constante `_VEO_SAFE_CONSTRAINTS`, líneas 349-355)
- Test: `core/content_pipeline/tests/test_reel_generator.py`

**Interfaces:**
- Produce: constante de módulo `_FALLBACK_COLOR_POOL` en `reel_generator.py` (duplicada
  a propósito de la de `image_generator.py`, sin módulo compartido — ver Global
  Constraints).

- [ ] **Step 1: Confirmar el código actual**

Leer `core/content_pipeline/generators/reel_generator.py` y confirmar que `generate()`
coincide con:

```python
    def generate(self, script: dict, colors: list[str], filename_prefix: str) -> tuple[str, str]:
        try:
            primary_color = colors[0] if colors else '#e94560'
            clips, has_branding = self._generate_clips_with_branding(
                script['scene_prompts'], script['hook_text'], script['highlight_word'],
                script['tag_cta'], primary_color, filename_prefix,
            )
            if len(clips) < 3:
```

y que `_VEO_SAFE_CONSTRAINTS` coincide con:

```python
    _VEO_SAFE_CONSTRAINTS = (
        "Absolutely NO text, NO letters, NO words, NO numbers, NO captions, NO subtitles, "
        "NO UI elements, NO icons, NO logos, NO play buttons, NO video player overlays, "
        "NO readable screen content anywhere in the image or video. "
        "If a screen or monitor appears, it must be blank, off, or showing only abstract "
        "blurred light — never legible text or interface elements."
    )
```

Si difiere, DETENERSE y reportar `NEEDS_CONTEXT`.

- [ ] **Step 2: Escribir los tests que fallan**

Agregar a `core/content_pipeline/tests/test_reel_generator.py`, en la clase/bloque que
contiene los tests de `generate()` (cerca de `test_returns_video_and_poster_urls_on_success`,
mismo patrón de mocks):

```python
    def test_resolves_empty_colors_to_same_pool_color_for_clips_and_assembly(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator, _FALLBACK_COLOR_POOL
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_clips_with_branding', return_value=([b'c1', b'c2', b'c3'], False)) as mock_clips, \
             patch.object(gen, '_generate_music', return_value=None), \
             patch.object(gen, '_generate_narration', return_value=None), \
             patch.object(gen, '_assemble_reel', return_value=b'final-mp4') as mock_assemble, \
             patch.object(gen, '_extract_poster_frame', return_value=b'poster-png'), \
             patch.object(gen, '_upload_video_to_storage', return_value='url1'), \
             patch.object(gen, '_upload_to_storage', return_value='url2'), \
             patch('core.content_pipeline.generators.reel_generator.random.choice', return_value='#8B5CF6') as mock_choice:
            gen.generate(_FAKE_SCRIPT, [], 'job1-day1')

        mock_choice.assert_called_once_with(_FALLBACK_COLOR_POOL)
        assert mock_clips.call_args.args[4] == '#8B5CF6'
        assert mock_assemble.call_args.args[4] == ['#8B5CF6']
```

Agregar cerca de los tests de `_VEO_SAFE_CONSTRAINTS`/negative_prompt existentes
(`test_negative_prompt_passed_via_config_not_appended_to_prompt`, línea ~213):

```python
    def test_veo_safe_constraints_covers_anatomy_and_product_accuracy(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        constraints = gen._VEO_SAFE_CONSTRAINTS.lower()
        assert 'deformed hands' in constraints
        assert 'plastic' in constraints
        assert 'incorrect or mismatched product' in constraints
```

- [ ] **Step 3: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_reel_generator.py -k "resolves_empty_colors or veo_safe_constraints_covers" -v`

Expected: `test_resolves_empty_colors_to_same_pool_color_for_clips_and_assembly` FALLA
(`mock_clips.call_args.args[4] == '#e94560'`, no `'#8B5CF6'` — el fallback viejo sigue
activo). `test_veo_safe_constraints_covers_anatomy_and_product_accuracy` FALLA
(`AssertionError`, los términos nuevos no existen todavía).

- [ ] **Step 4: Implementar los 2 cambios**

Agregar cerca del inicio del archivo (junto a otras constantes de módulo):

```python
_FALLBACK_COLOR_POOL = ['#e94560', '#3ED694', '#8B5CF6', '#F5A9C7', '#FFFFFF']
```

Reemplazar el inicio de `generate()`:

```python
    def generate(self, script: dict, colors: list[str], filename_prefix: str) -> tuple[str, str]:
        try:
            colors = colors or [random.choice(_FALLBACK_COLOR_POOL)]
            primary_color = colors[0]
            clips, has_branding = self._generate_clips_with_branding(
                script['scene_prompts'], script['hook_text'], script['highlight_word'],
                script['tag_cta'], primary_color, filename_prefix,
            )
            if len(clips) < 3:
```

Reemplazar `_VEO_SAFE_CONSTRAINTS`:

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

- [ ] **Step 5: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_reel_generator.py -v`

Expected: todos los tests del archivo PASAN — incluyendo
`test_negative_prompt_passed_via_config_not_appended_to_prompt` (línea 213) y el test
equivalente de la línea 449, que comparan contra `gen._VEO_SAFE_CONSTRAINTS.strip()`
dinámicamente y por tanto siguen pasando sin modificarlos. Todos los tests de `generate()`
que pasan `colors=['#1a1a2e']` (no vacío) tampoco cambian de comportamiento — `colors or
[...]` deja `colors` intacto cuando ya tiene contenido.

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/generators/reel_generator.py core/content_pipeline/tests/test_reel_generator.py
git commit -m "feat(reel-quality): pool de color consistente + negative-prompt ampliado en reel_generator"
```

---

### Task 3: `reel_generator.py` — caja de subtítulos + safe zone

**Files:**
- Modify: `core/content_pipeline/generators/reel_generator.py` (loop de subtítulos
  dentro de `_assemble_reel`, líneas 823-834)
- Test: `core/content_pipeline/tests/test_reel_generator.py`

**Interfaces:**
- Ninguna nueva — cambio interno de `_assemble_reel`, ya cubierto por sus tests
  existentes de subtítulos.

- [ ] **Step 1: Confirmar el código actual**

Leer `core/content_pipeline/generators/reel_generator.py` líneas 823-834 y confirmar que
coinciden con:

```python
            subtitle_fontsize = max(1, int(_SUBTITLE_FONTSIZE * scale))
            subtitle_y_offset = int(300 * scale)
            for i, sub in enumerate(subtitles or []):
                next_label = f'sub{i}'
                textfile = _write_drawtext_textfile(tmp, f'{next_label}.txt', _wrap_text(sub['text']))
                filter_parts.append(
                    f"[{last_label}]drawtext=fontfile={_DRAWTEXT_FONT_PATH}:textfile={textfile}:"
                    f"fontcolor=white:fontsize={subtitle_fontsize}:borderw=3:bordercolor=black:"
                    f"x=(w-text_w)/2:y=h-{subtitle_y_offset}:"
                    f"enable='between(t,{sub['start']},{sub['end']})'[{next_label}]"
                )
                last_label = next_label
```

Si difiere, DETENERSE y reportar `NEEDS_CONTEXT`.

- [ ] **Step 2: Escribir el test que falla**

Agregar a `core/content_pipeline/tests/test_reel_generator.py`, en la clase
`TestAssembleReel`, justo después de `test_adds_drawtext_filters_for_subtitles`:

```python
    @override_settings(REEL_TEXT_OVERLAY_ENGINE='drawtext')
    def test_subtitle_filter_has_contrast_box_and_higher_safe_zone(self, tmp_path):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        subtitles = [{'text': 'Tu negocio en linea.', 'start': 0.0, 'end': 2.5}]
        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=_fake_ffmpeg_run(fake_output)) as mock_run:
            gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                script=_FAKE_SCRIPT_FOR_ASSEMBLE, colors=['#1a1a2e'],
                subtitles=subtitles,
            )

        overlay_cmd = mock_run.call_args_list[3].args[0]
        filter_complex_idx = overlay_cmd.index('-filter_complex')
        filter_complex = overlay_cmd[filter_complex_idx + 1]
        assert 'box=1:boxcolor=black@0.5' in filter_complex
        assert 'y=h-345' in filter_complex
```

- [ ] **Step 3: Correr el test para confirmar que falla**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_reel_generator.py -k test_subtitle_filter_has_contrast_box_and_higher_safe_zone -v`

Expected: FAIL — `AssertionError` (`'box=1:boxcolor=black@0.5' not in filter_complex`, y
`'y=h-300'` en vez de `'y=h-345'`).

- [ ] **Step 4: Implementar el cambio**

Reemplazar el bloque completo (líneas 823-834):

```python
            subtitle_fontsize = max(1, int(_SUBTITLE_FONTSIZE * scale))
            subtitle_y_offset = int(345 * scale)
            for i, sub in enumerate(subtitles or []):
                next_label = f'sub{i}'
                textfile = _write_drawtext_textfile(tmp, f'{next_label}.txt', _wrap_text(sub['text']))
                filter_parts.append(
                    f"[{last_label}]drawtext=fontfile={_DRAWTEXT_FONT_PATH}:textfile={textfile}:"
                    f"fontcolor=white:fontsize={subtitle_fontsize}:borderw=3:bordercolor=black:"
                    f"box=1:boxcolor=black@0.5:boxborderw=10:"
                    f"x=(w-text_w)/2:y=h-{subtitle_y_offset}:"
                    f"enable='between(t,{sub['start']},{sub['end']})'[{next_label}]"
                )
                last_label = next_label
```

- [ ] **Step 5: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_reel_generator.py -v`

Expected: todos los tests del archivo PASAN — incluyendo
`test_adds_drawtext_filters_for_subtitles` y `test_omits_subtitle_filters_when_no_subtitles`
sin modificarlos (usan `in filter_complex`, no comparación exacta, así que la caja nueva
no los rompe).

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/generators/reel_generator.py core/content_pipeline/tests/test_reel_generator.py
git commit -m "feat(reel-quality): caja de contraste + safe zone mas alta para subtitulos"
```

---

### Task 4: `reel_generator.py` — fix de palabra huérfana en `_wrap_text`

**Files:**
- Modify: `core/content_pipeline/generators/reel_generator.py:131-146` (`_wrap_text`)
- Test: `core/content_pipeline/tests/test_reel_generator.py`

**Interfaces:**
- Ninguna nueva — `_wrap_text(text: str, max_chars: int = 22) -> str` mantiene su
  firma exacta, usada sin cambios por `_build_hook_filter_parts`,
  `_build_cta_filter_parts`, y el loop de subtítulos (Task 3).

- [ ] **Step 1: Confirmar el código actual**

Leer `core/content_pipeline/generators/reel_generator.py` líneas 131-146 y confirmar que
coincide con:

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
    return '\n'.join(lines)
```

Si difiere, DETENERSE y reportar `NEEDS_CONTEXT`.

- [ ] **Step 2: Escribir el test que falla**

Agregar a `core/content_pipeline/tests/test_reel_generator.py`, en la clase que contiene
`test_wraps_long_text_into_two_lines`:

```python
    def test_merges_orphaned_single_word_into_previous_line(self):
        text = 'creemos que cada caballero merece lucir su'
        result = _wrap_text(text, max_chars=20)
        lines = result.split('\n')
        assert ' ' in lines[-1]  # ninguna linea final queda con 1 sola palabra
        assert 'su' in lines[-1]
```

- [ ] **Step 3: Correr el test para confirmar que falla**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_reel_generator.py -k test_merges_orphaned_single_word_into_previous_line -v`

Expected: FAIL — la última línea es `'su'` sola (`' ' in lines[-1]` es `False`).

- [ ] **Step 4: Implementar el fix**

Reemplazar la función completa:

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

- [ ] **Step 5: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_reel_generator.py -v`

Expected: todos los tests del archivo PASAN — incluyendo `test_returns_unchanged_when_short`
y `test_wraps_long_text_into_two_lines` (verificado manualmente: su última línea ya tiene
5 palabras, la condición de fusión no se activa, comportamiento idéntico).

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/generators/reel_generator.py core/content_pipeline/tests/test_reel_generator.py
git commit -m "fix(reel-quality): _wrap_text ya no deja palabras huerfanas en la ultima linea"
```

---

### Task 5: `reel_script_generator.py` — scene_prompts: experiencia del cliente + coherencia de estilo

**Files:**
- Modify: `core/content_pipeline/generators/reel_script_generator.py` (constante
  `_PROMPT`, instrucción #5)
- Test: `core/content_pipeline/tests/test_reel_script_generator.py`

**Interfaces:**
- Ninguna nueva — `_PROMPT` sigue siendo una constante de módulo con el mismo
  placeholder de formato (`{business_name}`, `{caption}`, `{tone}`, `{description}`).

- [ ] **Step 1: Confirmar el código actual**

Leer `core/content_pipeline/generators/reel_script_generator.py` y confirmar que el
fragmento de la instrucción #5 dentro de `_PROMPT` coincide con:

```python
    "5. scene_prompts: exactamente 6 prompts EN INGLES describiendo 6 escenas visuales "
    "relacionadas al negocio, con roles DISTINTOS por posicion:\n"
    "   - scene_prompts[0]: para un GENERADOR DE VIDEO. Debe ser un plano amplio o de "
    "ambiente con movimiento de camara (push-in, pan lento, rotacion suave). NO debe "
    "incluir manipulacion precisa de objetos con las manos (atornillar, cablear, cortar, "
    "ensamblar, escribir a mano en primer plano) porque el generador de video falla en "
    "coherencia fisica de manos con herramientas entre frames.\n"
    "   - scene_prompts[1] a scene_prompts[5]: para un GENERADOR DE IMAGEN FIJA, 5 shots "
    "cortos e independientes (~2 segundos cada uno en el reel final) — como una rafaga "
    "de tomas distintas en un comercial: detalles del producto/servicio, manos "
    "trabajando, texturas, ambiente, resultados. Los 5 deben mostrar variedad visual "
    "real entre si, no la misma composicion repetida. Aqui SI se prefiere el detalle de "
    "precision (manos, herramientas, texturas de cerca) porque cada uno es una imagen "
    "fija y no necesita coherencia fisica en el tiempo.\n"
```

Si difiere, DETENERSE y reportar `NEEDS_CONTEXT` — el resto de la instrucción #5 (líneas
sobre pantallas/laptops y el nombre del negocio, HALLAZGO 77) NO se toca, solo el
fragmento de scene_prompts[1-5] citado arriba.

- [ ] **Step 2: Escribir el test que falla**

Agregar a `core/content_pipeline/tests/test_reel_script_generator.py`, junto a
`test_prompt_differentiates_veo_scene_from_imagen_scenes` (mismo patrón de mocks y
fixture `brand_dna`):

```python
def test_prompt_avoids_manufacturing_process_and_requires_style_consistency(brand_dna):
    from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
    post_data = {'caption': 'Bolsos artesanales hechos a mano'}
    response_json = (
        '{"hook_text":"H","highlight_word":"H","tag_cta":"C","narration_script":"N",'
        '"scene_prompts":["s1","s2","s3","s4","s5","s6"],"music_mood":"M"}'
    )
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        ReelScriptGenerator().generate(post_data, brand_dna)

    sent_prompt = mock_vc.return_value.models.generate_content.call_args.kwargs['contents']
    assert 'manos trabajando' not in sent_prompt
    assert 'cliente disfrutando' in sent_prompt or 'sensacion de satisfaccion' in sent_prompt
    assert 'mismo estilo fotografico consistente' in sent_prompt
```

- [ ] **Step 3: Correr el test para confirmar que falla**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_reel_script_generator.py -k test_prompt_avoids_manufacturing_process_and_requires_style_consistency -v`

Expected: FAIL — `'manos trabajando' not in sent_prompt` es `False` (la frase vieja
todavía está ahí), y los otros 2 asserts también fallan (frases nuevas no existen).

- [ ] **Step 4: Implementar el cambio**

Reemplazar el fragmento completo de la instrucción #5 citado en el Step 1 por:

```python
    "5. scene_prompts: exactamente 6 prompts EN INGLES describiendo 6 escenas visuales "
    "relacionadas al negocio, con roles DISTINTOS por posicion:\n"
    "   - scene_prompts[0]: para un GENERADOR DE VIDEO. Debe ser un plano amplio o de "
    "ambiente con movimiento de camara (push-in, pan lento, rotacion suave). NO debe "
    "incluir manipulacion precisa de objetos con las manos (atornillar, cablear, cortar, "
    "ensamblar, escribir a mano en primer plano) porque el generador de video falla en "
    "coherencia fisica de manos con herramientas entre frames.\n"
    "   - scene_prompts[1] a scene_prompts[5]: para un GENERADOR DE IMAGEN FIJA, 5 shots "
    "cortos e independientes (~2 segundos cada uno en el reel final) — como una rafaga "
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

- [ ] **Step 5: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_reel_script_generator.py -v`

Expected: todos los tests del archivo PASAN — incluyendo
`test_prompt_differentiates_veo_scene_from_imagen_scenes` (verifica `scene_prompts[0]`,
`GENERADOR DE VIDEO`, `scene_prompts[1] a scene_prompts[5]`, `GENERADOR DE IMAGEN FIJA`,
`5 shots`, `NO debe incluir manipulacion precisa` — todos fuera del fragmento
reemplazado, sin cambios).

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/generators/reel_script_generator.py core/content_pipeline/tests/test_reel_script_generator.py
git commit -m "feat(reel-quality): scene_prompts prioriza experiencia del cliente sobre proceso de fabricacion"
```
