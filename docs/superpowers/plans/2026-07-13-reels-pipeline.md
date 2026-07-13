# Reels Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Agregar un reel de video al día 1 ("Producto") del calendario semanal de Agente Cosmic — Veo (video mudo 9:16) + Lyria 3 (música) + TTS (narración) + overlay de texto animado (HTML/Playwright) ensamblado con ffmpeg.

**Architecture:** Dos generadores nuevos siguiendo el patrón ya establecido por `TextGenerator`/`ImageGenerator`: `ReelScriptGenerator` (Gemini genera hook/narración/prompts de escena/mood musical) y `ReelGenerator` (orquesta Veo + Lyria 3 + TTS + overlay HTML/Playwright + ensamblaje ffmpeg, sube el MP4 final a GCS). Se integra en `core/content_pipeline/tasks.py` como una tercera rama de `_generate_post_media()`, activa solo para el día 1 cuando no hay foto de producto real asignada ese día.

**Tech Stack:** `google-genai` SDK (Vertex AI: `models.generate_videos` para Veo, `interactions.create` para Lyria 3, `models.generate_content` con `SpeechConfig` para TTS), Playwright (overlay de texto, mismo patrón que `image_generator.py`), `ffmpeg` vía `subprocess` (ya instalado en `Dockerfile`/`Dockerfile.worker`), Django/RQ (mismo job síncrono que ya genera el resto del calendario).

## Global Constraints

- 1 reel por semana, día 1 ("Producto") únicamente — nunca otro día, nunca más de uno.
- Se genera automático, sin confirmación de costo, dentro del mismo job RQ que el resto del calendario (`job_timeout` a ajustar).
- Duración objetivo ~24s: 3 clips de Veo de ~8s.
- Si `_product_image_for_day(1, product_images_bytes)` devuelve una foto real, el día 1 se genera como imagen normal (`ImageGenerator.generate()`) — el reel se omite por completo, sin intentar usar la foto en Veo.
- Texto en pantalla: hook 0-3s, video limpio 3-21s, tag/CTA 21-24s. Sin subtítulos de la narración.
- Sin botón de regeneración manual para reels en esta versión — la acción `regenerate` de `post_action_api` debe rechazar posts con `format == 'reel'` con HTTP 400.
- Sin QC visual del video final con Gemini Vision (el único QC es sobre el guion, reutilizando la validación de nicho sensible ya existente para captions).
- Ningún test hace una llamada real a Veo/Lyria/TTS ni invoca el binario `ffmpeg` de verdad — todo mockeado, mismo estándar que el resto del proyecto.
- `email_daily.html`/`email_initial.html` NO necesitan cambios — ya no embeben `<img>` inline, solo enlazan a `calendar_review_url` con un botón CTA; el reel se ve ahí vía el nuevo reproductor de video.

---

## Mapa de archivos

```
core/content_pipeline/
  models.py                                    MODIFICAR: ContentPost.video_url, FORMAT_REEL
  migrations/000X_contentpost_video_url.py      CREAR (autogenerada)
  generators/
    reel_script_generator.py                    CREAR: ReelScriptGenerator
    reel_generator.py                            CREAR: ReelGenerator
  templates/content_pipeline/
    reel_hook.html                              CREAR: overlay hook (0-3s)
    reel_cta.html                                CREAR: overlay CTA (21-24s)
  tasks.py                                       MODIFICAR: _generate_post_media (3-tupla),
                                                  content_generation_task, generate_next_week,
                                                  _generate_missing_image
  tests/
    test_reel_script_generator.py                CREAR
    test_reel_generator.py                        CREAR
    test_tasks.py                                  MODIFICAR: casos de reel

core/brand_dna/
  views.py                                       MODIFICAR: download_post_image (rama reel),
                                                  post_action_api (bloquear regenerate en reels)
  templates/brand_dna/calendar_review.html        MODIFICAR: reproductor de video, badge, botones
  tests/test_views.py                             MODIFICAR: casos de reel

saas_chatbot/settings.py                          MODIFICAR: VERTEX_VIDEO_MODEL, VERTEX_MUSIC_MODEL,
                                                  VERTEX_TTS_MODEL
```

---

### Task 1: Modelo — `ContentPost.video_url` + `FORMAT_REEL`

**Files:**
- Modify: `core/content_pipeline/models.py:20-46` (clase `ContentPost`)
- Create: migración autogenerada en `core/content_pipeline/migrations/`

**Interfaces:**
- Produces: `ContentPost.FORMAT_REEL = 'reel'`, `ContentPost.video_url` (`URLField`, default `''`).

- [ ] **Step 1: Modificar el modelo**

En `core/content_pipeline/models.py`, dentro de la clase `ContentPost`:

```python
    FORMAT_SINGLE = 'single'
    FORMAT_CAROUSEL = 'carousel'
    FORMAT_REEL = 'reel'
    FORMAT_CHOICES = [
        (FORMAT_SINGLE, 'Imagen única'),
        (FORMAT_CAROUSEL, 'Carrusel'),
        (FORMAT_REEL, 'Reel'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    calendar = models.ForeignKey(ContentCalendar, on_delete=models.CASCADE, related_name='posts')
    day_number = models.IntegerField()
    caption = models.TextField()
    image_url = models.URLField(max_length=1000)
    # Carrusel (H20 + roadmap #5): lista ordenada de URLs de slides, vacia para posts
    # normales. image_url sigue siendo la portada/slide 1 para retrocompatibilidad
    # (email, thumbnail del dashboard, endpoint de descarga por default).
    image_urls = models.JSONField(default=list, blank=True)
    # Reel (roadmap #7): URL del MP4 final. image_url guarda el poster frame
    # (segundo 1 del video) para retrocompatibilidad con email/thumbnail.
    video_url = models.URLField(max_length=1000, blank=True, default='')
    format = models.CharField(max_length=20, choices=FORMAT_CHOICES, default=FORMAT_SINGLE)
    suggested_time = models.TimeField()
```

(El resto de la clase, desde `hashtags` en adelante, no cambia.)

- [ ] **Step 2: Generar y aplicar la migración**

Run: `docker compose exec -T backend python manage.py makemigrations content_pipeline`
Expected: crea un archivo `000X_contentpost_video_url.py` con una operación `AddField` para `video_url` y una `AlterField` para `format` (por el nuevo choice).

Run: `docker compose exec -T backend python manage.py migrate content_pipeline`
Expected: `Applying content_pipeline.000X_contentpost_video_url... OK`

- [ ] **Step 3: Commit**

```bash
git add core/content_pipeline/models.py core/content_pipeline/migrations/
git commit -m "feat(reels): agregar ContentPost.video_url y FORMAT_REEL"
```

---

### Task 2: Settings — modelos de Vertex AI para video/música/voz

**Files:**
- Modify: `saas_chatbot/settings.py:170-173`

**Interfaces:**
- Produces: `settings.VERTEX_VIDEO_MODEL`, `settings.VERTEX_MUSIC_MODEL`, `settings.VERTEX_TTS_MODEL`.

- [ ] **Step 1: Agregar los settings**

En `saas_chatbot/settings.py`, justo después de la línea `VERTEX_VERTEX_MODEL = 'publishers/google/models/gemini-2.5-flash'` (línea 173):

```python
VERTEX_VIDEO_MODEL = 'veo-3.0-fast-generate-001'
VERTEX_MUSIC_MODEL = 'lyria-3-clip-preview'
VERTEX_TTS_MODEL = 'publishers/google/models/gemini-2.5-flash-tts'
```

- [ ] **Step 2: Verificar que Django carga sin errores**

Run: `docker compose exec -T backend python manage.py check`
Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 3: Commit**

```bash
git add saas_chatbot/settings.py
git commit -m "feat(reels): settings de modelos Vertex AI para video/musica/voz"
```

---

### Task 3: `ReelScriptGenerator`

**Files:**
- Create: `core/content_pipeline/generators/reel_script_generator.py`
- Test: `core/content_pipeline/tests/test_reel_script_generator.py`

**Interfaces:**
- Consumes: `BrandDNA` (de `core.brand_dna.models`), un `post_data: dict` con al menos `caption` (viene de `TextGenerator.generate()`), nada más de tareas anteriores.
- Produces: `ReelScriptGenerator().generate(post_data: dict, brand_dna: BrandDNA) -> dict` retornando:
  ```python
  {
      "hook_text": str, "highlight_word": str, "tag_cta": str,
      "narration_script": str, "scene_prompts": list[str],  # siempre 3 elementos
      "music_mood": str,
  }
  ```
  Usado por Task 7 (`ReelGenerator.generate`) como su parámetro `script`.

