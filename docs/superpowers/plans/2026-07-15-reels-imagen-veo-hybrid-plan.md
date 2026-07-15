# Reels: Escenas híbridas Imagen+zoompan/Veo — Plan de Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reducir alucinación visual y costo en los reels de Agente Cosmic — de las 3 escenas de video que genera un reel, solo 1 se sigue generando con Veo (video real); las otras 2 pasan a ser una imagen fija generada con Imagen y animada con el filtro `zoompan` de ffmpeg (efecto Ken Burns), sin agregar Playwright/HyperFrames al pipeline.

**Architecture:** `reel_script_generator.py` diferencia el rol de cada escena por índice en el prompt que le manda a Gemini (escena 0 = segura para video, escenas 1-2 = pueden tener detalle de precisión porque van a imagen fija). `reel_generator.py` orquesta: genera el clip de Veo primero, mide su resolución/fps real con `ffprobe`, y genera las 2 imágenes fijas + su animación normalizadas exactamente a esa resolución/fps para que el `concat -c copy` existente siga funcionando sin cambios.

**Tech Stack:** Vertex AI (Veo `generate_videos`, Imagen `generate_images`), ffmpeg (`zoompan`, `ffprobe`), Django, pytest.

## Global Constraints

- Duración de cada clip/escena: 8 segundos (`_VEO_CLIP_DURATION_SECONDS`, sin cambios).
- Aspect ratio 9:16 para Veo (sin cambios) y también para Imagen en este flujo (nuevo, específico de reels — no afecta `VERTEX_IMAGE_MODEL` usado para posts, que sigue en 1:1).
- El guion (`ReelScriptGenerator.generate()`) sigue devolviendo exactamente 3 `scene_prompts` (lista de strings) — el schema JSON no cambia, solo el contenido de la instrucción que arma cada prompt.
- `_generate_video_clips` debe seguir devolviendo `list[bytes]` de hasta 3 elementos de 8s c/u — `generate()` sigue abortando el reel si `len(clips) < 3` (umbral sin cambios).
- `_assemble_reel`, `_probe_video_width` (se reutiliza, NO se reemplaza ni se modifica), el concat `-c copy`, ensamblaje final, subtítulos, música, narración y overlay de texto NO se tocan en este plan.
- NO usar HyperFrames ni Playwright para animar las imágenes fijas — solo el filtro `zoompan` nativo de ffmpeg (decisión de producto explícita, ver spec).
- NO implementar clasificación automática de escenas (Opción C) — la regla es fija por posición: índice 0 = Veo, índices 1 y 2 = Imagen+zoompan.
- NO reducir a 0 clips de Veo — el reel debe conservar 1 escena de video real.

---

## Task 1: Guion — diferenciar el rol de cada escena por índice

**Files:**
- Modify: `core/content_pipeline/generators/reel_script_generator.py:19-45` (constante `_PROMPT`)
- Test: `core/content_pipeline/tests/test_reel_script_generator.py`

**Interfaces:**
- Consumes: nada nuevo — `_PROMPT` sigue siendo un string module-level, formateado con `.format(business_name=..., caption=..., tone=..., description=...)` en `ReelScriptGenerator.generate()` (sin cambios en esa llamada).
- Produces: nada que otras tareas consuman directamente — este cambio es independiente de las Tareas 2-3 (ambas trabajan en `reel_generator.py` y no dependen del contenido exacto del prompt, solo de que `scene_prompts` siga siendo una lista de 3 strings).

- [ ] **Step 1: Escribir el test que verifica el nuevo prompt**

Abre `core/content_pipeline/tests/test_reel_script_generator.py`. Agrega este test al final del archivo (usa el fixture `brand_dna` y el helper `_mock_vertex_client` ya definidos arriba en el mismo archivo):

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
        '"scene_prompts":["s1","s2","s3"],"music_mood":"M"}'
    )
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        ReelScriptGenerator().generate(post_data, brand_dna)

    sent_prompt = mock_vc.return_value.models.generate_content.call_args.kwargs['contents']
    assert 'scene_prompts[0]' in sent_prompt
    assert 'GENERADOR DE VIDEO' in sent_prompt
    assert 'scene_prompts[1]' in sent_prompt and 'scene_prompts[2]' in sent_prompt
    assert 'GENERADOR DE IMAGEN FIJA' in sent_prompt
    assert 'NO debe incluir manipulacion precisa' in sent_prompt
