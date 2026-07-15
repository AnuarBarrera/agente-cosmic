# Reels: Shots cortos de imagen (Parte A) — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cambiar los reels de 2 shots de imagen de 8s cada uno a 5 shots de imagen de 2s cada uno (imitando el ritmo de corte rápido de publicidad real), corrigiendo el cálculo de duración total del reel para que soporte clips de duración mixta.

**Architecture:** El guion pasa de pedir 3 `scene_prompts` a pedir 6 (1 rol Veo + 5 rol Imagen). `_generate_video_clips` genera la escena 0 vía Veo (sin cambios) y las escenas 1-5 vía Imagen+zoompan a `_IMAGE_SHOT_DURATION_SECONDS` (2s) cada una. `_assemble_reel` mide la duración real del video concatenado con `ffprobe` en vez de calcularla como `len(clips) * 8`.

**Tech Stack:** Vertex AI (Veo, Imagen), ffmpeg (`ffprobe`), Django, pytest.

## Global Constraints

- `_IMAGE_SHOT_DURATION_SECONDS = 2.0` para cada shot de imagen (1 a 5). `_VEO_CLIP_DURATION_SECONDS = 8` sin cambios, sigue aplicando a la escena 0 (Veo o su fallback a Imagen).
- El guion debe seguir devolviendo un JSON con las mismas 6 claves (`hook_text`, `highlight_word`, `tag_cta`, `narration_script`, `scene_prompts`, `music_mood`) — solo cambia que `scene_prompts` pasa de 3 a 6 elementos.
- `_generate_video_clips` sigue devolviendo `list[bytes]` — el contrato no cambia, solo cuántos elementos hay y cuánto dura cada uno.
- `_assemble_reel` NUNCA debe volver a calcular `duration` a partir de `len(clips)` — siempre debe medirse con `ffprobe` sobre el video ya concatenado.
- No se toca `_probe_video_width` (se sigue usando tal cual para el ancho). No se toca el umbral `len(clips) < 3` en `generate()`.
- Fuera de alcance (Parte B, spec/plan separados): portada/contraportada con HyperFrames, reubicar hook/CTA fuera de `drawtext`.

---

## Task 1: Guion — 6 escenas (1 Veo + 5 Imagen)

**Files:**
- Modify: `core/content_pipeline/generators/reel_script_generator.py`
- Test: `core/content_pipeline/tests/test_reel_script_generator.py`

**Interfaces:**
- Consumes: nada nuevo.
- Produces: nada que la Tarea 2 consuma directamente — son independientes (la
  Tarea 2 solo necesita que `scene_prompts` sea una lista de 6 strings,
  sin importar su contenido exacto).

- [ ] **Step 1: Escribir los tests que fallan**

Abre `core/content_pipeline/tests/test_reel_script_generator.py` y aplica estos
4 cambios exactos:

**1a.** En `test_generate_returns_fallback_on_api_error`, cambia:
```python
    assert len(result['scene_prompts']) == 3
```
por:
```python
    assert len(result['scene_prompts']) == 6
```

**1b.** En `test_generate_parses_valid_gemini_response`, reemplaza el bloque
`response_json` completo:
```python
    response_json = (
        '{"hook_text":"Bolsos que cuentan tu historia","highlight_word":"historia",'
        '"tag_cta":"Compra ahora","narration_script":"Cada bolso es unico, hecho a mano con materiales de la mas alta calidad.",'
        '"scene_prompts":["scene1, no text, no logos, no people speaking to camera.",'
        '"scene2, no text, no logos, no people speaking to camera.",'
        '"scene3, no text, no logos, no people speaking to camera."],'
        '"music_mood":"warm acoustic, artisanal feel"}'
    )
```
por:
```python
    response_json = (
        '{"hook_text":"Bolsos que cuentan tu historia","highlight_word":"historia",'
        '"tag_cta":"Compra ahora","narration_script":"Cada bolso es unico, hecho a mano con materiales de la mas alta calidad.",'
        '"scene_prompts":["scene1, no text, no logos, no people speaking to camera.",'
        '"scene2, no text, no logos, no people speaking to camera.",'
        '"scene3, no text, no logos, no people speaking to camera.",'
        '"scene4, no text, no logos, no people speaking to camera.",'
        '"scene5, no text, no logos, no people speaking to camera.",'
        '"scene6, no text, no logos, no people speaking to camera."],'
        '"music_mood":"warm acoustic, artisanal feel"}'
    )
```
Y cambia:
```python
    assert len(result['scene_prompts']) == 3
```
por:
```python
    assert len(result['scene_prompts']) == 6
```