- [ ] **Step 1: Escribir el test de fallback**

Crear `core/content_pipeline/tests/test_reel_script_generator.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings
from core.brand_dna.models import AnalysisJob, BrandDNA

pytestmark = pytest.mark.django_db


@pytest.fixture
def brand_dna():
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://tuwebmx.com')
    return BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno', 'web'],
        audience='PYMEs', tone='profesional', primary_colors=['#1a1a2e'],
    )


def _mock_vertex_client(json_text):
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = json_text
    mock_client.models.generate_content.return_value = mock_resp
    return mock_client


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_returns_fallback_on_api_error(brand_dna):
    from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
    post_data = {'caption': 'Descubre nuestra nueva coleccion de bolsos artesanales'}
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
        result = ReelScriptGenerator().generate(post_data, brand_dna)

    assert set(result.keys()) == {
        'hook_text', 'highlight_word', 'tag_cta', 'narration_script',
        'scene_prompts', 'music_mood',
    }
    assert len(result['scene_prompts']) == 3
    assert len(result['hook_text']) > 0
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_script_generator.py -v`
Expected: `FAIL` — `ModuleNotFoundError: No module named 'core.content_pipeline.generators.reel_script_generator'`

- [ ] **Step 3: Implementar `ReelScriptGenerator`**

Crear `core/content_pipeline/generators/reel_script_generator.py`:

```python
import json
import logging
import re
import google.genai as genai
from django.conf import settings
from core.brand_dna.models import BrandDNA
from core.shared.metrics_utils import track_external_api, record_tokens
from core.shared.rate_limiter import call_with_429_retry
from core.content_pipeline.generators.text_generator import _is_sensitive_niche, _strip_accents

logger = logging.getLogger(__name__)

_FALLBACK_SCENES = [
    "Overhead flat lay of the product on a clean surface with soft natural light, slow push-in camera movement, no people, no text, no logos.",
    "Close-up detail shot of the product with shallow depth of field, gentle rotation, warm bokeh background, no text, no logos.",
    "Product displayed in a lifestyle setting with soft ambient light, subtle camera pan, no people, no text, no logos.",
]

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
    "5. scene_prompts: exactamente 3 prompts EN INGLES para un generador de video (Veo), "
    "describiendo 3 escenas visuales secuenciales relacionadas al negocio. Cada prompt debe "
    "terminar con: 'no text, no logos, no people speaking to camera.'\n"
    "6. music_mood: 1 frase corta en ingles describiendo el mood musical (ej. "
    "'upbeat corporate, optimistic, minimal percussion').\n\n"
    "REGLA DE SEGURIDAD: si el negocio pertenece a un nicho sensible, usa tono neutro-positivo, "
    "sin promesas absolutas ('garantizado', 'aseguramos', '100%').\n\n"
    "Responde UNICAMENTE con este JSON (sin markdown):\n"
    '{{"hook_text":"...","highlight_word":"...","tag_cta":"...",'
    '"narration_script":"...","scene_prompts":["...","...","..."],"music_mood":"..."}}'
)


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


class ReelScriptGenerator:
    def generate(self, post_data: dict, brand_dna: BrandDNA) -> dict:
        caption = post_data.get('caption', '')
        fallback = {
            'hook_text': ' '.join(caption.split()[:6]) or 'Descubre algo nuevo',
            'highlight_word': (caption.split()[0] if caption.split() else 'nuevo'),
            'tag_cta': 'Contáctanos hoy',
            'narration_script': caption[:200],
            'scene_prompts': list(_FALLBACK_SCENES),
            'music_mood': f"background music matching a {brand_dna.tone} mood, instrumental only",
        }
        try:
            client = _vertex_client()
            prompt = _PROMPT.format(
                business_name=brand_dna.business_name,
                caption=caption,
                tone=brand_dna.tone,
                description=brand_dna.description,
            )

            def _call():
                with track_external_api('gemini', operation='reel_script'):
                    return client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, contents=prompt)
            resp = call_with_429_retry(_call, settings.VERTEX_TEXT_MODEL)
            record_tokens(resp, operation='reel_script',
                          prompt_preview=prompt[:500],
                          response_preview=resp.text[:500] if resp.text else '')
            raw = resp.text.strip()
            raw = re.sub(r'^```(?:json)?\n?', '', raw)
            raw = re.sub(r'\n?```$', '', raw)
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return fallback
            data = json.loads(match.group())
            scene_prompts = data.get('scene_prompts') or []
            if len(scene_prompts) != 3:
                scene_prompts = list(_FALLBACK_SCENES)
            result = {
                'hook_text': str(data.get('hook_text', '')).strip() or fallback['hook_text'],
                'highlight_word': str(data.get('highlight_word', '')).strip() or fallback['highlight_word'],
                'tag_cta': str(data.get('tag_cta', '')).strip() or fallback['tag_cta'],
                'narration_script': str(data.get('narration_script', '')).strip() or fallback['narration_script'],
                'scene_prompts': scene_prompts,
                'music_mood': str(data.get('music_mood', '')).strip() or fallback['music_mood'],
            }
            if _is_sensitive_niche(brand_dna):
                text_to_check = _strip_accents(f"{result['hook_text']} {result['narration_script']}".lower())
                banned = ('garantizado', 'garantizamos', 'asegurar', 'aseguramos', '100%')
                if any(word in text_to_check for word in banned):
                    logger.warning("ReelScriptGenerator: guion rechazado por lenguaje prohibido en nicho sensible, usando fallback")
                    return fallback
            return result
        except Exception as e:
            logger.warning(f"ReelScriptGenerator fallback: {e}")
            return fallback
```

- [ ] **Step 4: Correr el test y verificar que pasa**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_script_generator.py -v`
Expected: `1 passed`

- [ ] **Step 5: Agregar test de parseo de respuesta real**

Agregar a `core/content_pipeline/tests/test_reel_script_generator.py`:

```python
@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_parses_valid_gemini_response(brand_dna):
    from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
    post_data = {'caption': 'Bolsos artesanales hechos a mano'}
    response_json = (
        '{"hook_text":"Bolsos que cuentan tu historia","highlight_word":"historia",'
        '"tag_cta":"Compra ahora","narration_script":"Cada bolso es unico, hecho a mano con materiales de la mas alta calidad.",'
        '"scene_prompts":["scene1, no text, no logos, no people speaking to camera.",'
        '"scene2, no text, no logos, no people speaking to camera.",'
        '"scene3, no text, no logos, no people speaking to camera."],'
        '"music_mood":"warm acoustic, artisanal feel"}'
    )
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        result = ReelScriptGenerator().generate(post_data, brand_dna)

    assert result['hook_text'] == 'Bolsos que cuentan tu historia'
    assert result['highlight_word'] == 'historia'
    assert result['tag_cta'] == 'Compra ahora'
    assert len(result['scene_prompts']) == 3


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_uses_fallback_scenes_when_gemini_returns_wrong_count(brand_dna):
    from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
    post_data = {'caption': 'Bolsos artesanales'}
    response_json = (
        '{"hook_text":"H","highlight_word":"H","tag_cta":"C",'
        '"narration_script":"N","scene_prompts":["solo una escena"],"music_mood":"M"}'
    )
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        result = ReelScriptGenerator().generate(post_data, brand_dna)

    assert len(result['scene_prompts']) == 3


@pytest.fixture
def sensitive_brand_dna():
    job = AnalysisJob.objects.create(email='t@t.com', business_url='https://pediatra.com')
    return BrandDNA.objects.create(
        job=job, business_name='Pediatra Juan Gonzalez', business_url='https://pediatra.com',
        description='Atencion pediatrica para ninos de 0 a 12 anos',
        keywords=['pediatria', 'salud infantil'],
        audience='Padres y tutores de ninos', tone='profesional', primary_colors=['#1a1a2e'],
    )


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
)
def test_generate_rejects_banned_language_in_sensitive_niche(sensitive_brand_dna):
    from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
    post_data = {'caption': 'Atencion pediatrica de calidad'}
    response_json = (
        '{"hook_text":"Garantizamos tu salud","highlight_word":"Garantizamos","tag_cta":"Agenda hoy",'
        '"narration_script":"Aseguramos resultados en cada consulta.","scene_prompts":'
        '["s1, no text, no logos, no people speaking to camera.",'
        '"s2, no text, no logos, no people speaking to camera.",'
        '"s3, no text, no logos, no people speaking to camera."],"music_mood":"calm"}'
    )
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        result = ReelScriptGenerator().generate(post_data, sensitive_brand_dna)

    assert result['hook_text'] != 'Garantizamos tu salud'
```