```

- [ ] **Step 2: Correr el test para verificar que falla**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_script_generator.py::test_prompt_differentiates_veo_scene_from_imagen_scenes -v`
Expected: FAIL — el prompt actual no contiene `'scene_prompts[0]'` ni `'GENERADOR DE VIDEO'`.

- [ ] **Step 3: Reescribir `_PROMPT` en `reel_script_generator.py`**

Reemplaza el punto 5 completo dentro de la constante `_PROMPT` (líneas 32-37 del archivo actual). El texto completo de `_PROMPT` queda así (todo lo demás — puntos 1-4, 6, la regla de seguridad, y el formato JSON de salida — se mantiene exactamente igual, solo cambia el punto 5):

```python
_PROMPT = (
    "Eres un guionista de reels para redes sociales. Genera el guion completo para un "
    "reel de ~24 segundos (3 escenas de Veo) sobre este negocio, basado en este post:\n\n"
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
    "5. scene_prompts: exactamente 3 prompts EN INGLES describiendo 3 escenas visuales "
    "secuenciales relacionadas al negocio, con roles DISTINTOS por posicion:\n"
    "   - scene_prompts[0]: para un GENERADOR DE VIDEO. Debe ser un plano amplio o de "
    "ambiente con movimiento de camara (push-in, pan lento, rotacion suave). NO debe "
    "incluir manipulacion precisa de objetos con las manos (atornillar, cablear, cortar, "
    "ensamblar, escribir a mano en primer plano) porque el generador de video falla en "
    "coherencia fisica de manos con herramientas entre frames.\n"
    "   - scene_prompts[1] y scene_prompts[2]: para un GENERADOR DE IMAGEN FIJA. Aqui SI "
    "se prefiere el detalle de precision: manos trabajando con herramientas, texturas de "
    "cerca, el oficio en accion — porque es una imagen fija y no necesita coherencia "
    "fisica en el tiempo.\n"
    "   Las 3 evitan describir pantallas, laptops, monitores o interfaces con contenido — "
    "el generador alucina texto falso/ilegible cuando la escena implica una pantalla con "
    "informacion. Cada prompt debe terminar con: 'no text, no logos, no people speaking "
    "to camera.'\n"
    "6. music_mood: 1 frase corta en ingles describiendo el mood musical (ej. "
    "'upbeat corporate, optimistic, minimal percussion').\n\n"
    "REGLA DE SEGURIDAD: si el negocio pertenece a un nicho sensible, usa tono neutro-positivo, "
    "sin promesas absolutas ('garantizado', 'aseguramos', '100%').\n\n"
    "Responde UNICAMENTE con este JSON (sin markdown):\n"
    '{{"hook_text":"...","highlight_word":"...","tag_cta":"...",'
    '"narration_script":"...","scene_prompts":["...","...","..."],"music_mood":"..."}}'
)
```

`_FALLBACK_SCENES` (líneas 13-17) **no cambia** — ya es genérico y seguro tanto para Veo como para Imagen (ninguna de las 3 describe manos con herramientas).