**1c.** En `test_generate_uses_fallback_scenes_when_gemini_returns_wrong_count`,
cambia:
```python
    assert len(result['scene_prompts']) == 3
```
por:
```python
    assert len(result['scene_prompts']) == 6
```
(el `response_json` de este test ya envía solo `["solo una escena"]` — sigue
siendo un conteo incorrecto sin importar el nuevo número exacto, no hace
falta tocarlo).

**1d.** Reemplaza el test completo `test_prompt_differentiates_veo_scene_from_imagen_scenes`:
```python
@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_prompt_differentiates_veo_scene_from_imagen_scenes(brand_dna):
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
    assert 'scene_prompts[0]' in sent_prompt
    assert 'GENERADOR DE VIDEO' in sent_prompt
    assert 'scene_prompts[1] a scene_prompts[5]' in sent_prompt
    assert 'GENERADOR DE IMAGEN FIJA' in sent_prompt
    assert '5 shots' in sent_prompt
    assert 'NO debe incluir manipulacion precisa' in sent_prompt
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_script_generator.py -v`
Expected: FAIL — el código actual todavía pide 3 escenas y no menciona
`scene_prompts[1] a scene_prompts[5]` ni `5 shots`.

- [ ] **Step 3: Actualizar `_FALLBACK_SCENES` (de 3 a 6 entradas)**

En `core/content_pipeline/generators/reel_script_generator.py`, reemplaza:
```python
_FALLBACK_SCENES = [
    "Overhead flat lay of the product on a clean surface with soft natural light, slow push-in camera movement, no people, no text, no logos.",
    "Close-up detail shot of the product with shallow depth of field, gentle rotation, warm bokeh background, no text, no logos.",
    "Product displayed in a lifestyle setting with soft ambient light, subtle camera pan, no people, no text, no logos.",
]
```
por:
```python
_FALLBACK_SCENES = [
    "Overhead flat lay of the product on a clean surface with soft natural light, slow push-in camera movement, no people, no text, no logos.",
    "Close-up detail shot of the product with shallow depth of field, gentle rotation, warm bokeh background, no text, no logos.",
    "Product displayed in a lifestyle setting with soft ambient light, no people, no text, no logos.",
    "Macro shot of texture and materials up close, soft directional light, shallow depth of field, no text, no logos.",
    "Hands arranging or presenting the product on a clean surface, natural light, no text, no logos.",
    "Wide clean studio shot of the product centered with soft shadow, minimal background, no text, no logos.",
]
```

- [ ] **Step 4: Reescribir `_PROMPT` para 6 escenas**

Reemplaza la constante `_PROMPT` completa por:
```python
_PROMPT = (
    "Eres un guionista de reels para redes sociales. Genera el guion completo para un "
    "reel de ~18 segundos (1 escena de video + 5 shots de imagen) sobre este negocio, "
    "basado en este post:\n\n"
    "MARCA: {business_name}\n"
    "CAPTION DEL POST: {caption}\n"
    "TONO: {tone}\n"
    "DESCRIPCION: {description}\n\n"
    "Genera:\n"
    "1. hook_text: 3-8 palabras, gancho de apertura potente (aparece 0-3s).\n"
    "2. highlight_word: UNA palabra dentro de hook_text a resaltar visualmente.\n"
    "3. tag_cta: 2-4 palabras, llamada a la accion de cierre (aparece en los ultimos 3s).\n"
    "4. narration_script: guion de voz en off en espanol, ~15-20 segundos hablados "
    "(unas 40-50 palabras), tono conversacional, sin leer literalmente el hook ni el CTA.\n"
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
    "   Las 6 evitan describir pantallas, laptops, monitores o interfaces con contenido — "
    "el generador alucina texto falso/ilegible cuando la escena implica una pantalla con "
    "informacion. Cada prompt debe terminar con: 'no text, no logos, no people speaking "
    "to camera.'\n"
    "6. music_mood: 1 frase corta en ingles describiendo el mood musical (ej. "
    "'upbeat corporate, optimistic, minimal percussion').\n\n"
    "REGLA DE SEGURIDAD: si el negocio pertenece a un nicho sensible, usa tono neutro-positivo, "
    "sin promesas absolutas ('garantizado', 'aseguramos', '100%').\n\n"
    "Responde UNICAMENTE con este JSON (sin markdown):\n"
    '{{"hook_text":"...","highlight_word":"...","tag_cta":"...",'
    '"narration_script":"...","scene_prompts":["...","...","...","...","...","..."],'
    '"music_mood":"..."}}'
)
```

