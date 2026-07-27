# Fidelidad de imagen/reel al producto real — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reenfocar los prompts de imagen (`image_generator.py`) hacia experiencia/sensación
en vez de producto literal, extender el auditor de logos/texto al reel (hueco real
confirmado en código), y reforzar el negative-prompt de Veo contra zoom infinito y física
de tela poco natural.

**Architecture:** 3 tareas independientes sobre 2 archivos ya existentes
(`core/content_pipeline/generators/image_generator.py`,
`core/content_pipeline/generators/reel_generator.py`) — ningún archivo nuevo, ninguna
migración, ningún import nuevo. Cada tarea es un cambio quirúrgico sobre texto de prompt o
un método de QC ya establecido en el patrón del proyecto.

**Tech Stack:** Django, Vertex AI (Imagen 3, Veo, Gemini), pytest + pytest-django,
`unittest.mock`.

## Global Constraints

- Repo: `/home/anuarbarrera/agente-cosmic/`, checkout normal de `main`, sin rama de feature.
- Todos los comandos de pytest se ejecutan dentro del contenedor:
  `docker compose exec backend <comando>`.
- **No compartir código entre `image_generator.py` y `reel_generator.py`** — este proyecto
  duplica deliberadamente constantes/métodos entre ambos generadores (ya se hizo así con
  `_FALLBACK_COLOR_POOL`/`MEXICO_TZ`) en vez de extraer un módulo compartido. `_validate_scene_still`
  (Task 2) es una copia adaptada de `_validate_background`, no una importación.
- El auditor de resultados (Task 2) **no** intenta detectar si el diseño de un producto
  coincide con la realidad — eso es explícitamente NO auditable sin fotos de referencia.
  Solo detecta: texto/logos legibles, renders 3D abstractos, contenido de pantalla, objetos
  deformes, y objetos/personas flotando sin apoyo físico.
- Cada commit usa `GIT_EDITOR=true git commit -m "mensaje"` (nunca heredoc).
- Spec completa con todo el código de referencia:
  `docs/superpowers/specs/2026-07-27-image-reel-fidelity-fixes-design.md`.

---

### Task 1: Reenfoque de `_analyze_brand_scene` — producto literal → experiencia/sensación

**Files:**
- Modify: `core/content_pipeline/generators/image_generator.py:293-297` (instrucción STEP 2 de Gemini)
- Modify: `core/content_pipeline/generators/image_generator.py:275-283` (`_FALLBACK_PROMPT`)
- Test: `core/content_pipeline/tests/test_image_generator.py`

**Interfaces:**
- Consumes: nada de otras tareas.
- Produce: nada consumido por tareas futuras — cambio de contenido de prompt autocontenido.
  `_analyze_brand_scene(caption, keywords, description, tone, colors, audience='')` mantiene
  su firma y su retorno `tuple[str, bool]` (`scene_prompt`, `product_mode`) sin cambios.

- [ ] **Step 1: Escribir los tests que fallan — agregar a `core/content_pipeline/tests/test_image_generator.py`**

Agregar una clase nueva (después de `TestValidateBackground`, línea 545, antes de
`class TestChooseTemplateForImage`):

```python
class TestAnalyzeBrandScene:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_gemini_prompt_avoids_literal_product_depiction(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"mode": "lifestyle", "prompt": "A cozy scene"}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            gen._analyze_brand_scene('Caption', ['keyword'], 'Descripcion', 'profesional', ['#1a1a2e'])
        call_kwargs = mock_vc.return_value.models.generate_content.call_args.kwargs
        gemini_prompt = call_kwargs['contents']
        assert 'focus on the product/food/objects only' not in gemini_prompt
        assert "DO NOT attempt to depict this business's exact product design" in gemini_prompt
        assert 'focus on how a customer FEELS' in gemini_prompt

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_fallback_prompt_does_not_promise_literal_product_focus(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API down')
            scene_prompt, product_mode = gen._analyze_brand_scene(
                'Caption', ['keyword'], 'Descripcion', 'profesional', ['#1a1a2e'], audience='niños'
            )
        assert product_mode is True
        assert 'Focus on the product itself.' not in scene_prompt
        assert 'artful' not in scene_prompt.lower()
        assert 'Generic/abstract representation only' in scene_prompt
```

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_image_generator.py::TestAnalyzeBrandScene -v`
Expected: FAIL — el prompt de Gemini todavía contiene `"focus on the product/food/objects only"`,
y `_FALLBACK_PROMPT` todavía contiene `"Focus on the product itself."`.

- [ ] **Step 3: Reemplazar la instrucción STEP 2 en `_analyze_brand_scene`**

En `core/content_pipeline/generators/image_generator.py`, dentro del método
`_analyze_brand_scene`, cambiar (líneas 293-297):

```python
                f"STEP 2 — Generate a background prompt (max 80 words):\n"
                f"- If risk=YES → mode=\"product\": focus on the product/food/objects only, NO people of any age, NO hands.\n"
                f"  Think: overhead flat lay, artful food arrangement, colorful props matching brand palette.\n"
                f"- If risk=NO  → mode=\"lifestyle\": real-world scene reflecting the brand's world and customers.\n"
                f"  Think: service environment, lifestyle moment, nature matching brand values. NO offices or screens.\n\n"