- [ ] **Step 4: Correr el test para verificar que pasa**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_script_generator.py -v`
Expected: los 5 tests del archivo (4 existentes + el nuevo) en PASS.

- [ ] **Step 5: Commit**

```bash
git add core/content_pipeline/generators/reel_script_generator.py core/content_pipeline/tests/test_reel_script_generator.py
git commit -m "feat(reels): diferenciar escena de Veo vs escenas de Imagen fija en el guion"
```

---

## Task 2: Funciones atómicas nuevas en `reel_generator.py`

**Files:**
- Modify: `core/content_pipeline/generators/reel_generator.py` (agregar constante, 1 función module-level, 2 métodos de `ReelGenerator`)
- Test: `core/content_pipeline/tests/test_reel_generator.py`

**Interfaces:**
- Consumes: `_vertex_client()` (ya existe, línea 232), `settings.VERTEX_IMAGE_MODEL`, `track_external_api`/`record_imagen_generation` (de `core.shared.metrics_utils`), `types.GenerateImagesConfig` (de `google.genai.types`, ya importado como `types`).
- Produces (para la Tarea 3):
  - `_probe_video_dimensions(video_path: str) -> tuple[int, int, float]` — función module-level, devuelve `(width, height, fps)`.
  - `ReelGenerator._generate_scene_still(self, prompt: str) -> bytes | None` — 1 intento a Imagen, sin reintento interno (el reintento lo hace quien la llame, igual que `_generate_single_clip`).
  - `ReelGenerator._animate_still_to_clip(self, image_bytes: bytes, width: int, height: int, fps: float, duration: float = _VEO_CLIP_DURATION_SECONDS) -> bytes` — anima con `zoompan`, sin llamadas a API.
  - Constante `_DEFAULT_CLIP_FPS = 24.0`.

- [ ] **Step 1: Escribir los tests que fallan**

Abre `core/content_pipeline/tests/test_reel_generator.py`. Agrega `record_imagen_generation` a las importaciones que ya se mockean vía `patch(...)` (no requiere import nuevo en el archivo de test, se referencia por string en `patch()`). Agrega estas 3 clases de test, después de `TestGenerateVideoClips` (antes de `TestGenerateMusic`, línea 209 actual):

```python
class TestProbeVideoDimensions:
    def test_returns_width_height_fps(self):
        from core.content_pipeline.generators.reel_generator import _probe_video_dimensions
        fake_result = MagicMock()
        fake_result.stdout = '720,1280,24/1\n'
        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    return_value=fake_result) as mock_run:
            width, height, fps = _probe_video_dimensions('/fake/path.mp4')
        assert (width, height, fps) == (720, 1280, 24.0)
        cmd = mock_run.call_args.args[0]
        assert cmd[0] == 'ffprobe'
        assert '/fake/path.mp4' in cmd

    def test_handles_non_integer_frame_rate(self):
        from core.content_pipeline.generators.reel_generator import _probe_video_dimensions
        fake_result = MagicMock()
        fake_result.stdout = '1080,1920,25000/1001\n'
        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    return_value=fake_result):
            _, _, fps = _probe_video_dimensions('/fake/path.mp4')
        assert round(fps, 3) == round(25000 / 1001, 3)


class TestGenerateSceneStill:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
    )
    def test_returns_image_bytes_on_success(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_image = b'fake-image-bytes'
        mock_generated = MagicMock()
        mock_generated.image.image_bytes = fake_image
        mock_resp = MagicMock()
        mock_resp.generated_images = [mock_generated]
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_images.return_value = mock_resp
            result = gen._generate_scene_still('a workshop scene')
        assert result == fake_image
        call_kwargs = mock_vc.return_value.models.generate_images.call_args.kwargs
        assert call_kwargs['model'] == 'imagen-3.0-generate-001'
        assert call_kwargs['config'].aspect_ratio == '9:16'
        assert call_kwargs['prompt'].startswith('a workshop scene')

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
    )
    def test_returns_none_on_api_error(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_images.side_effect = Exception('rejected')
            result = gen._generate_scene_still('a workshop scene')
        assert result is None

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_IMAGE_MODEL='imagen-3.0-generate-001',
    )
    def test_returns_none_when_no_images_generated(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_resp = MagicMock()
        mock_resp.generated_images = []
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_images.return_value = mock_resp
            result = gen._generate_scene_still('a workshop scene')
        assert result is None


class TestAnimateStillToClip:
    def test_builds_zoompan_command_with_exact_dimensions(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-animated-mp4-bytes'

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[-1], 'wb') as f:
                f.write(fake_output)
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=fake_run) as mock_run:
            result = gen._animate_still_to_clip(b'fake-image-bytes', width=720, height=1280, fps=24.0, duration=8)

        assert result == fake_output
        cmd = mock_run.call_args.args[0]
        assert cmd[0] == 'ffmpeg'
        assert cmd[cmd.index('-t') + 1] == '8'
        vf_idx = cmd.index('-vf')
        assert 's=720x1280:fps=24.0' in cmd[vf_idx + 1]
        assert 'zoompan' in cmd[vf_idx + 1]

    def test_uses_default_duration_of_8_seconds(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[-1], 'wb') as f:
                f.write(b'out')
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run',
                    side_effect=fake_run) as mock_run:
            gen._animate_still_to_clip(b'fake-image-bytes', width=1080, height=1920, fps=24.0)

        cmd = mock_run.call_args.args[0]
        assert cmd[cmd.index('-t') + 1] == '8'
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py::TestProbeVideoDimensions core/content_pipeline/tests/test_reel_generator.py::TestGenerateSceneStill core/content_pipeline/tests/test_reel_generator.py::TestAnimateStillToClip -v`
Expected: FAIL — `_probe_video_dimensions`, `_generate_scene_still` y `_animate_still_to_clip` no existen todavía.

- [ ] **Step 3: Agregar la constante `_DEFAULT_CLIP_FPS`**

En `core/content_pipeline/generators/reel_generator.py`, justo después de la línea `_VIDEO_HEIGHT = 1920` (línea 48 actual):

```python
_DEFAULT_CLIP_FPS = 24.0  # usado solo cuando no hay clip real de Veo del cual medir fps
```

- [ ] **Step 4: Agregar `_probe_video_dimensions` (module-level, junto a `_probe_video_width`)**

Justo después de la función `_probe_video_width` existente (después de la línea 144, antes de `def _build_hook_filter_parts`):

```python
def _probe_video_dimensions(video_path: str) -> tuple[int, int, float]:
    # Extiende _probe_video_width (que solo mide ancho, para el centrado del hook)
    # con alto y fps reales — necesarios para normalizar los clips de Imagen+zoompan
    # exactamente al mismo formato que produjo Veo, para que el concat -c copy de
    # _assemble_reel siga funcionando sin cambios.
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
         '-show_entries', 'stream=width,height,r_frame_rate', '-of', 'csv=p=0', video_path],
        check=True, capture_output=True, text=True,
    )
    width_str, height_str, fps_str = result.stdout.strip().split(',')
    num, den = fps_str.split('/')
    fps = float(num) / float(den) if float(den) != 0 else float(num)
    return int(width_str), int(height_str), fps