- [ ] **Step 5: Actualizar la validación de conteo**

En el método `generate()` de la clase `ReelScriptGenerator`, cambia:
```python
            if len(scene_prompts) != 3:
```
por:
```python
            if len(scene_prompts) != 6:
```

- [ ] **Step 6: Correr los tests para verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_script_generator.py -v`
Expected: los 5 tests del archivo en PASS.

- [ ] **Step 7: Commit**

```bash
git add core/content_pipeline/generators/reel_script_generator.py core/content_pipeline/tests/test_reel_script_generator.py
git commit -m "feat(reels): guion pide 6 escenas (1 Veo + 5 shots de imagen) en vez de 3"
```

---

## Task 2: Shots de 2s + duración real medida con ffprobe

**Files:**
- Modify: `core/content_pipeline/generators/reel_generator.py`
- Test: `core/content_pipeline/tests/test_reel_generator.py`

**Interfaces:**
- Consumes: nada de la Tarea 1 directamente (solo asume que `scene_prompts`
  llega con 6 elementos en producción real, pero el código de esta tarea no
  depende de un conteo específico — itera `scene_prompts[1:]` sin importar
  cuántos haya).
- Produces: `_probe_video_duration(video_path: str) -> float` (module-level),
  `_generate_still_scene_clip(..., duration: float = _VEO_CLIP_DURATION_SECONDS)`
  con el nuevo parámetro `duration`, constante `_IMAGE_SHOT_DURATION_SECONDS = 2.0`.

- [ ] **Step 1: Escribir los tests que fallan**

Abre `core/content_pipeline/tests/test_reel_generator.py` y aplica estos
6 cambios exactos, en orden:

**1a.** Reemplaza el helper `_fake_ffmpeg_run` completo:
```python
def _fake_ffmpeg_run(fake_output: bytes):
    # _assemble_reel ahora llama ffprobe (via _probe_video_width) para saber el
    # ancho real del video de Veo antes de posicionar el hook — ese subprocess
    # no escribe a un archivo de salida como los demas, lee de stdout.
    def run(cmd, *args, **kwargs):
        if cmd[0] == 'ffprobe':
            return MagicMock(returncode=0, stdout='1080\n')
        with open(cmd[-1], 'wb') as f:
            f.write(fake_output)
        return MagicMock(returncode=0)
    return run
```
por:
```python
def _fake_ffmpeg_run(fake_output: bytes, width: str = '1080', duration: str = '24.0'):
    # _assemble_reel llama ffprobe 2 veces: una para la duracion real del video
    # concatenado (_probe_video_duration, formato=duration) y otra para el ancho
    # (_probe_video_width, stream=width, usado al posicionar el hook). Ninguna de
    # las 2 escribe a un archivo de salida como los demas comandos, leen de stdout.
    def run(cmd, *args, **kwargs):
        if cmd[0] == 'ffprobe':
            if 'format=duration' in cmd:
                return MagicMock(returncode=0, stdout=f'{duration}\n')
            return MagicMock(returncode=0, stdout=f'{width}\n')
        with open(cmd[-1], 'wb') as f:
            f.write(fake_output)
        return MagicMock(returncode=0)
    return run
```

**1b.** En `test_calls_ffmpeg_and_returns_output_bytes`, cambia:
```python
        assert mock_run.call_count == 4  # concat, ffprobe, overlay-drawtext, audio-mix