```

por:

```python
                f"STEP 2 — Generate a background prompt (max 80 words):\n"
                f"- If risk=YES → mode=\"product\": DO NOT attempt to depict this business's exact product design — "
                f"there is no reference photo, and a wrong specific detail (shape, topping, pattern) will look "
                f"factually incorrect to a real customer. Instead, evoke the CATEGORY generically through color, "
                f"texture, and mood: abstract close-up of textures/ingredients/materials in the brand palette, or a "
                f"generic/simple version of the product category (not an elaborate custom design). NO people of any age, NO hands.\n"
                f"- If risk=NO  → mode=\"lifestyle\": DO NOT feature this business's exact product/craft as the main "
                f"subject either — focus on how a customer FEELS after using/consuming it (satisfaction, comfort, a "
                f"genuine expression, the environment/mood of the experience), not a literal shot of the product "
                f"itself. NO offices or screens.\n\n"
```

- [ ] **Step 4: Reemplazar `_FALLBACK_PROMPT`**

En el mismo método, cambiar (líneas 275-283):

```python
        _FALLBACK_PROMPT = (
            f"Real-world {'product photography' if keyword_product_mode else 'lifestyle photograph'} inspired by: {brand_ctx[:100]}. "
            f"Natural lighting, shallow depth of field. Prominently feature the brand color palette ({color_str}) "
            f"in props, backdrop, or accent elements — the background should visibly reflect these colors, not "
            f"look like a generic neutral stock photo. Mood: {tone}. "
            f"{'NO people, NO children, NO hands. Focus on the product itself.' if keyword_product_mode else 'Authentic setting, real textures, professional photography style.'} "
            f"NO laptops, NO computers, NO phones, NO desk, NO office, NO keyboard. "
            f"NO text, NO logos, NO UI elements. Square 1:1 format. Photorealistic."
        )
```

por:

```python
        _FALLBACK_PROMPT = (
            f"Real-world {'abstract product-category texture/color composition' if keyword_product_mode else 'lifestyle photograph evoking customer satisfaction'} inspired by: {brand_ctx[:100]}. "
            f"Natural lighting, shallow depth of field. Prominently feature the brand color palette ({color_str}) "
            f"in props, backdrop, or accent elements — the background should visibly reflect these colors, not "
            f"look like a generic neutral stock photo. Mood: {tone}. "
            f"{'NO people, NO children, NO hands. Generic/abstract representation only, NOT a specific product design.' if keyword_product_mode else 'Focus on the feeling of the experience, not a literal product shot. Authentic setting, real textures, professional photography style.'} "
            f"NO laptops, NO computers, NO phones, NO desk, NO office, NO keyboard. "
            f"NO text, NO logos, NO UI elements. Square 1:1 format. Photorealistic."
        )