```

- [ ] **Step 5: Agregar `_generate_scene_still` y `_animate_still_to_clip` a la clase `ReelGenerator`**

Justo después del método `_generate_single_clip` existente (después de la línea 355, antes de `def _generate_music`):

```python
    def _generate_scene_still(self, prompt: str) -> bytes | None:
        try:
            client = _vertex_client()
            with track_external_api('imagen3', operation='image_generate'):
                resp = client.models.generate_images(
                    model=settings.VERTEX_IMAGE_MODEL,
                    prompt=prompt + self._VEO_SAFE_CONSTRAINTS,
                    config=types.GenerateImagesConfig(
                        number_of_images=1,
                        aspect_ratio='9:16',
                    ),
                )
            if resp.generated_images:
                record_imagen_generation('reel_scene')
                return resp.generated_images[0].image.image_bytes
            return None
        except Exception as e:
            logger.warning(f"Imagen scene generation failed: {e}")
            return None

    def _animate_still_to_clip(self, image_bytes: bytes, width: int, height: int,
                                fps: float, duration: float = _VEO_CLIP_DURATION_SECONDS) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            image_path = os.path.join(tmp, 'still.png')
            with open(image_path, 'wb') as f:
                f.write(image_bytes)
            output_path = os.path.join(tmp, 'animated.mp4')
            subprocess.run(
                ['ffmpeg', '-y', '-loop', '1', '-i', image_path, '-t', str(duration),
                 '-vf', (
                     "scale=8000:-1,"
                     "zoompan=z='min(zoom+0.0015,1.08)':d=1:"
                     "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                     f"s={width}x{height}:fps={fps}"
                 ),
                 '-c:v', 'libx264', '-pix_fmt', 'yuv420p', output_path],
                check=True, capture_output=True,
            )
            with open(output_path, 'rb') as f:
                return f.read()
```

`_VEO_SAFE_CONSTRAINTS` ya existe como atributo de clase (línea 249) — se reutiliza tal cual para Imagen porque la misma regla ("no texto/UI legible alucinado") aplica igual de bien a imagen fija que a video.

`record_imagen_generation` necesita agregarse al import existente de `core.shared.metrics_utils` (línea 16-20 actual):

```python
from core.shared.metrics_utils import (
    track_external_api, record_tokens, record_veo_generation,
    record_lyria_generation, record_tts_generation,
    record_playwright_overlay_fallback, record_imagen_generation,
)
```

- [ ] **Step 6: Correr los tests para verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -v`
Expected: todos los tests del archivo en PASS (los existentes siguen pasando sin cambios, los 8 nuevos también).

- [ ] **Step 7: Commit**