```
por:
```python
        assert mock_run.call_count == 5  # concat, ffprobe-duration, ffprobe-width, overlay-drawtext, audio-mix
```

**1c.** Reemplaza `test_hook_centering_uses_real_probed_width` completo (este
tiene su propio mock inline, distinto del helper compartido):
```python
    @override_settings(REEL_TEXT_OVERLAY_ENGINE='drawtext')
    def test_hook_centering_uses_real_probed_width(self, tmp_path):
        # Veo no garantiza 1080px (en produccion real devolvio 720x1280) — el
        # cursor del segmento resaltado del hook debe usar el ancho real
        # detectado via ffprobe, no un valor fijo.
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        def run(cmd, *args, **kwargs):
            if cmd[0] == 'ffprobe':
                return MagicMock(returncode=0, stdout='720\n')
            with open(cmd[-1], 'wb') as f:
                f.write(fake_output)
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run', side_effect=run) as mock_run:
            gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                script=_FAKE_SCRIPT_FOR_ASSEMBLE, colors=['#1a1a2e'],
            )
        overlay_cmd = mock_run.call_args_list[2].args[0]
        filter_complex = overlay_cmd[overlay_cmd.index('-filter_complex') + 1]
        # con ancho real 720 el cursor de 'nuevo' (resaltado, al final de la
        # linea) debe quedar bien a la izquierda de 720, nunca cerca de 1080
        highlight_filter = [p for p in filter_complex.split(';') if "text='nuevo'" in p][0]
        x_value = int(highlight_filter.split('x=')[1].split(':')[0])
        assert x_value < 720
```
por:
```python
    @override_settings(REEL_TEXT_OVERLAY_ENGINE='drawtext')
    def test_hook_centering_uses_real_probed_width(self, tmp_path):
        # Veo no garantiza 1080px (en produccion real devolvio 720x1280) — el
        # cursor del segmento resaltado del hook debe usar el ancho real
        # detectado via ffprobe, no un valor fijo.
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=_fake_ffmpeg_run(fake_output, width='720')) as mock_run:
            gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None, narration=None,
                script=_FAKE_SCRIPT_FOR_ASSEMBLE, colors=['#1a1a2e'],
            )
        overlay_cmd = mock_run.call_args_list[3].args[0]
        filter_complex = overlay_cmd[overlay_cmd.index('-filter_complex') + 1]
        # con ancho real 720 el cursor de 'nuevo' (resaltado, al final de la
        # linea) debe quedar bien a la izquierda de 720, nunca cerca de 1080
        highlight_filter = [p for p in filter_complex.split(';') if "text='nuevo'" in p][0]
        x_value = int(highlight_filter.split('x=')[1].split(':')[0])
        assert x_value < 720
```

**1d.** Después de aplicar 1c, la cadena EXACTA `mock_run.call_args_list[2].args[0]`
sigue apareciendo en otros 5 lugares del archivo (y en ningún otro — no
confundir con `mock_client_cls...call_args_list[2].kwargs` de la clase
`TestGenerateMusic`, que es un mock distinto y no debe tocarse). Reemplaza
las 5 ocurrencias restantes de:
```python
        overlay_cmd = mock_run.call_args_list[2].args[0]
```
por:
```python
        overlay_cmd = mock_run.call_args_list[3].args[0]