```

- [ ] **Step 5: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_image_generator.py -v`
Expected: PASS — todos, incluyendo los 2 nuevos. Confirmado por grep previo que ningún test
existente depende del texto viejo (`"artful"`, `"Focus on the product"`, `"product
photography"`, `"lifestyle photograph"`).

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/generators/image_generator.py core/content_pipeline/tests/test_image_generator.py
GIT_EDITOR=true git commit -m "feat(fidelidad): reenfocar prompts de imagen hacia experiencia en vez de producto literal"
```

---

### Task 2: Auditor de logos/texto — extender a `reel_generator.py`

**Files:**
- Modify: `core/content_pipeline/generators/reel_generator.py` (método nuevo `_validate_scene_still` + hook en `_generate_still_scene_clip`, líneas 429-436 y después de 670)
- Modify: `core/content_pipeline/generators/image_generator.py:372-373` (fortalecer descripción `has_text` en `_validate_background`)
- Test: `core/content_pipeline/tests/test_reel_generator.py`

**Interfaces:**
- Consumes: nada de la Task 1.
- Produce: `ReelGenerator._validate_scene_still(image_bytes: bytes) -> bool` — mismo
  contrato que `ImageGenerator._validate_background` (True = ok, False = rechazar, True en
  caso de error de API — fail-open).

- [ ] **Step 1: Escribir los tests que fallan — agregar a `core/content_pipeline/tests/test_reel_generator.py`**

Agregar una clase nueva después de `class TestGenerateSceneStill` (línea 504, antes de
`class TestChooseReelTemplate`):

```python
class TestValidateSceneStill:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_true_when_image_ok(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, "ok": true}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._validate_scene_still(b'fake-png')
        assert result is True

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_false_when_has_text(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"has_text": true, "is_abstract_3d": false, "has_screen_content": false, "ok": false}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._validate_scene_still(b'fake-png')
        assert result is False

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_true_on_api_error(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            result = gen._validate_scene_still(b'fake-png')
        assert result is True  # don't block pipeline on QC error


class TestGenerateStillSceneClipQC:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
    )
    def test_retries_once_when_qc_rejects_first_still(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_scene_still', side_effect=[b'bad-still', b'good-still']) as mock_still, \
             patch.object(gen, '_validate_scene_still', side_effect=[False, True]), \
             patch.object(gen, '_animate_still_to_clip', return_value=b'animated-clip') as mock_animate:
            result = gen._generate_still_scene_clip('a scene', 720, 1280, 24.0, duration=2.0)
        assert result == b'animated-clip'
        assert mock_still.call_count == 2
        mock_animate.assert_called_once_with(b'good-still', 720, 1280, 24.0, duration=2.0)

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
    )
    def test_uses_retry_result_even_if_retry_also_fails_qc(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_scene_still', side_effect=[b'bad-still-1', b'bad-still-2']) as mock_still, \
             patch.object(gen, '_validate_scene_still', return_value=False), \
             patch.object(gen, '_animate_still_to_clip', return_value=b'animated-clip') as mock_animate:
            result = gen._generate_still_scene_clip('a scene', 720, 1280, 24.0, duration=2.0)
        assert result == b'animated-clip'
        assert mock_still.call_count == 2
        # se acepta el resultado del reintento aunque tambien falle QC -- no se pierde la escena
        mock_animate.assert_called_once_with(b'bad-still-2', 720, 1280, 24.0, duration=2.0)

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
    )
    def test_does_not_retry_when_first_still_passes_qc(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_scene_still', return_value=b'good-still') as mock_still, \
             patch.object(gen, '_validate_scene_still', return_value=True), \
             patch.object(gen, '_animate_still_to_clip', return_value=b'animated-clip'):
            gen._generate_still_scene_clip('a scene', 720, 1280, 24.0, duration=2.0)
        assert mock_still.call_count == 1
```

Actualizar también las 2 llamadas existentes en `class TestGenerateVideoClips` que mockean
`_generate_scene_still` devolviendo bytes reales (no `None`) — con el cambio de esta tarea,
`_generate_still_scene_clip` ahora también llama `_validate_scene_still` cuando
`_generate_scene_still` no devuelve `None`, y esa llamada real a Gemini rompería estos 2
tests si no se mockea. Agregar `patch.object(gen, '_validate_scene_still',
return_value=True)` a los `with` de ambos tests:

`test_first_scene_via_veo_rest_via_imagen_zoompan` (línea 272), cambiar:
```python
        with patch.object(gen, '_generate_single_clip', return_value=b'veo-clip') as mock_veo, \
             patch.object(gen, '_probe_clip_dimensions', return_value=(720, 1280, 24.0)) as mock_probe, \
             patch.object(gen, '_generate_scene_still', return_value=b'still-bytes') as mock_still, \
             patch.object(gen, '_animate_still_to_clip', return_value=b'animated-clip') as mock_animate:
```
por:
```python
        with patch.object(gen, '_generate_single_clip', return_value=b'veo-clip') as mock_veo, \
             patch.object(gen, '_probe_clip_dimensions', return_value=(720, 1280, 24.0)) as mock_probe, \
             patch.object(gen, '_generate_scene_still', return_value=b'still-bytes') as mock_still, \
             patch.object(gen, '_validate_scene_still', return_value=True), \
             patch.object(gen, '_animate_still_to_clip', return_value=b'animated-clip') as mock_animate:
```

`test_falls_back_to_imagen_when_veo_scene_fails_completely` (línea 300), cambiar:
```python
        with patch.object(gen, '_generate_single_clip', return_value=None) as mock_veo, \
             patch.object(gen, '_probe_clip_dimensions') as mock_probe, \
             patch.object(gen, '_generate_scene_still', return_value=b'still-bytes'), \
             patch.object(gen, '_animate_still_to_clip', return_value=b'animated-clip') as mock_animate:
```
por:
```python
        with patch.object(gen, '_generate_single_clip', return_value=None) as mock_veo, \
             patch.object(gen, '_probe_clip_dimensions') as mock_probe, \
             patch.object(gen, '_generate_scene_still', return_value=b'still-bytes'), \
             patch.object(gen, '_validate_scene_still', return_value=True), \
             patch.object(gen, '_animate_still_to_clip', return_value=b'animated-clip') as mock_animate:
```

`test_skips_imagen_scene_that_fails_completely` (línea 332) **NO necesita cambio** —
`_generate_scene_still` devuelve `None` en ese test, y con `still is None or not
self._validate_scene_still(still)` el cortocircuito de `or` nunca llega a evaluar
`_validate_scene_still` cuando `still` ya es `None`.

- [ ] **Step 2: Correr los tests para confirmar que fallan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_reel_generator.py::TestValidateSceneStill core/content_pipeline/tests/test_reel_generator.py::TestGenerateStillSceneClipQC -v`
Expected: FAIL — `AttributeError: 'ReelGenerator' object has no attribute '_validate_scene_still'`.

- [ ] **Step 3: Agregar `_validate_scene_still` a `ReelGenerator`**

En `core/content_pipeline/generators/reel_generator.py`, insertar el método nuevo
inmediatamente después de `_generate_scene_still` (después de la línea 669, antes de
`def _animate_still_to_clip`):

```python
    def _validate_scene_still(self, image_bytes: bytes) -> bool:
        """Gemini reviews the generated scene still for forbidden elements. Mismo
        checklist que ImageGenerator._validate_background — duplicado aqui a proposito
        (mismo patron de este proyecto para generadores independientes)."""
        try:
            client = _vertex_client()
            image_part = types.Part.from_bytes(data=image_bytes, mime_type='image/png')
            prompt = (
                "Analyze this image strictly. Reply ONLY with this JSON (no markdown):\n"
                "{\"has_text\": <bool>, \"is_abstract_3d\": <bool>, \"has_screen_content\": <bool>, "
                "\"has_malformed_object\": <bool>, \"has_unrealistic_grounding\": <bool>, \"ok\": <bool>}\n\n"
                "has_text: true if ANY readable letters, words, numbers or text appear ANYWHERE in the image — "
                "including text on signs, labels, books, packaging, walls, or any surface — OR any logo/brand "
                "mark of any kind, even a purely graphic symbol with no letters (real or invented). Even partial "
                "words or blurry text count. Be very strict.\n"
                "is_abstract_3d: true if the image has floating 3D geometric shapes, abstract CGI objects, or surreal renders.\n"
                "has_screen_content: true if any computer monitor, laptop screen, phone screen, TV, or digital display "
                "shows visible content — including websites, text, images, graphics, UI elements, or any non-blank content. "
                "A screen must be completely BLACK or clearly turned off to not count. Be very strict.\n"
                "has_malformed_object: true if any object, tool, instrument, hand, or mechanical item is anatomically or "
                "physically impossible or distorted — wrong number of parts, parts connected incorrectly, missing pieces "
                "a real version of the object would have, or a structurally implausible shape. Examine objects with "
                "multiple connected parts (tools, instruments, hands, machinery) closely. Only flag clear, obvious cases.\n"
                "has_unrealistic_grounding: true if the main subject (person, animal, or product) appears to float, "
                "hover, or is otherwise disconnected from the surface/floor/background it should be resting or "
                "standing on — no visible contact point, no matching contact shadow directly beneath it, wrong "
                "scale or perspective versus the background, or a dynamic mid-air pose (jumping, running) composited "
                "onto a background that implies the subject is stationary. This commonly happens when a subject's "
                "pose doesn't match its new background. Only flag clear, obvious cases where it looks physically wrong.\n"
                "ok: true ONLY if has_text=false AND is_abstract_3d=false AND has_screen_content=false "
                "AND has_malformed_object=false AND has_unrealistic_grounding=false."
            )
            with track_external_api('gemini', operation='reel_scene_qc'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[image_part, prompt],
                    config=types.GenerateContentConfig(labels=vertex_labels()),
                )
            record_tokens(resp, operation='reel_scene_qc',
                          prompt_preview=prompt[:500],
                          response_preview=resp.text[:500] if resp.text else '')
            raw = resp.text.strip()
            match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
            if match:
                data = json.loads(match.group())
                ok = bool(data.get('ok', True))
                if not ok:
                    flags = [k for k in ('has_text', 'is_abstract_3d', 'has_screen_content', 'has_malformed_object', 'has_unrealistic_grounding') if data.get(k)]
                    logger.warning(f"Reel scene QC REJECTED: {', '.join(flags)} | full={data}")
                return ok
        except Exception as e:
            logger.warning(f"Reel scene QC error (assuming ok): {e}")
        return True
```

- [ ] **Step 4: Enganchar la validación en `_generate_still_scene_clip`**

En el mismo archivo, cambiar (líneas 429-436):

```python
    def _generate_still_scene_clip(self, prompt: str, width: int, height: int, fps: float,
                                    duration: float = _VEO_CLIP_DURATION_SECONDS) -> bytes | None:
        still = self._generate_scene_still(prompt)
        if still is None:
            still = self._generate_scene_still(prompt)  # 1 reintento
        if still is None:
            return None
        return self._animate_still_to_clip(still, width, height, fps, duration=duration)
```

por:

```python
    def _generate_still_scene_clip(self, prompt: str, width: int, height: int, fps: float,
                                    duration: float = _VEO_CLIP_DURATION_SECONDS) -> bytes | None:
        still = self._generate_scene_still(prompt)
        if still is None or not self._validate_scene_still(still):
            retry_still = self._generate_scene_still(prompt)
            if retry_still is not None:
                still = retry_still  # se usa el reintento aunque tambien falle QC —
                # mismo criterio que _generate_background: reintentos agotados, se
                # acepta la ultima imagen generada en vez de perder la escena completa.
        if still is None:
            return None
        return self._animate_still_to_clip(still, width, height, fps, duration=duration)
```

- [ ] **Step 5: Fortalecer `has_text` en `_validate_background` de `image_generator.py`**

En `core/content_pipeline/generators/image_generator.py`, dentro de `_validate_background`,
cambiar (líneas 372-374):

```python
                "has_text: true if ANY readable letters, words, numbers or text appear ANYWHERE in the image — "
                "including text on signs, labels, books, packaging, walls, or any surface. "
                "Even partial words or blurry text count. Be very strict.\n"
```

por:

```python
                "has_text: true if ANY readable letters, words, numbers or text appear ANYWHERE in the image — "
                "including text on signs, labels, books, packaging, walls, or any surface — OR any logo/brand "
                "mark of any kind, even a purely graphic symbol with no letters (real or invented). Even partial "
                "words or blurry text count. Be very strict.\n"
```

- [ ] **Step 6: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_reel_generator.py core/content_pipeline/tests/test_image_generator.py -v`
Expected: PASS — todos, incluyendo los 6 tests nuevos de `reel_generator` y sin
regresiones en los 3 tests existentes de `TestGenerateVideoClips`.

- [ ] **Step 7: Commit**

```bash
git add core/content_pipeline/generators/reel_generator.py core/content_pipeline/generators/image_generator.py core/content_pipeline/tests/test_reel_generator.py
GIT_EDITOR=true git commit -m "feat(fidelidad): extender auditor de logos/texto al reel + fortalecer deteccion de logos en imagen"
```

---

### Task 3: Reforzar `_VEO_SAFE_CONSTRAINTS` contra zoom infinito y física de tela

**Files:**
- Modify: `core/content_pipeline/generators/reel_generator.py` (constante de clase `_VEO_SAFE_CONSTRAINTS`)
- Test: `core/content_pipeline/tests/test_reel_generator.py`

**Interfaces:**
- Consumes: nada de las tareas 1-2.
- Produce: nada consumido por tareas futuras — la constante ya se usa automáticamente en
  `_generate_single_clip` (línea 624) y `_generate_scene_still` (línea 659), ninguna de las
  2 llamadas se modifica en esta tarea.

- [ ] **Step 1: Escribir el test que falla — extender el test existente en `core/content_pipeline/tests/test_reel_generator.py`**

Localizar `test_veo_safe_constraints_covers_anatomy_and_product_accuracy` (línea 257) y
agregar las aserciones nuevas al final del método (NO modificar las aserciones existentes):

```python
    def test_veo_safe_constraints_covers_anatomy_and_product_accuracy(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        constraints = gen._VEO_SAFE_CONSTRAINTS.lower()
        assert 'deformed hands' in constraints
        assert 'plastic' in constraints
        assert 'incorrect or mismatched product' in constraints
        assert 'infinite zoom' in constraints
        assert 'fabric' in constraints or 'cloth' in constraints
        assert 'spatial continuity' in constraints
```

- [ ] **Step 2: Correr el test para confirmar que falla**

Run: `docker compose exec backend pytest "core/content_pipeline/tests/test_reel_generator.py::TestGenerateSingleClip::test_veo_safe_constraints_covers_anatomy_and_product_accuracy" -v`
Expected: FAIL — `_VEO_SAFE_CONSTRAINTS` todavía no contiene "infinite zoom"/"fabric"/"spatial continuity".
(Confirmado: el test vive dentro de `class TestGenerateSingleClip`, línea 225 de
`test_reel_generator.py`.)

- [ ] **Step 3: Ampliar `_VEO_SAFE_CONSTRAINTS`**

En `core/content_pipeline/generators/reel_generator.py`, cambiar:

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

por:

```python
    _VEO_SAFE_CONSTRAINTS = (
        "Absolutely NO text, NO letters, NO words, NO numbers, NO captions, NO subtitles, "
        "NO UI elements, NO icons, NO logos, NO play buttons, NO video player overlays, "
        "NO readable screen content anywhere in the image or video. "
        "If a screen or monitor appears, it must be blank, off, or showing only abstract "
        "blurred light — never legible text or interface elements. "
        "NO deformed hands, NO extra or fused fingers, NO mutated hands, NO distorted "
        "anatomy, NO plastic-looking skin or food, NO oversaturated glossy textures, NO "
        "unrealistic reflections, NO incorrect or mismatched product. "
        "NO continuous or infinite zoom into a single point, NO extreme or unnatural zoom "
        "speed, NO unnatural cloth, fabric, or sheet physics — fabric must move and settle "
        "naturally under gravity, never float or fold in an impossible way. NO camera "
        "movement that breaks spatial continuity within the shot."
    )
```

- [ ] **Step 4: Correr los tests para confirmar que pasan**

Run: `docker compose exec backend pytest core/content_pipeline/tests/test_reel_generator.py -v`
Expected: PASS — todos, sin regresiones (la constante solo crece, ninguna frase existente
se elimina).

- [ ] **Step 5: Commit**

```bash
git add core/content_pipeline/generators/reel_generator.py core/content_pipeline/tests/test_reel_generator.py
GIT_EDITOR=true git commit -m "feat(fidelidad): reforzar negative-prompt de Veo contra zoom infinito y fisica de tela"
```

---

## Verificación final

Después de completar las 3 tareas, correr la suite completa del proyecto:

Run: `docker compose exec backend pytest core/ -v`
Expected: solo los fallos preexistentes ya documentados (HALLAZGO 80, flake intermitente de
`PasswordSecurityTestCase`, no relacionado a este plan); todo lo demás en verde.