```bash
git add core/content_pipeline/generators/reel_generator.py core/content_pipeline/tests/test_reel_generator.py
git commit -m "feat(reels): agregar generación de escena fija (Imagen) y animación zoompan"
```

---

## Task 3: Orquestación híbrida — reescribir `_generate_video_clips`

**Files:**
- Modify: `core/content_pipeline/generators/reel_generator.py` (reescribe `_generate_video_clips`, agrega 2 métodos helper)
- Test: `core/content_pipeline/tests/test_reel_generator.py` (reemplaza 2 tests existentes, agrega 3 nuevos)

**Interfaces:**
- Consumes (de Tareas 1-2, y de código existente): `_generate_single_clip` (existente, sin cambios), `_probe_video_dimensions` (Tarea 2), `_generate_scene_still` (Tarea 2), `_animate_still_to_clip` (Tarea 2), `_DEFAULT_CLIP_FPS` (Tarea 2), `_VIDEO_WIDTH`/`_VIDEO_HEIGHT` (constantes existentes, líneas 33/48).
- Produces: `ReelGenerator._probe_clip_dimensions(self, video_bytes: bytes) -> tuple[int, int, float]`, `ReelGenerator._generate_still_scene_clip(self, prompt: str, width: int, height: int, fps: float) -> bytes | None`, y `ReelGenerator._generate_video_clips(self, scene_prompts: list[str]) -> list[bytes]` reescrito — **mismo contrato de firma y retorno que la versión actual** (consumido por `generate()`, que NO se modifica en este plan).

- [ ] **Step 1: Reemplazar los 2 tests existentes que quedarán obsoletos**

`test_returns_one_clip_per_scene_prompt` y `test_skips_clip_that_fails_after_retry` (dentro de `class TestGenerateVideoClips`, líneas 108-145 actuales de `test_reel_generator.py`) asumen que las 3 escenas van a Veo — eso deja de ser cierto. Reemplázalos por estos 3 tests (mantén los otros 2 tests de la misma clase — `test_single_clip_returns_none_if_operation_never_completes_within_timeout` y `test_single_clip_keeps_polling_while_under_timeout` — sin tocar, siguen probando `_generate_single_clip` directamente, que no cambia):

Primero, agrega `call` al import de `unittest.mock` al inicio del archivo (línea 1 actual):

```python
from unittest.mock import patch, MagicMock, call
```

Luego, en `class TestGenerateVideoClips`, borra `test_returns_one_clip_per_scene_prompt` y `test_skips_clip_that_fails_after_retry`, y agrega en su lugar:

```python
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_VIDEO_MODEL='veo-3.0-fast-generate-001',
    )
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

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_VIDEO_MODEL='veo-3.0-fast-generate-001',
    )
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

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_VIDEO_MODEL='veo-3.0-fast-generate-001',
    )
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

También agrega esta clase nueva (en cualquier parte del archivo, por ejemplo justo antes de `class TestGenerateMusic`):

```python
class TestProbeClipDimensions:
    def test_writes_bytes_to_temp_file_and_probes(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        captured = {}

        def fake_probe(path):
            with open(path, 'rb') as f:
                captured['content'] = f.read()
            return (720, 1280, 24.0)

        with patch('core.content_pipeline.generators.reel_generator._probe_video_dimensions',
                    side_effect=fake_probe):
            result = gen._probe_clip_dimensions(b'fake-video-bytes')

        assert result == (720, 1280, 24.0)
        assert captured['content'] == b'fake-video-bytes'
```

- [ ] **Step 2: Correr los tests para verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py::TestGenerateVideoClips core/content_pipeline/tests/test_reel_generator.py::TestProbeClipDimensions -v`
Expected: FAIL — `_probe_clip_dimensions` no existe, y `_generate_video_clips` todavía genera las 3 escenas vía Veo.

- [ ] **Step 3: Reescribir `_generate_video_clips` y agregar los 2 helpers**

En `core/content_pipeline/generators/reel_generator.py`, reemplaza el método `_generate_video_clips` completo (líneas 307-317 actuales) por esto:

```python
    def _probe_clip_dimensions(self, video_bytes: bytes) -> tuple[int, int, float]:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, 'probe.mp4')
            with open(path, 'wb') as f:
                f.write(video_bytes)
            return _probe_video_dimensions(path)

    def _generate_still_scene_clip(self, prompt: str, width: int, height: int, fps: float) -> bytes | None:
        still = self._generate_scene_still(prompt)
        if still is None:
            still = self._generate_scene_still(prompt)  # 1 reintento
        if still is None:
            return None
        return self._animate_still_to_clip(still, width, height, fps)

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

- [ ] **Step 4: Correr los tests para verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -v`
Expected: todos los tests del archivo en PASS.

- [ ] **Step 5: Correr la suite completa del proyecto**

Run: `docker compose exec -T backend python -m pytest`
Expected: todos los tests en PASS (esto confirma que `generate()`, `_assemble_reel` y el resto del pipeline de reels siguen funcionando sin cambios, ya que `TestGenerate` mockea `_generate_video_clips` completo y no le importa su implementación interna).

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/generators/reel_generator.py core/content_pipeline/tests/test_reel_generator.py
git commit -m "feat(reels): orquestar escenas hibridas Veo+Imagen en _generate_video_clips"
```

---

## Task 4: Verificación real end-to-end (no delegar a agente externo — la ejecuta el controlador de esta sesión)

Esta tarea NO se despacha a un agente externo — requiere gastar cuota real de Veo/Imagen y juicio directo sobre el resultado (mismo patrón que las verificaciones reales anteriores en `project_cosmic_reels.md`, ej. "real-day1-scaled-*"). Se ejecuta después de que las Tareas 1-3 estén mergeadas y verificadas.

- [ ] **Step 1: Levantar el stack y confirmar que corre el código nuevo**

```bash
docker compose up -d --force-recreate --no-deps --scale rqworker=3 backend rqworker
```

- [ ] **Step 2: Generar un guion + reel real de punta a punta**

```bash
docker compose exec -T backend python manage.py shell -c "
from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
from core.content_pipeline.generators.reel_generator import ReelGenerator
from core.brand_dna.models import BrandDNA

brand = BrandDNA.objects.filter(business_name__isnull=False).first()
if brand is None:
    print('No hay ningun BrandDNA real en la base — crear uno de prueba antes de correr esto.')
else:
    script = ReelScriptGenerator().generate(
        {'caption': 'Mira como transformamos este proyecto de principio a fin'}, brand,
    )
    print('scene_prompts:', script['scene_prompts'])
    video_url, poster_url = ReelGenerator(bucket_name='agente-cosmic-assets').generate(
        script, brand.primary_colors or ['#1a1a2e'], 'verify-hybrid-reel',
    )
    print('video_url:', video_url)
    print('poster_url:', poster_url)
"
```

Expected: imprime 3 `scene_prompts` (el primero orientado a video/ambiente, los otros 2 con más detalle de precisión), y `video_url`/`poster_url` no vacíos.

- [ ] **Step 3: Verificar el MP4 resultante con `ffprobe`**

Descarga el `video_url` impreso y corre:

```bash
ffprobe -v error -show_entries stream=codec_type,width,height,r_frame_rate -show_entries format=duration -of default=noprint_wrappers=0 <archivo-descargado>.mp4
```

Expected: 1 stream de video con resolución/fps consistentes en todo el archivo (confirma que el concat entre el clip de Veo y los 2 clips de Imagen+zoompan no produjo un archivo corrupto ni con resoluciones mezcladas), duración total ≈24s, y el archivo se reproduce correctamente en un reproductor real (no solo `ffprobe` — abrirlo y verlo).

- [ ] **Step 4: Confirmar en los logs cuáles escenas fueron Veo vs Imagen**

```bash
docker compose logs backend rqworker --since 10m | grep -i "Imagen scene\|Clip de Veo\|Escena de Imagen"
```

Expected: sin errores de `"Imagen scene generation failed"` ni `"Escena de Imagen fallida"` en la corrida (si aparecen, investigar antes de dar la tarea por cerrada — puede ser un problema real de la API o del prompt, no descartar como flake sin evidencia).

- [ ] **Step 5: Documentar el resultado**

Si el reel real se ve bien (3 escenas concatenadas sin artefactos, la escena de Veo se ve fluida, las 2 de Imagen+zoompan no se ven "cortadas" ni pixeladas), agregar una entrada a `hallazgos.txt` documentando la verificación real (mismo formato que HALLAZGO 58 en `project_cosmic_reels.md` — "2 corridas reales... 0 fallbacks"). Si algo se ve mal, reportarlo como bloqueante antes de continuar — no forzar el cierre de la tarea.