```
Estas 5 están dentro de: `test_hook_and_cta_drawtext_filters_are_always_present`,
`test_adds_drawtext_filters_for_subtitles`, `test_omits_subtitle_filters_when_no_subtitles`
(las 3 en `class TestAssembleReel`), y `test_playwright_engine_composes_both_pngs_via_overlay`,
`test_playwright_engine_falls_back_to_drawtext_per_element` (en
`class TestAssembleReelPlaywrightEngine`). Si tu editor soporta
"reemplazar todas las ocurrencias" de esa cadena exacta, es seguro usarlo
aquí — ya no quedan más después de aplicar 1c.

**1e.** Reemplaza los 3 tests de `class TestGenerateVideoClips` que orquestan
la mezcla Veo+Imagen. Primero, `test_first_scene_via_veo_rest_via_imagen_zoompan`:
```python
    def test_first_scene_via_veo_rest_via_imagen_zoompan(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_single_clip', return_value=b'veo-clip') as mock_veo, \
             patch.object(gen, '_probe_clip_dimensions', return_value=(720, 1280, 24.0)) as mock_probe, \
             patch.object(gen, '_generate_scene_still', return_value=b'still-bytes') as mock_still, \
             patch.object(gen, '_animate_still_to_clip', return_value=b'animated-clip') as mock_animate:
            clips = gen._generate_video_clips(['scene 1', 'scene 2', 'scene 3'])

        assert clips == [b'veo-clip', b'animated-clip', b'animated-clip']
        mock_veo.assert_called_once_with('scene 1')
        mock_probe.assert_called_once_with(b'veo-clip')
        assert mock_still.call_args_list == [call('scene 2'), call('scene 3')]
        assert mock_animate.call_args_list == [
            call(b'still-bytes', 720, 1280, 24.0),
            call(b'still-bytes', 720, 1280, 24.0),
        ]
```
por:
```python
    def test_first_scene_via_veo_rest_via_imagen_zoompan(self):
        from core.content_pipeline.generators.reel_generator import (
            ReelGenerator, _IMAGE_SHOT_DURATION_SECONDS,
        )
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_single_clip', return_value=b'veo-clip') as mock_veo, \
             patch.object(gen, '_probe_clip_dimensions', return_value=(720, 1280, 24.0)) as mock_probe, \
             patch.object(gen, '_generate_scene_still', return_value=b'still-bytes') as mock_still, \
             patch.object(gen, '_animate_still_to_clip', return_value=b'animated-clip') as mock_animate:
            clips = gen._generate_video_clips(
                ['scene 1', 'scene 2', 'scene 3', 'scene 4', 'scene 5', 'scene 6']
            )

        assert clips == [b'veo-clip'] + [b'animated-clip'] * 5
        mock_veo.assert_called_once_with('scene 1')
        mock_probe.assert_called_once_with(b'veo-clip')
        assert mock_still.call_args_list == [
            call('scene 2'), call('scene 3'), call('scene 4'), call('scene 5'), call('scene 6'),
        ]
        assert mock_animate.call_args_list == [
            call(b'still-bytes', 720, 1280, 24.0, duration=_IMAGE_SHOT_DURATION_SECONDS),
        ] * 5
```

Luego, `test_falls_back_to_imagen_when_veo_scene_fails_completely`:
```python
    def test_falls_back_to_imagen_when_veo_scene_fails_completely(self):
        from core.content_pipeline.generators.reel_generator import (
            ReelGenerator, _VIDEO_WIDTH, _VIDEO_HEIGHT, _DEFAULT_CLIP_FPS,
        )
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_single_clip', return_value=None) as mock_veo, \
             patch.object(gen, '_probe_clip_dimensions') as mock_probe, \
             patch.object(gen, '_generate_scene_still', return_value=b'still-bytes'), \
             patch.object(gen, '_animate_still_to_clip', return_value=b'animated-clip') as mock_animate:
            clips = gen._generate_video_clips(['scene 1', 'scene 2', 'scene 3'])

        assert mock_veo.call_count == 2  # 1 intento + 1 reintento, ambos fallan
        mock_probe.assert_not_called()
        assert clips == [b'animated-clip', b'animated-clip', b'animated-clip']
        assert mock_animate.call_args_list[0] == call(b'still-bytes', _VIDEO_WIDTH, _VIDEO_HEIGHT, _DEFAULT_CLIP_FPS)
```
por:
```python
    def test_falls_back_to_imagen_when_veo_scene_fails_completely(self):
        from core.content_pipeline.generators.reel_generator import (
            ReelGenerator, _VIDEO_WIDTH, _VIDEO_HEIGHT, _DEFAULT_CLIP_FPS,
            _VEO_CLIP_DURATION_SECONDS, _IMAGE_SHOT_DURATION_SECONDS,
        )
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_single_clip', return_value=None) as mock_veo, \
             patch.object(gen, '_probe_clip_dimensions') as mock_probe, \
             patch.object(gen, '_generate_scene_still', return_value=b'still-bytes'), \
             patch.object(gen, '_animate_still_to_clip', return_value=b'animated-clip') as mock_animate:
            clips = gen._generate_video_clips(
                ['scene 1', 'scene 2', 'scene 3', 'scene 4', 'scene 5', 'scene 6']
            )

        assert mock_veo.call_count == 2  # 1 intento + 1 reintento, ambos fallan
        mock_probe.assert_not_called()
        assert clips == [b'animated-clip'] * 6
        assert mock_animate.call_count == 6
        assert mock_animate.call_args_list[0] == call(
            b'still-bytes', _VIDEO_WIDTH, _VIDEO_HEIGHT, _DEFAULT_CLIP_FPS,
            duration=_VEO_CLIP_DURATION_SECONDS,
        )
        assert mock_animate.call_args_list[1] == call(
            b'still-bytes', _VIDEO_WIDTH, _VIDEO_HEIGHT, _DEFAULT_CLIP_FPS,
            duration=_IMAGE_SHOT_DURATION_SECONDS,
        )
```

Y finalmente, `test_skips_imagen_scene_that_fails_completely`:
```python
    def test_skips_imagen_scene_that_fails_completely(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_single_clip', return_value=b'veo-clip'), \
             patch.object(gen, '_probe_clip_dimensions', return_value=(720, 1280, 24.0)), \
             patch.object(gen, '_generate_scene_still', return_value=None) as mock_still, \
             patch.object(gen, '_animate_still_to_clip') as mock_animate:
            clips = gen._generate_video_clips(['scene 1', 'scene 2', 'scene 3'])

        assert clips == [b'veo-clip']
        assert mock_still.call_count == 4  # 2 escenas x (1 intento + 1 reintento)
        mock_animate.assert_not_called()
```
por:
```python
    def test_skips_imagen_scene_that_fails_completely(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_single_clip', return_value=b'veo-clip'), \
             patch.object(gen, '_probe_clip_dimensions', return_value=(720, 1280, 24.0)), \
             patch.object(gen, '_generate_scene_still', return_value=None) as mock_still, \
             patch.object(gen, '_animate_still_to_clip') as mock_animate:
            clips = gen._generate_video_clips(
                ['scene 1', 'scene 2', 'scene 3', 'scene 4', 'scene 5', 'scene 6']
            )

        assert clips == [b'veo-clip']
        assert mock_still.call_count == 10  # 5 escenas x (1 intento + 1 reintento)
        mock_animate.assert_not_called()
```

- [ ] **Step 2: Agregar el test de `_probe_video_duration`**

Agrega esta clase nueva en `test_reel_generator.py`, justo después de
`class TestProbeVideoDimensions` (antes de `class TestGenerateSceneStill`):

```python
class TestProbeVideoDuration:
    def test_returns_duration_as_float(self):
        from core.content_pipeline.generators.reel_generator import _probe_video_duration
        fake_result = MagicMock()
        fake_result.stdout = '18.5\n'
        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    return_value=fake_result) as mock_run:
            duration = _probe_video_duration('/fake/path.mp4')
        assert duration == 18.5
        cmd = mock_run.call_args.args[0]
        assert cmd[0] == 'ffprobe'
        assert 'format=duration' in cmd
        assert '/fake/path.mp4' in cmd
```

- [ ] **Step 3: Correr los tests para verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -v 2>&1 | tail -60`
Expected: FAIL — `_probe_video_duration` no existe, `_IMAGE_SHOT_DURATION_SECONDS`
no existe, `_generate_video_clips` todavía trata `scene_prompts[1:]` como si
fueran solo 2 elementos de 8s, `_assemble_reel` todavía calcula
`duration = len(clips) * 8`.

- [ ] **Step 4: Agregar la constante `_IMAGE_SHOT_DURATION_SECONDS`**

En `core/content_pipeline/generators/reel_generator.py`, línea 27, cambia:
```python
_VEO_CLIP_DURATION_SECONDS = 8
```
por:
```python
_VEO_CLIP_DURATION_SECONDS = 8
_IMAGE_SHOT_DURATION_SECONDS = 2.0  # duracion de cada shot corto de imagen (escenas 1-5)
```

- [ ] **Step 5: Agregar `_probe_video_duration`**

Justo después de la función `_probe_video_dimensions` existente (antes de
`def _build_hook_filter_parts`):

```python
def _probe_video_duration(video_path: str) -> float:
    # Con clips de duracion mixta (Veo 8s + shots de imagen de 2s) la formula
    # anterior duration = len(clips) * _VEO_CLIP_DURATION_SECONDS ya no es
    # valida — se mide la duracion real del video ya concatenado.
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
         '-of', 'csv=p=0', video_path],
        check=True, capture_output=True, text=True,
    )
    return float(result.stdout.strip())
```

- [ ] **Step 6: `_generate_still_scene_clip` gana el parámetro `duration`**

Reemplaza el método completo:
```python
    def _generate_still_scene_clip(self, prompt: str, width: int, height: int, fps: float) -> bytes | None:
        still = self._generate_scene_still(prompt)
        if still is None:
            still = self._generate_scene_still(prompt)  # 1 reintento
        if still is None:
            return None
        return self._animate_still_to_clip(still, width, height, fps)
```
por:
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

- [ ] **Step 7: Actualizar `_generate_video_clips`**

Reemplaza el método completo:
```python
    def _generate_video_clips(self, scene_prompts: list[str]) -> list[bytes]:
        # scene_prompts[0] va a Veo (video real). scene_prompts[1] y [2] se generan
        # como imagen fija (Imagen) + animacion zoompan de ffmpeg — reduce el uso de
        # Veo de 3 a 1 clip por reel (costo ~$2.40 -> ~$0.88, y elimina 2/3 del riesgo
        # de alucinacion de movimiento). Ver docs/superpowers/specs/2026-07-15-reels-imagen-veo-hybrid-design.md
        clips = []

        veo_clip = self._generate_single_clip(scene_prompts[0])
        if veo_clip is None:
            veo_clip = self._generate_single_clip(scene_prompts[0])  # 1 reintento

        if veo_clip is not None:
            clips.append(veo_clip)
            width, height, fps = self._probe_clip_dimensions(veo_clip)
        else:
            logger.warning(
                f"Clip de Veo fallido tras reintento, escena 0 tambien se genera via "
                f"Imagen: {scene_prompts[0][:80]}"
            )
            width, height, fps = _VIDEO_WIDTH, _VIDEO_HEIGHT, _DEFAULT_CLIP_FPS
            still_clip = self._generate_still_scene_clip(scene_prompts[0], width, height, fps)
            if still_clip is not None:
                clips.append(still_clip)

        for prompt in scene_prompts[1:]:
            still_clip = self._generate_still_scene_clip(prompt, width, height, fps)
            if still_clip is not None:
                clips.append(still_clip)
            else:
                logger.warning(f"Escena de Imagen fallida tras reintento, se omite: {prompt[:80]}")

        return clips
```
por:
```python
    def _generate_video_clips(self, scene_prompts: list[str]) -> list[bytes]:
        # scene_prompts[0] va a Veo (video real, _VEO_CLIP_DURATION_SECONDS=8s).
        # scene_prompts[1:] (5 shots cortos) se generan como imagen fija (Imagen) +
        # animacion zoompan de ffmpeg, cada uno de _IMAGE_SHOT_DURATION_SECONDS=2s —
        # ritmo de corte rapido tipo publicidad, costo marginal (Imagen $0.04/imagen).
        # Ver docs/superpowers/specs/2026-07-15-reels-short-image-shots-design.md
        clips = []

        veo_clip = self._generate_single_clip(scene_prompts[0])
        if veo_clip is None:
            veo_clip = self._generate_single_clip(scene_prompts[0])  # 1 reintento

        if veo_clip is not None:
            clips.append(veo_clip)
            width, height, fps = self._probe_clip_dimensions(veo_clip)
        else:
            logger.warning(
                f"Clip de Veo fallido tras reintento, escena 0 tambien se genera via "
                f"Imagen: {scene_prompts[0][:80]}"
            )
            width, height, fps = _VIDEO_WIDTH, _VIDEO_HEIGHT, _DEFAULT_CLIP_FPS
            still_clip = self._generate_still_scene_clip(
                scene_prompts[0], width, height, fps, duration=_VEO_CLIP_DURATION_SECONDS,
            )
            if still_clip is not None:
                clips.append(still_clip)

        for prompt in scene_prompts[1:]:
            still_clip = self._generate_still_scene_clip(
                prompt, width, height, fps, duration=_IMAGE_SHOT_DURATION_SECONDS,
            )
            if still_clip is not None:
                clips.append(still_clip)
            else:
                logger.warning(f"Escena de Imagen fallida tras reintento, se omite: {prompt[:80]}")

        return clips
```

- [ ] **Step 8: Fix de `_assemble_reel` — medir la duración real**

Cambia:
```python
            duration = len(clips) * _VEO_CLIP_DURATION_SECONDS
```
por:
```python
            duration = _probe_video_duration(concat_path)
```

- [ ] **Step 9: Correr los tests para verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -v`
Expected: todos los tests del archivo en PASS.

- [ ] **Step 10: Correr la suite completa del proyecto**

Run: `docker compose exec -T backend python -m pytest`
Expected: todos los tests en PASS.

- [ ] **Step 11: Commit**

```bash
git add core/content_pipeline/generators/reel_generator.py core/content_pipeline/tests/test_reel_generator.py
git commit -m "feat(reels): shots de imagen de 2s en vez de 8s, duracion real medida con ffprobe"
```

---

## Task 3: Verificación real end-to-end (no delegar a agente externo — la ejecuta el controlador de esta sesión)

Mismo patrón que la verificación real de la Parte anterior (escenas híbridas
Imagen+Veo) — gasta cuota real de Veo/Imagen, se ejecuta después de que las
Tareas 1-2 estén mergeadas y verificadas.

- [ ] **Step 1: Levantar el stack con el código nuevo**

```bash
docker compose up -d --force-recreate --no-deps --scale rqworker=3 backend rqworker
```

- [ ] **Step 2: Generar un guion + reel real de punta a punta**

```bash
docker compose exec -T backend python manage.py shell -c "
from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
from core.content_pipeline.generators.reel_generator import ReelGenerator
from core.brand_dna.models import BrandDNA

brand = BrandDNA.objects.filter(business_name__isnull=False).exclude(business_name='').first()
script = ReelScriptGenerator().generate(
    {'caption': 'Mira como transformamos este proyecto de principio a fin'}, brand,
)
print('scene_prompts (', len(script['scene_prompts']), 'total):')
for i, s in enumerate(script['scene_prompts']):
    print(f'  [{i}]', s[:150])
video_url, poster_url = ReelGenerator(bucket_name='agente-cosmic-assets').generate(
    script, brand.primary_colors or ['#1a1a2e'], 'verify-short-shots-reel',
)
print('video_url:', video_url)
print('poster_url:', poster_url)
"
```

Expected: imprime 6 `scene_prompts`, `video_url`/`poster_url` no vacíos.

- [ ] **Step 3: Verificar el MP4 resultante con `ffprobe`**

Descarga el `video_url` impreso y corre:

```bash
ffprobe -v error -show_entries stream=codec_type,width,height,r_frame_rate -show_entries format=duration -of default=noprint_wrappers=0 <archivo-descargado>.mp4
```

Expected: 1 stream de video uniforme, duración total ≈18s (8s Veo + 5×2s
Imagen). Extraer 4-5 frames a lo largo del video (ej. con `ffmpeg -vf
select=...`) y revisarlos visualmente: los cortes entre shots deben sentirse
rápidos pero no abruptos/rotos, sin artefactos de concat, hook/CTA legibles
sobre cualquiera de los fondos (Veo o Imagen).

- [ ] **Step 4: Confirmar en los logs que no hubo errores**

```bash
docker compose logs backend rqworker --since 10m | grep -i "imagen scene\|clip de veo\|escena de imagen\|error\|traceback" | grep -v "INFO\|DeprecationWarning"
```

Expected: sin errores. Si aparece algo, investigar con evidencia antes de dar
la tarea por cerrada.

- [ ] **Step 5: Documentar el resultado**

Agregar una entrada a `hallazgos.txt` documentando la verificación real
(mismo formato que HALLAZGO 68), incluyendo la duración real medida y
confirmación de que los 5 shots de 2s se sienten como "ráfaga" sin romper
la continuidad visual.