- [ ] **Step 6: Correr todos los tests del archivo**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_script_generator.py -v`
Expected: `4 passed`

- [ ] **Step 7: Commit**

```bash
git add core/content_pipeline/generators/reel_script_generator.py core/content_pipeline/tests/test_reel_script_generator.py
git commit -m "feat(reels): ReelScriptGenerator — guion de hook/narracion/escenas via Gemini"
```

---

### Task 4: Templates de overlay + `ReelGenerator._render_text_overlay()`

**Files:**
- Create: `core/content_pipeline/templates/content_pipeline/reel_hook.html`
- Create: `core/content_pipeline/templates/content_pipeline/reel_cta.html`
- Create: `core/content_pipeline/generators/reel_generator.py` (clase `ReelGenerator`, solo este método por ahora)
- Test: `core/content_pipeline/tests/test_reel_generator.py` (solo `TestRenderTextOverlay` por ahora)

**Interfaces:**
- Consumes: nada de tasks anteriores (templates son estáticos).
- Produces: `ReelGenerator()._render_text_overlay(text: str, highlight_word: str, style: str, colors: list[str]) -> bytes` (PNG 1080x1920 con canal alpha). `style` es `'hook'` o `'cta'`. Usado por Task 7.

- [ ] **Step 1: Crear los templates HTML**

Basados en el estilo "pill resaltador" ya aprobado en el prototipo (`style1_pill.html` de la sesión de brainstorming). Crear `core/content_pipeline/templates/content_pipeline/reel_hook.html`:

```html
<!DOCTYPE html>
<html>
<head>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;700;900&display=swap');
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 1080px; height: 1920px; background: transparent; overflow: hidden; }
  .wrap {
    width: 100%; height: 100%; display: flex; flex-direction: column;
    align-items: center; justify-content: flex-start; padding-top: 220px;
  }
  .hook {
    font-family: 'Poppins', sans-serif; font-weight: 900; font-size: 88px;
    color: #ffffff; text-align: center; line-height: 1.15; width: 860px;
    text-shadow: 0 4px 24px rgba(0,0,0,0.55), 0 2px 6px rgba(0,0,0,0.7);
  }
  .highlight {
    position: relative; display: inline-block; color: #1a1a2e; padding: 4px 18px;
  }
  .highlight::before {
    content: ''; position: absolute; inset: 6px -6px; background: {{primary_color}};
    border-radius: 14px; transform: rotate(-1.5deg); z-index: -1;
    box-shadow: 0 6px 18px rgba(0,0,0,0.35);
  }
</style>
</head>
<body>
  <div class="wrap">
    <div class="hook">{{hook_html}}</div>
  </div>
</body>
</html>
```

Crear `core/content_pipeline/templates/content_pipeline/reel_cta.html`:

```html
<!DOCTYPE html>
<html>
<head>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Poppins:wght@400;700;900&display=swap');
  * { margin: 0; padding: 0; box-sizing: border-box; }
  html, body { width: 1080px; height: 1920px; background: transparent; overflow: hidden; }
  .wrap {
    width: 100%; height: 100%; display: flex; flex-direction: column;
    align-items: center; justify-content: flex-end; padding-bottom: 260px;
  }
  .cta {
    position: relative; font-family: 'Poppins', sans-serif; font-weight: 900; font-size: 72px;
    color: #1a1a2e; text-align: center; padding: 14px 44px;
  }
  .cta::before {
    content: ''; position: absolute; inset: 0; background: {{primary_color}};
    border-radius: 20px; z-index: -1;
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
  }
</style>
</head>
<body>
  <div class="wrap">
    <div class="cta">{{cta_text}}</div>
  </div>
</body>
</html>
```

- [ ] **Step 2: Escribir el test de `_render_text_overlay`**

Crear `core/content_pipeline/tests/test_reel_generator.py`:

```python
import io
from unittest.mock import patch, MagicMock
from django.test import override_settings
from PIL import Image


def _png_bytes(color=(30, 30, 60), size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', size, color).save(buf, format='PNG')
    return buf.getvalue()


class TestRenderTextOverlay:
    def _make_mock_playwright(self, screenshot_bytes: bytes):
        mock_page = MagicMock()
        mock_page.screenshot.return_value = screenshot_bytes
        mock_browser = MagicMock()
        mock_browser.new_page.return_value = mock_page
        mock_pw_instance = MagicMock()
        mock_pw_instance.chromium.launch.return_value = mock_browser
        mock_pw_context = MagicMock()
        mock_pw_context.__enter__.return_value = mock_pw_instance
        mock_pw_context.__exit__.return_value = False
        return mock_pw_context, mock_page

    def test_returns_screenshot_bytes_for_hook_style(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_png = _png_bytes()
        mock_pw_context, mock_page = self._make_mock_playwright(fake_png)
        with patch('core.content_pipeline.generators.reel_generator.sync_playwright', return_value=mock_pw_context):
            result = gen._render_text_overlay('Descubre algo nuevo', 'nuevo', 'hook', ['#e94560'])
        assert result == fake_png
        mock_page.screenshot.assert_called_once_with(omit_background=True)

    def test_returns_screenshot_bytes_for_cta_style(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_png = _png_bytes()
        mock_pw_context, mock_page = self._make_mock_playwright(fake_png)
        with patch('core.content_pipeline.generators.reel_generator.sync_playwright', return_value=mock_pw_context):
            result = gen._render_text_overlay('', '', 'cta', ['#e94560'], cta_text='Compra ahora')
        assert result == fake_png

    def test_highlight_word_is_wrapped_in_span(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_png = _png_bytes()
        mock_pw_context, mock_page = self._make_mock_playwright(fake_png)
        with patch('core.content_pipeline.generators.reel_generator.sync_playwright', return_value=mock_pw_context):
            gen._render_text_overlay('Descubre algo nuevo', 'nuevo', 'hook', ['#e94560'])
        html_sent = mock_page.set_content.call_args.args[0]
        assert '<span class="highlight">nuevo</span>' in html_sent
```

- [ ] **Step 3: Correr el test y verificar que falla**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -v`
Expected: `FAIL` — `ModuleNotFoundError: No module named 'core.content_pipeline.generators.reel_generator'`

- [ ] **Step 4: Implementar `ReelGenerator._render_text_overlay`**

Crear `core/content_pipeline/generators/reel_generator.py`:

```python
import html as _html
import logging
import os
import re
import google.genai as genai
from google.cloud import storage
from django.conf import settings
from playwright.sync_api import sync_playwright
from core.shared.metrics import GCS_OPERATIONS
from core.shared.metrics_utils import track_external_api, record_tokens

logger = logging.getLogger(__name__)

_TEMPLATE_MAP = {
    'hook': 'reel_hook.html',
    'cta': 'reel_cta.html',
}


def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION,
    )


class ReelGenerator:
    def __init__(self, bucket_name: str):
        self._bucket = bucket_name

    def _render_text_overlay(self, text: str, highlight_word: str, style: str, colors: list[str], cta_text: str = '') -> bytes:
        template_name = _TEMPLATE_MAP[style]
        template_path = os.path.normpath(os.path.join(
            os.path.dirname(__file__), '..', 'templates', 'content_pipeline', template_name,
        ))
        with open(template_path) as f:
            html = f.read()
        primary = colors[0] if colors else '#e94560'
        html = html.replace('{{primary_color}}', primary)

        if style == 'hook':
            escaped = _html.escape(text)
            if highlight_word:
                escaped_word = _html.escape(highlight_word)
                pattern = re.compile(re.escape(escaped_word), re.IGNORECASE)
                escaped = pattern.sub(f'<span class="highlight">{escaped_word}</span>', escaped, count=1)
            html = html.replace('{{hook_html}}', escaped)
        else:
            html = html.replace('{{cta_text}}', _html.escape(cta_text))

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox', '--disable-dev-shm-usage'],
            )
            page = browser.new_page(viewport={'width': 1080, 'height': 1920})
            page.set_content(html, wait_until='load')
            page.evaluate('document.fonts.ready')
            png_bytes = page.screenshot(omit_background=True)
            browser.close()

        return png_bytes
```

- [ ] **Step 5: Correr los tests y verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -v`
Expected: `3 passed`

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/generators/reel_generator.py core/content_pipeline/templates/content_pipeline/reel_hook.html core/content_pipeline/templates/content_pipeline/reel_cta.html core/content_pipeline/tests/test_reel_generator.py
git commit -m "feat(reels): overlay de texto hook/CTA via HTML+Playwright (ReelGenerator paso 1/4)"
```

---

### Task 5: `ReelGenerator` — generación de video (Veo), música (Lyria) y narración (TTS)

**Files:**
- Modify: `core/content_pipeline/generators/reel_generator.py`
- Modify: `core/content_pipeline/tests/test_reel_generator.py`

**Interfaces:**
- Produces:
  - `ReelGenerator()._generate_video_clips(scene_prompts: list[str]) -> list[bytes]` — puede devolver menos de 3 elementos si algún clip falla tras el reintento.
  - `ReelGenerator()._generate_music(music_mood: str) -> bytes | None`
  - `ReelGenerator()._generate_narration(narration_script: str) -> bytes | None`
  Usados por Task 7.

- [ ] **Step 1: Escribir los tests**

Agregar a `core/content_pipeline/tests/test_reel_generator.py`:

```python
class TestGenerateVideoClips:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_VIDEO_MODEL='veo-3.0-fast-generate-001',
    )
    def test_returns_one_clip_per_scene_prompt(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_video = b'fake-video-bytes'
        mock_video = MagicMock()
        mock_video.video_bytes = fake_video
        mock_generated = MagicMock()
        mock_generated.video = mock_video
        mock_op = MagicMock()
        mock_op.done = True
        mock_op.error = None
        mock_op.result.generated_videos = [mock_generated]
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_videos.return_value = mock_op
            clips = gen._generate_video_clips(['scene 1', 'scene 2', 'scene 3'])
        assert clips == [fake_video, fake_video, fake_video]

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_VIDEO_MODEL='veo-3.0-fast-generate-001',
    )
    def test_skips_clip_that_fails_after_retry(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_videos.side_effect = Exception('rejected')
            clips = gen._generate_video_clips(['scene 1'])
        assert clips == []


class TestGenerateMusic:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_MUSIC_MODEL='lyria-3-clip-preview',
    )
    def test_returns_audio_bytes_on_success(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_audio = MagicMock()
        mock_audio.data = b'fake-music-bytes'
        mock_interaction = MagicMock()
        mock_interaction.output_audio = mock_audio
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.interactions.create.return_value = mock_interaction
            result = gen._generate_music('upbeat corporate, optimistic')
        assert result == b'fake-music-bytes'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_MUSIC_MODEL='lyria-3-clip-preview',
    )
    def test_returns_none_on_api_error(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.interactions.create.side_effect = Exception('error')
            result = gen._generate_music('upbeat')
        assert result is None


class TestGenerateNarration:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TTS_MODEL='publishers/google/models/gemini-2.5-flash-tts',
    )
    def test_returns_audio_bytes_on_success(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_part = MagicMock()
        mock_part.inline_data.data = b'fake-narration-bytes'
        mock_resp = MagicMock()
        mock_resp.candidates = [MagicMock(content=MagicMock(parts=[mock_part]))]
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._generate_narration('Bienvenido a nuestra tienda.')
        assert result == b'fake-narration-bytes'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TTS_MODEL='publishers/google/models/gemini-2.5-flash-tts',
    )
    def test_returns_none_on_api_error(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('error')
            result = gen._generate_narration('texto')
        assert result is None
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -v`
Expected: `FAIL` — `AttributeError: 'ReelGenerator' object has no attribute '_generate_video_clips'` (y los otros 2 métodos)

- [ ] **Step 3: Implementar los 3 métodos**

Agregar a `core/content_pipeline/generators/reel_generator.py` (imports adicionales al inicio del archivo):

```python
import time
from google.genai import types
from core.shared.rate_limiter import call_with_429_retry
```

Agregar estos métodos a la clase `ReelGenerator`:

```python
    def _generate_video_clips(self, scene_prompts: list[str]) -> list[bytes]:
        clips = []
        for prompt in scene_prompts:
            clip = self._generate_single_clip(prompt)
            if clip is None:
                clip = self._generate_single_clip(prompt)  # 1 reintento
            if clip is not None:
                clips.append(clip)
            else:
                logger.warning(f"Clip de Veo fallido tras reintento, se omite: {prompt[:80]}")
        return clips

    def _generate_single_clip(self, prompt: str) -> bytes | None:
        try:
            client = _vertex_client()

            def _call():
                with track_external_api('veo', operation='video_generate'):
                    return client.models.generate_videos(
                        model=settings.VERTEX_VIDEO_MODEL,
                        prompt=prompt,
                        config=types.GenerateVideosConfig(
                            aspect_ratio='9:16',
                            duration_seconds=8,
                            number_of_videos=1,
                            generate_audio=False,
                        ),
                    )
            operation = call_with_429_retry(_call, settings.VERTEX_VIDEO_MODEL)
            client = _vertex_client()
            while not operation.done:
                time.sleep(10)
                operation = client.operations.get(operation)
            if operation.error:
                logger.warning(f"Veo devolvió error: {operation.error}")
                return None
            generated = operation.result.generated_videos
            if not generated:
                return None
            return generated[0].video.video_bytes
        except Exception as e:
            logger.warning(f"Veo clip generation failed: {e}")
            return None

    def _generate_music(self, music_mood: str) -> bytes | None:
        try:
            client = _vertex_client()
            with track_external_api('lyria', operation='music_generate'):
                interaction = client.interactions.create(
                    model=settings.VERTEX_MUSIC_MODEL,
                    input=f"Instrumental only, no vocals. {music_mood}",
                    response_modalities=['audio'],
                )
            audio = getattr(interaction, 'output_audio', None)
            if audio is not None and getattr(audio, 'data', None):
                return audio.data
            return None
        except Exception as e:
            logger.warning(f"Lyria music generation failed (reel sin musica): {e}")
            return None

    def _generate_narration(self, narration_script: str) -> bytes | None:
        try:
            client = _vertex_client()
            with track_external_api('gemini', operation='tts_generate'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TTS_MODEL,
                    contents=narration_script,
                    config=types.GenerateContentConfig(response_modalities=['AUDIO']),
                )
            for part in resp.candidates[0].content.parts:
                if part.inline_data:
                    return part.inline_data.data
            return None
        except Exception as e:
            logger.warning(f"TTS narration generation failed (reel sin narracion): {e}")
            return None
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -v`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add core/content_pipeline/generators/reel_generator.py core/content_pipeline/tests/test_reel_generator.py
git commit -m "feat(reels): generacion de clips Veo, musica Lyria 3 y narracion TTS (ReelGenerator paso 2/4)"
```

---

### Task 6: `ReelGenerator` — ensamblaje ffmpeg y poster frame

**Files:**
- Modify: `core/content_pipeline/generators/reel_generator.py`
- Modify: `core/content_pipeline/tests/test_reel_generator.py`

**Interfaces:**
- Produces:
  - `ReelGenerator()._assemble_reel(clips: list[bytes], music: bytes | None, narration: bytes | None, hook_png: bytes, cta_png: bytes) -> bytes` (MP4 final, 1080x1920).
  - `ReelGenerator()._extract_poster_frame(video_bytes: bytes) -> bytes` (PNG del segundo 1).
  Usados por Task 7. `subprocess.run` se mockea siempre en tests — nunca se invoca ffmpeg real.

- [ ] **Step 1: Escribir los tests**

Agregar a `core/content_pipeline/tests/test_reel_generator.py`:

```python
class TestAssembleReel:
    def test_calls_ffmpeg_and_returns_output_bytes(self, tmp_path):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        def fake_run(cmd, *args, **kwargs):
            output_path = cmd[-1]
            with open(output_path, 'wb') as f:
                f.write(fake_output)
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run', side_effect=fake_run) as mock_run:
            result = gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=b'music-bytes',
                narration=b'narration-bytes',
                hook_png=b'hook-png-bytes',
                cta_png=b'cta-png-bytes',
            )
        assert result == fake_output
        mock_run.assert_called_once()

    def test_works_without_music_or_narration(self, tmp_path):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_output = b'fake-mp4-bytes'

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[-1], 'wb') as f:
                f.write(fake_output)
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run', side_effect=fake_run):
            result = gen._assemble_reel(
                clips=[b'clip1', b'clip2', b'clip3'],
                music=None,
                narration=None,
                hook_png=b'hook-png-bytes',
                cta_png=b'cta-png-bytes',
            )
        assert result == fake_output


class TestExtractPosterFrame:
    def test_calls_ffmpeg_and_returns_frame_bytes(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        fake_frame = b'fake-frame-png-bytes'

        def fake_run(cmd, *args, **kwargs):
            with open(cmd[-1], 'wb') as f:
                f.write(fake_frame)
            return MagicMock(returncode=0)

        with patch('core.content_pipeline.generators.reel_generator.subprocess.run', side_effect=fake_run):
            result = gen._extract_poster_frame(b'fake-video-bytes')
        assert result == fake_frame
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -v`
Expected: `FAIL` — `AttributeError: 'ReelGenerator' object has no attribute '_assemble_reel'`

- [ ] **Step 3: Implementar los 2 métodos**

Agregar al inicio de `core/content_pipeline/generators/reel_generator.py`:

```python
import subprocess
import tempfile
```

Agregar estos métodos a la clase `ReelGenerator`:

```python
    def _assemble_reel(self, clips: list[bytes], music: bytes | None, narration: bytes | None,
                        hook_png: bytes, cta_png: bytes) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            clip_paths = []
            for i, clip_bytes in enumerate(clips):
                path = os.path.join(tmp, f'clip{i}.mp4')
                with open(path, 'wb') as f:
                    f.write(clip_bytes)
                clip_paths.append(path)

            concat_list_path = os.path.join(tmp, 'concat.txt')
            with open(concat_list_path, 'w') as f:
                for p in clip_paths:
                    f.write(f"file '{p}'\n")

            concat_path = os.path.join(tmp, 'concat.mp4')
            subprocess.run(
                ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', concat_list_path,
                 '-c', 'copy', concat_path],
                check=True, capture_output=True,
            )

            hook_path = os.path.join(tmp, 'hook.png')
            with open(hook_path, 'wb') as f:
                f.write(hook_png)
            cta_path = os.path.join(tmp, 'cta.png')
            with open(cta_path, 'wb') as f:
                f.write(cta_png)

            duration = len(clips) * 8
            cta_start = max(0, duration - 3)
            overlay_path = os.path.join(tmp, 'overlay.mp4')
            subprocess.run(
                ['ffmpeg', '-y', '-i', concat_path, '-i', hook_path, '-i', cta_path,
                 '-filter_complex',
                 f"[0:v][1:v]overlay=0:0:enable='between(t,0,3)'[v1];"
                 f"[v1][2:v]overlay=0:0:enable='between(t,{cta_start},{duration})'[v2]",
                 '-map', '[v2]', '-t', str(duration), '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                 overlay_path],
                check=True, capture_output=True,
            )

            audio_inputs = []
            audio_paths = []
            if music is not None:
                music_path = os.path.join(tmp, 'music.mp3')
                with open(music_path, 'wb') as f:
                    f.write(music)
                audio_paths.append(music_path)
            if narration is not None:
                narration_path = os.path.join(tmp, 'narration.mp3')
                with open(narration_path, 'wb') as f:
                    f.write(narration)
                audio_paths.append(narration_path)

            output_path = os.path.join(tmp, 'output.mp4')
            if not audio_paths:
                subprocess.run(['ffmpeg', '-y', '-i', overlay_path, '-c', 'copy', output_path],
                                check=True, capture_output=True)
            else:
                cmd = ['ffmpeg', '-y', '-i', overlay_path]
                for p in audio_paths:
                    cmd += ['-i', p]
                if len(audio_paths) == 2:
                    filter_complex = '[1:a][2:a]amix=inputs=2:duration=shortest[a]'
                    cmd += ['-filter_complex', filter_complex, '-map', '0:v', '-map', '[a]']
                else:
                    cmd += ['-map', '0:v', '-map', '1:a']
                cmd += ['-t', str(duration), '-c:v', 'copy', '-c:a', 'aac', '-shortest', output_path]
                subprocess.run(cmd, check=True, capture_output=True)

            with open(output_path, 'rb') as f:
                return f.read()

    def _extract_poster_frame(self, video_bytes: bytes) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            video_path = os.path.join(tmp, 'video.mp4')
            with open(video_path, 'wb') as f:
                f.write(video_bytes)
            frame_path = os.path.join(tmp, 'frame.png')
            subprocess.run(
                ['ffmpeg', '-y', '-ss', '1', '-i', video_path, '-vframes', '1', frame_path],
                check=True, capture_output=True,
            )
            with open(frame_path, 'rb') as f:
                return f.read()
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -v`
Expected: `13 passed`

- [ ] **Step 5: Commit**

```bash
git add core/content_pipeline/generators/reel_generator.py core/content_pipeline/tests/test_reel_generator.py
git commit -m "feat(reels): ensamblaje ffmpeg (concat+overlay+mezcla de audio) y poster frame (ReelGenerator paso 3/4)"
```

---

### Task 7: `ReelGenerator` — orquestación (`generate()`) y subida a GCS

**Files:**
- Modify: `core/content_pipeline/generators/reel_generator.py`
- Modify: `core/content_pipeline/tests/test_reel_generator.py`

**Interfaces:**
- Consumes: `script: dict` con las claves producidas por `ReelScriptGenerator` (Task 3): `hook_text`, `highlight_word`, `tag_cta`, `narration_script`, `scene_prompts`, `music_mood`.
- Produces: `ReelGenerator().generate(script: dict, colors: list[str], filename_prefix: str) -> tuple[str, str]` — `(video_url, poster_url)`, ambos `''` si falla. Usado por Task 8 (`tasks.py::_generate_post_media`).

- [ ] **Step 1: Escribir los tests**

Agregar a `core/content_pipeline/tests/test_reel_generator.py`:

```python
_FAKE_SCRIPT = {
    'hook_text': 'Descubre algo nuevo', 'highlight_word': 'nuevo',
    'tag_cta': 'Compra ahora', 'narration_script': 'Bienvenido a nuestra tienda.',
    'scene_prompts': ['scene 1', 'scene 2', 'scene 3'],
    'music_mood': 'upbeat, optimistic',
}


class TestGenerate:
    def test_returns_video_and_poster_urls_on_success(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_video_clips', return_value=[b'c1', b'c2', b'c3']), \
             patch.object(gen, '_generate_music', return_value=b'music'), \
             patch.object(gen, '_generate_narration', return_value=b'narration'), \
             patch.object(gen, '_render_text_overlay', return_value=b'overlay-png'), \
             patch.object(gen, '_assemble_reel', return_value=b'final-mp4'), \
             patch.object(gen, '_extract_poster_frame', return_value=b'poster-png'), \
             patch.object(gen, '_upload_video_to_storage', return_value='https://storage.test/reel.mp4') as mock_up_video, \
             patch.object(gen, '_upload_to_storage', return_value='https://storage.test/poster.png') as mock_up_poster:
            video_url, poster_url = gen.generate(_FAKE_SCRIPT, ['#1a1a2e'], 'job1-day1')

        assert video_url == 'https://storage.test/reel.mp4'
        assert poster_url == 'https://storage.test/poster.png'
        mock_up_video.assert_called_once_with(b'final-mp4', 'job1-day1')
        mock_up_poster.assert_called_once_with(b'poster-png', 'job1-day1-poster')

    def test_returns_empty_strings_when_fewer_than_3_clips_generated(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_video_clips', return_value=[b'c1', b'c2']):
            video_url, poster_url = gen.generate(_FAKE_SCRIPT, ['#1a1a2e'], 'job1-day1')
        assert (video_url, poster_url) == ('', '')

    def test_returns_empty_strings_when_assembly_raises(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_video_clips', return_value=[b'c1', b'c2', b'c3']), \
             patch.object(gen, '_generate_music', return_value=None), \
             patch.object(gen, '_generate_narration', return_value=None), \
             patch.object(gen, '_render_text_overlay', return_value=b'overlay-png'), \
             patch.object(gen, '_assemble_reel', side_effect=Exception('ffmpeg error')):
            video_url, poster_url = gen.generate(_FAKE_SCRIPT, ['#1a1a2e'], 'job1-day1')
        assert (video_url, poster_url) == ('', '')


class TestUploadVideoToStorage:
    def test_uploads_with_video_mimetype_and_cache_busting(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        mock_blob = MagicMock()
        mock_blob.public_url = 'https://storage.googleapis.com/test-bucket/reels/job1-day1.mp4'
        mock_bucket = MagicMock()
        mock_bucket.blob.return_value = mock_blob
        mock_client = MagicMock()
        mock_client.bucket.return_value = mock_bucket
        with patch('core.content_pipeline.generators.reel_generator.storage.Client', return_value=mock_client):
            url = gen._upload_video_to_storage(b'fake-mp4-bytes', 'job1-day1')
        mock_blob.upload_from_string.assert_called_once_with(b'fake-mp4-bytes', content_type='video/mp4')
        assert url.startswith('https://storage.googleapis.com/test-bucket/reels/job1-day1.mp4?v=')
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -v`
Expected: `FAIL` — `AttributeError: 'ReelGenerator' object has no attribute 'generate'`

- [ ] **Step 3: Implementar `generate`, `_upload_to_storage` y `_upload_video_to_storage`**

Agregar al inicio de `core/content_pipeline/generators/reel_generator.py`:

```python
import time as _time
```

Agregar estos métodos a la clase `ReelGenerator`:

```python
    def generate(self, script: dict, colors: list[str], filename_prefix: str) -> tuple[str, str]:
        try:
            clips = self._generate_video_clips(script['scene_prompts'])
            if len(clips) < 3:
                logger.warning(f"Reel abortado: solo {len(clips)}/3 clips de Veo generados")
                return '', ''

            music = self._generate_music(script['music_mood'])
            narration = self._generate_narration(script['narration_script'])
            hook_png = self._render_text_overlay(script['hook_text'], script['highlight_word'], 'hook', colors)
            cta_png = self._render_text_overlay('', '', 'cta', colors, cta_text=script['tag_cta'])

            final_video = self._assemble_reel(clips, music, narration, hook_png, cta_png)
            poster = self._extract_poster_frame(final_video)

            video_url = self._upload_video_to_storage(final_video, filename_prefix)
            poster_url = self._upload_to_storage(poster, f'{filename_prefix}-poster')
            return video_url, poster_url
        except Exception as e:
            logger.error(f"ReelGenerator.generate error: {e}")
            return '', ''

    def _upload_to_storage(self, image_bytes: bytes, filename: str) -> str:
        with track_external_api('gcs'):
            client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
            bucket = client.bucket(self._bucket)
            blob = bucket.blob(f'posts/{filename}.png')
            blob.upload_from_string(image_bytes, content_type='image/png')
        GCS_OPERATIONS.labels(operation='upload').inc()
        return f'{blob.public_url}?v={int(_time.time())}'

    def _upload_video_to_storage(self, video_bytes: bytes, filename: str) -> str:
        with track_external_api('gcs'):
            client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
            bucket = client.bucket(self._bucket)
            blob = bucket.blob(f'reels/{filename}.mp4')
            blob.upload_from_string(video_bytes, content_type='video/mp4')
        GCS_OPERATIONS.labels(operation='upload').inc()
        return f'{blob.public_url}?v={int(_time.time())}'
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_reel_generator.py -v`
Expected: `17 passed`

- [ ] **Step 5: Commit**

```bash
git add core/content_pipeline/generators/reel_generator.py core/content_pipeline/tests/test_reel_generator.py
git commit -m "feat(reels): orquestacion ReelGenerator.generate() con contrato de fallback (ReelGenerator paso 4/4)"
```

---

### Task 8: Wiring en `tasks.py`

**Files:**
- Modify: `core/content_pipeline/tasks.py`
- Modify: `core/content_pipeline/tests/test_tasks.py`

**Interfaces:**
- Consumes: `ReelScriptGenerator().generate(post_data, brand_dna)` (Task 3), `ReelGenerator().generate(script, colors, filename_prefix)` (Task 7).
- Produces: `_generate_post_media(...)` retorna una tupla de 3 `(image_url, image_urls, video_url)` en vez de 2 — cambio de firma que afecta a los 4 call sites existentes.

- [ ] **Step 1: Escribir los tests de la nueva rama**

Agregar a `core/content_pipeline/tests/test_tasks.py` (junto a los tests de carrusel ya existentes):

```python
_MOCK_POSTS_WITH_REEL = [
    {'caption': f'Post {i}', 'hashtags': ['#test'], 'suggested_time': '19:00',
     'format': 'reel' if i == 1 else 'single'}
    for i in range(1, 8)
]


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    DEFAULT_FROM_EMAIL='noreply@test.com',
)
def test_content_generation_uses_reel_for_day_1_without_product_photo(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.ReelScriptGenerator') as MockScript, \
         patch('core.content_pipeline.tasks.ReelGenerator') as MockReel, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
        MockText.return_value.generate.return_value = _MOCK_POSTS_WITH_REEL
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'
        MockScript.return_value.generate.return_value = {'hook_text': 'H', 'scene_prompts': ['a', 'b', 'c']}
        MockReel.return_value.generate.return_value = ('https://storage.test/reel.mp4', 'https://storage.test/poster.png')

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    assert MockReel.return_value.generate.call_count == 1
    assert MockImage.return_value.generate.call_count == 6
    posts = ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna)
    reel_post = posts.get(day_number=1)
    assert reel_post.format == 'reel'
    assert reel_post.video_url == 'https://storage.test/reel.mp4'
    assert reel_post.image_url == 'https://storage.test/poster.png'


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    DEFAULT_FROM_EMAIL='noreply@test.com',
)
def test_content_generation_falls_back_to_image_when_reel_generation_fails(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.ReelScriptGenerator') as MockScript, \
         patch('core.content_pipeline.tasks.ReelGenerator') as MockReel, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'):
        MockText.return_value.generate.return_value = _MOCK_POSTS_WITH_REEL
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/fallback.jpg'
        MockScript.return_value.generate.return_value = {'hook_text': 'H', 'scene_prompts': ['a', 'b', 'c']}
        MockReel.return_value.generate.return_value = ('', '')

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    posts = ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna)
    day1 = posts.get(day_number=1)
    assert day1.format == 'reel'
    assert day1.video_url == ''
    assert day1.image_url == 'https://storage.googleapis.com/test/fallback.jpg'


@override_settings(
    GOOGLE_CLOUD_PROJECT='agente-cosmic',
    GOOGLE_CLOUD_LOCATION='us-central1',
    VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    VERTEX_IMAGE_MODEL='publishers/google/models/gemini-2.5-flash-image',
    VERTEX_VISION_MODEL='publishers/google/models/gemini-2.5-flash',
    GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    DEFAULT_FROM_EMAIL='noreply@test.com',
)
def test_content_generation_skips_reel_when_day1_has_product_photo(job_with_dna):
    with patch('core.content_pipeline.tasks.TextGenerator') as MockText, \
         patch('core.content_pipeline.tasks.ImageGenerator') as MockImage, \
         patch('core.content_pipeline.tasks.ReelGenerator') as MockReel, \
         patch('core.content_pipeline.tasks.EmailSender'), \
         patch('core.content_pipeline.tasks.schedule_daily_emails'), \
         patch('core.content_pipeline.tasks._load_product_images', return_value=[b'foto-dia-1']):
        MockText.return_value.generate.return_value = [dict(p) for p in _MOCK_POSTS_WITH_REEL]
        MockImage.return_value.generate.return_value = 'https://storage.googleapis.com/test/img.jpg'

        from core.content_pipeline.tasks import content_generation_task
        content_generation_task(str(job_with_dna.id))

    MockReel.return_value.generate.assert_not_called()
    posts = ContentPost.objects.filter(calendar__brand_dna__job=job_with_dna)
    assert posts.get(day_number=1).format == 'single'
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_tasks.py -k reel -v`
Expected: `FAIL` — `ImportError: cannot import name 'ReelScriptGenerator'` (o similar, `tasks.py` no la importa todavía)

- [ ] **Step 3: Modificar `tasks.py`**

Agregar imports al inicio de `core/content_pipeline/tasks.py`:

```python
from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
from core.content_pipeline.generators.reel_generator import ReelGenerator
```

Reemplazar `_generate_post_media` (definida en una sesión anterior) por esta versión de 3 valores:

```python
def _generate_post_media(image_gen: ImageGenerator, reel_script_gen: ReelScriptGenerator, reel_gen: ReelGenerator,
                          fmt: str, filename: str, brand_dna=None, post_data: dict = None,
                          max_qc_retries: int = 2, **kwargs) -> tuple[str, list[str], str]:
    """Genera el/los medio(s) de un post segun su formato. Retorna
    (image_url, image_urls, video_url) — image_url es siempre la portada
    (slide 1 del carrusel, poster frame del reel) para retrocompatibilidad."""
    if fmt == ContentPost.FORMAT_REEL:
        script = reel_script_gen.generate(post_data, brand_dna)
        video_url, poster_url = reel_gen.generate(script=script, colors=kwargs.get('colors', []), filename_prefix=filename)
        if not video_url:
            url = image_gen.generate(filename=filename, max_qc_retries=max_qc_retries, **kwargs)
            return url, [], ''
        return poster_url, [], video_url
    if fmt == ContentPost.FORMAT_CAROUSEL:
        urls = image_gen.generate_carousel(filename_prefix=filename, max_qc_retries=max_qc_retries, **kwargs)
        return (urls[0] if urls else ''), urls, ''
    url = image_gen.generate(filename=filename, max_qc_retries=max_qc_retries, **kwargs)
    return url, [], ''
```

En `content_generation_task`, después de la línea `product_images_bytes = _load_product_images(calendar.active_product_images)`:

```python
        _disable_carousel_if_full_product_week(posts_data, product_images_bytes)
        if _product_image_for_day(1, product_images_bytes) is not None:
            posts_data[0]['format'] = ContentPost.FORMAT_SINGLE
```

Modificar el bloque del loop principal (reemplaza la llamada existente a `_generate_post_media`):

```python
        reel_script_gen = ReelScriptGenerator()
        reel_gen = ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)

        for i, post_data in enumerate(posts_data, start=1):
            scheduled = scheduled_dates[i - 1]
            day_product = _product_image_for_day(i, product_images_bytes)
            image_url, image_urls, video_url = _generate_post_media(
                image_gen, reel_script_gen, reel_gen,
                fmt=post_data.get('format', ContentPost.FORMAT_SINGLE),
                filename=f"{job_id}-day{i}",
                caption=post_data['caption'],
                colors=brand_dna.primary_colors,
                tone=brand_dna.tone,
                brand_name=brand_dna.business_name,
                keywords=brand_dna.keywords,
                description=brand_dna.description,
                audience=brand_dna.audience,
                product_image_bytes=day_product,
                brand_dna=brand_dna,
                post_data=post_data,
            )

            ContentPost.objects.create(
                calendar=calendar,
                day_number=i,
                caption=post_data['caption'],
                image_url=image_url,
                image_urls=image_urls,
                video_url=video_url,
                format=post_data.get('format', ContentPost.FORMAT_SINGLE),
                suggested_time=scheduled.astimezone(MEXICO_TZ).time(),
                hashtags=post_data.get('hashtags', []),
                scheduled_at=scheduled,
            )
            job.update_progress(AnalysisJob.STAGE_CONTENT, 87 + int(8 * i / total))
```

**Nota:** `ReelGenerator.generate()`/`ImageGenerator.generate()`/`generate_carousel()` no aceptan `brand_dna`/`post_data` como kwargs — `_generate_post_media` los consume solo para la rama `reel` y no los reenvía a `image_gen.generate(**kwargs)`. Como `kwargs` en la firma de `_generate_post_media` ya excluye `brand_dna`/`post_data` (son parámetros nombrados explícitos, no parte de `**kwargs`), esto ya funciona correctamente tal como está escrito arriba — verificar en el test del Step 4 que no se cuelan como argumentos inesperados a `image_gen.generate`.

Aplicar el mismo cambio de firma (3 valores) en `_generate_missing_image` y en `generate_next_week` — mismo patrón, agregando `reel_script_gen`/`reel_gen` y pasando `video_url` al `ContentPost` correspondiente. En `generate_next_week`, la decisión de "día 1 de la semana = reel salvo foto real" usa `i == 1` (relativo a la semana, igual que hoy hace `_product_image_for_day(i, ...)`).

En `_generate_missing_image`, agregar la misma lógica de `reel_script_gen`/`reel_gen` y actualizar la llamada:

```python
        post.image_url, post.image_urls, post.video_url = _generate_post_media(
            image_gen, ReelScriptGenerator(), ReelGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET),
            fmt=post.format,
            filename=f"{job_id}-day{post.day_number}",
            caption=post.caption,
            colors=brand_dna.primary_colors,
            tone=brand_dna.tone,
            brand_name=brand_dna.business_name,
            keywords=brand_dna.keywords,
            description=brand_dna.description,
            audience=brand_dna.audience,
            product_image_bytes=product_image_bytes,
            brand_dna=brand_dna,
            post_data={'caption': post.caption},
        )
        post.save(update_fields=['image_url', 'image_urls', 'video_url'])
```

- [ ] **Step 4: Correr los tests y verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_tasks.py -v`
Expected: todos los tests pasan (los ya existentes de carrusel/imagen siguen pasando porque el 3er valor de retorno simplemente se ignora donde no se usa, y los nuevos de reel pasan).

- [ ] **Step 5: Actualizar el call site de `views.py::post_action_api` (regeneración)**

En `core/brand_dna/views.py`, dentro de la acción `'regenerate'`, el import y la llamada a `_generate_post_media` necesitan el nuevo argumento posicional. Esto se corrige en el Task 9 junto con el bloqueo de regeneración para reels — no lo toques todavía en este paso.

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/tasks.py core/content_pipeline/tests/test_tasks.py
git commit -m "feat(reels): wiring en tasks.py — dia 1 usa ReelGenerator salvo foto de producto real"
```

---

### Task 9: `views.py` — descarga de reel y bloqueo de regeneración

**Files:**
- Modify: `core/brand_dna/views.py`
- Modify: `core/brand_dna/tests/test_views.py`

**Interfaces:**
- Consumes: `ContentPost.FORMAT_REEL`, `ContentPost.video_url` (Task 1).
- Produces: `download_post_image` sirve el MP4 para reels; `post_action_api` rechaza `action='regenerate'` con 400 para posts `format == 'reel'`.

- [ ] **Step 1: Escribir los tests**

Agregar a `core/brand_dna/tests/test_views.py`:

```python
def test_download_post_image_returns_mp4_for_reel(client, user, job_with_calendar):
    post = job_with_calendar.brand_dna.calendar.posts.get(day_number=1)
    post.format = 'reel'
    post.video_url = 'https://example.com/reel.mp4'
    post.save(update_fields=['format', 'video_url'])
    client.force_login(user)
    fake_response = MagicMock()
    fake_response.read.return_value = b'fake-mp4-bytes'
    fake_response.__enter__.return_value = fake_response
    with patch('urllib.request.urlopen', return_value=fake_response):
        response = client.get(f'/api/post/{post.id}/download/')
    assert response.status_code == 200
    assert response['Content-Type'] == 'video/mp4'
    assert 'reel.mp4' in response['Content-Disposition']
    assert response.content == b'fake-mp4-bytes'


def test_regenerate_action_blocked_for_reel_posts(client, user, job_with_calendar):
    post = job_with_calendar.brand_dna.calendar.posts.get(day_number=1)
    post.format = 'reel'
    post.video_url = 'https://example.com/reel.mp4'
    post.save(update_fields=['format', 'video_url'])
    client.force_login(user)
    response = client.post(
        f'/api/post/{post.id}/action/',
        data=json.dumps({'action': 'regenerate', 'value': 'Hazlo mas corto'}),
        content_type='application/json',
    )
    assert response.status_code == 400
    post.refresh_from_db()
    assert post.video_url == 'https://example.com/reel.mp4'
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -k "reel" -v`
Expected: `FAIL` — `test_download_post_image_returns_mp4_for_reel` da 200 con `Content-Type: image/png` en vez de `video/mp4`; `test_regenerate_action_blocked_for_reel_posts` da 200 en vez de 400.

- [ ] **Step 3: Modificar `download_post_image`**

En `core/brand_dna/views.py`, agregar una rama ANTES de la rama de carrusel (que ya existe de una sesión anterior):

```python
    if post.format == ContentPost.FORMAT_REEL and post.video_url:
        try:
            with urllib.request.urlopen(post.video_url, timeout=30) as resp:
                data = resp.read()
        except Exception as e:
            logger.warning(f"download_post_image: no se pudo obtener el reel de {post.video_url}: {e}")
            raise Http404
        response = HttpResponse(data, content_type='video/mp4')
        response['Content-Disposition'] = f'attachment; filename="post-dia-{post.day_number}-reel.mp4"'
        return response
```

(Va justo después de la línea `post = get_object_or_404(...)` y del chequeo `if not post.image_url: raise Http404` — nota que para un reel exitoso `image_url` SÍ tiene el poster frame, así que ese chequeo no bloquea el flujo.)

- [ ] **Step 4: Modificar `post_action_api` para bloquear `regenerate` en reels**

En `core/brand_dna/views.py`, al inicio del bloque `if action == 'regenerate':`, antes de la validación de `value`:

```python
    if action == 'regenerate':
        if post.format == ContentPost.FORMAT_REEL:
            return JsonResponse({'error': 'La regeneración no está disponible para reels todavía.'}, status=400)
        if not value:
```

(Nota: la línea `if not value:` ya existe — solo se agrega el chequeo de `FORMAT_REEL` antes.)

- [ ] **Step 5: Correr los tests y verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -v`
Expected: todos pasan.

- [ ] **Step 6: Commit**

```bash
git add core/brand_dna/views.py core/brand_dna/tests/test_views.py
git commit -m "feat(reels): descarga de MP4 y bloqueo de regeneracion manual para posts reel"
```

---

### Task 10: UI — `calendar_review.html`

**Files:**
- Modify: `core/brand_dna/templates/brand_dna/calendar_review.html`

**Interfaces:**
- Consumes: `post.format == 'reel'`, `post.video_url`, `post.image_url` (poster).

- [ ] **Step 1: Agregar CSS para el badge de reel**

En el bloque `<style>`, junto al `.carousel-badge` ya existente:

```css
    .reel-badge { position: absolute; top: 8px; left: 8px; background: rgba(0,0,0,0.65); color: #fff; font-size: 0.7rem; font-weight: 700; padding: 3px 8px; border-radius: 20px; z-index: 2; pointer-events: none; }
    .post-img video { width: 100%; height: 100%; object-fit: cover; }
```

- [ ] **Step 2: Agregar la rama de reel en el markup de la card**

Modificar el bloque `<div class="post-img...">` para agregar una rama ANTES de la de carrusel:

```html
      <div class="post-img{% if post.format == 'carousel' and post.image_urls %} carousel-grid{% endif %}">
        {% if post.format == 'reel' and post.video_url %}
          <video controls poster="{{ post.image_url }}" style="width:100%;height:100%;object-fit:cover;">
            <source src="{{ post.video_url }}" type="video/mp4">
          </video>
          <div class="reel-badge">🎬 Reel</div>
        {% elif post.format == 'carousel' and post.image_urls %}
          {% for slide_url in post.image_urls %}
          <a href="{{ slide_url }}" target="_blank" rel="noopener" title="Slide {{ forloop.counter }}">
            <img src="{{ slide_url }}" alt="Slide {{ forloop.counter }} del día {{ post.day_number }}" loading="lazy" style="cursor:zoom-in;">
          </a>
          {% endfor %}
          <div class="carousel-badge">🎠 Carrusel</div>
        {% elif post.image_url %}
          <a href="{{ post.image_url }}" target="_blank" rel="noopener" title="Ver imagen completa" style="display:block;width:100%;height:100%;">
            <img src="{{ post.image_url }}" alt="Día {{ post.day_number }}" loading="lazy" style="cursor:zoom-in;">
          </a>
        {% else %}
          📸
        {% endif %}
      </div>
```

- [ ] **Step 3: Cambiar el texto del botón de descarga y ocultar el de regenerar para reels**

Modificar la línea del botón de descarga:

```html
          <button class="btn-action btn-download" onclick="downloadImage('{{ post.id }}')">⬇ Descargar {% if post.format == 'carousel' %}carrusel (.zip){% elif post.format == 'reel' %}reel (.mp4){% else %}imagen{% endif %}</button>
```

Modificar el botón de regenerar para que no se muestre en posts de tipo reel (agregar la condición al `style` existente que ya oculta el botón cuando `post.downloaded_at`):

```html
          <button class="btn-action btn-regen" id="regen-btn-{{ post.id }}" onclick="toggleRegen('{{ post.id }}')" style="{% if post.downloaded_at or post.format == 'reel' %}display:none;{% endif %}">↺ Regenera la imagen</button>
```

- [ ] **Step 4: Verificar visualmente**

Con un post de prueba `format='reel'` y `video_url`/`image_url` apuntando a archivos reales (o placeholders), renderizar `calendar_review.html` (mismo método usado en sesiones anteriores: `Client()` autenticado + `override_settings(ALLOWED_HOSTS=['testserver'], SECURE_SSL_REDIRECT=False)` + `secure=True`) y tomar una captura con Playwright para confirmar que el badge "🎬 Reel", el reproductor de video y el botón "Descargar reel (.mp4)" se ven correctamente, y que NO aparece el botón "Regenera la imagen" en esa card.

- [ ] **Step 5: Commit**

```bash
git add core/brand_dna/templates/brand_dna/calendar_review.html
git commit -m "feat(reels): UI del reproductor de video, badge y botones en calendar_review.html"
```

---

## Self-Review

**Cobertura de la spec:**
- ✅ 1 reel/semana, día 1 → Task 8 (`content_generation_task`).
- ✅ Automático sin confirmación → Task 8 (mismo job, sin gate).
- ✅ ~24s / 3 clips → Task 5 (`_generate_video_clips`), Task 6 (`_assemble_reel` calcula `duration = len(clips) * 8`).
- ✅ Omitir reel si hay foto real día 1 → Task 8 (`_product_image_for_day(1, ...)`).
- ✅ Mismo job, `job_timeout` — **gap encontrado:** el plan no incluye un paso explícito para subir `job_timeout` en `core/brand_dna/tasks.py:70` (hoy 1500s) y `core/brand_dna/views.py:530` (`generate_next_week`, también 1500s). Agregado como Step final de Task 8 antes del commit: cambiar ambos `job_timeout=1500` a `job_timeout=2400` (los ~3-5 min adicionales de Veo/Lyria/TTS/ffmpeg caben con margen).
- ✅ Hook 0-3s, limpio 3-21s, CTA 21-24s → Task 6 (`enable='between(t,0,3)'` / `between(t,{cta_start},{duration})`).
- ✅ Sin subtítulos → fuera de alcance, ningún task los construye.
- ✅ Sin regeneración manual → Task 9.
- ✅ Sin QC visual → ningún task lo agrega (correcto, es una omisión intencional).
- ✅ `email_daily.html` sin cambios → confirmado al leer el template actual, no embebe imagen inline.
- ✅ Fallback contract completo (tabla de la spec) → Task 5 (Veo reintento 1x, música/narración `None` sin abortar), Task 7 (`generate()` aborta si `len(clips) < 3` o si `_assemble_reel` lanza).

**Gap corregido durante el self-review:** ver arriba (job_timeout).

**Consistencia de tipos:** `_generate_post_media` retorna `tuple[str, list[str], str]` en Task 8, consumido correctamente en los 3 call sites con desempaquetado de 3 valores. `ReelGenerator.generate()` retorna `tuple[str, str]` (Task 7), consistente con su uso en Task 8. `ReelScriptGenerator.generate()` retorna `dict` con 6 claves fijas (Task 3), consumidas por nombre exacto en Task 7 (`script['hook_text']`, etc.) — mismos nombres en ambos lados.

## Corrección aplicada: `job_timeout`

### Task 8 (adición) — Step 7: Subir `job_timeout`

**Files:**
- Modify: `core/brand_dna/tasks.py:70`
- Modify: `core/brand_dna/views.py:530`

- [ ] En `core/brand_dna/tasks.py`, cambiar:
  ```python
  django_rq.enqueue(content_generation_task, str(job_id), job_timeout=1500)
  ```
  a:
  ```python
  django_rq.enqueue(content_generation_task, str(job_id), job_timeout=2400)
  ```

- [ ] En `core/brand_dna/views.py`, cambiar:
  ```python
  django_rq.enqueue(generate_next_week, str(calendar.id), next_week, job_timeout=1500)
  ```
  a:
  ```python
  django_rq.enqueue(generate_next_week, str(calendar.id), next_week, job_timeout=2400)
  ```

- [ ] Commit:
  ```bash
  git add core/brand_dna/tasks.py core/brand_dna/views.py
  git commit -m "fix(reels): subir job_timeout a 2400s para cubrir la generacion de reels"
  ```
