# Reels Pipeline Design

## Goal

Añadir un reel de video (día 1, pilar "Producto") al calendario semanal de 7 posts, generado con Veo (video mudo 9:16), Lyria 3 (música), TTS (narración) y overlay de texto animado (hook + CTA) renderizado con el mismo enfoque HTML+Playwright que ya usa Cosmic para las imágenes de posts — no SVG.

## Context

Cosmic genera hoy 7 posts por semana con pilares de contenido fijos (`core/content_pipeline/generators/text_generator.py::CONTENT_PILLARS`), uno de los cuales (día 3, "Prueba social") ya usa formato carrusel en vez de imagen única (ver `ImageGenerator.generate_carousel()`). Esta spec extiende el mismo patrón de "formato distinto por pilar" al día 1 ("Producto"), agregando un tercer formato: reel.

Investigación de feasibility ya hecha (misma sesión, ver memoria `project_cosmic_reels.md`):
- Veo 3 / Veo 3 Fast: acceso GA confirmado en el proyecto `agente-cosmic`, soporta `aspect_ratio="9:16"` y `generate_audio` (que NO se usará — el video debe generarse mudo).
- Lyria 3 (`lyria-3-clip-preview`): acceso `PUBLIC_PREVIEW` confirmado, invocable vía `client.interactions.create(...)` con las MISMAS credenciales de Vertex AI que ya usa Cosmic (sin API key nueva). Clips de hasta 30s, ~$0.04/clip.
- TTS (`gemini-2.5-flash-tts`): llamada normal vía `SpeechConfig` en `generate_content`, mismo patrón que el resto del proyecto.
- `ffmpeg` ya está instalado en `Dockerfile` y `Dockerfile.worker` — sin dependencia nueva de infraestructura.
- Prototipo de overlay de texto (HTML+Playwright → PNG transparente → `ffmpeg overlay` sobre video) ya construido y aprobado visualmente por Anuar — 2 de 3 estilos probados quedaron listos (pill resaltador, contorno tipo impacto), uno (subrayado) tiene un bug de z-index pendiente de arreglar en la implementación.

## Decisiones de producto (Anuar, explícitas en esta sesión)

- **1 reel por semana**, integrado como el formato del día 1 ("Producto") — mismo patrón que el carrusel en día 3, no una feature manual aparte.
- **Se genera automático**, sin confirmación de costo previa — mismo flujo que el resto del calendario, pese a costar ~6-9x más que el resto de la semana junta.
- **Duración objetivo: ~24s / 3 clips de Veo.**
- **Si el día 1 tiene una foto de producto real asignada** (`_product_image_for_day(1, product_images_bytes)` devuelve algo distinto de `None`), **se omite el reel** — ese día se genera como imagen normal con la foto real, igual que hoy. Riesgo de distorsionar una foto real del usuario vía Veo, no vale la pena.
- **Se genera en el mismo job** que el resto del calendario (no un job RQ separado) — se extiende el `job_timeout` existente.
- **Texto en pantalla:** hook grande los primeros ~3s, video limpio (sin texto) los ~18s del medio, tag/CTA corto los últimos ~3s. Subtítulos de la narración NO están en el alcance de esta versión (confirmado explícitamente por Anuar).
- **Sin regeneración manual para reels en esta primera versión** — es el formato más costoso, y no hay valor real en poder pedir "cambia solo la frase final" a este costo. Primero se valida con datos reales, después se decide si vale la pena construir regeneración (probablemente async con polling, dado que tardaría minutos — ver sección "Fuera de alcance").
- **Sin QC visual del video final** — el pipeline de imágenes hace 2-3 rondas de QC con Gemini Vision porque una imagen se regenera barato; un reintento de reel cuesta ~$0.53 solo en Veo. El único QC real es sobre el contenido del guion (reutiliza la validación de nicho sensible/promesas absolutas que ya corre sobre captions, H36). Riesgo aceptado explícitamente por Anuar — "vamos con cuidado" — a revisar tras validar con datos reales.

## Arquitectura

```
TextGenerator.generate()  [ya existe, sin cambios]
  └─ post_data[0]: pillar='Producto', day=1

_product_image_for_day(1, product_images_bytes) devuelve foto real?
  │
  ├─ SÍ  → post_data[0]['format'] = 'single'  (igual que hoy, con la foto real)
  │
  └─ NO  → post_data[0]['format'] = 'reel'
             │
             ReelScriptGenerator.generate(post_data[0], brand_dna)  [NUEVO]
               └─ { hook_text, highlight_word, tag_cta, narration_script,
                    scene_prompts: [p1, p2, p3], music_mood }
                             │
             ReelGenerator.generate(...)  [NUEVO]
               ├─ 1. Veo: 3x generate_videos() (9:16, mudo, ~8s c/u)
               ├─ 2. Lyria 3 Clip: 1x interactions.create() (música, ≤30s)
               ├─ 3. TTS: 1x generate_content() con SpeechConfig (narración)
               ├─ 4. Overlay: 2x render HTML+Playwright → PNG transparente
               │      (hook para 0-3s, CTA para 21-24s)
               └─ 5. ffmpeg: concat clips → overlay por ventana de tiempo →
                      mezcla música+voz → recorte a 24s → MP4 1080x1920
                             │
             ContentPost.video_url = MP4 en GCS
             ContentPost.image_url = frame del segundo 1 (poster)
```

**Contrato de fallo (ningún paso rompe el calendario completo):**

| Paso | Si falla | Efecto |
|---|---|---|
| `ReelScriptGenerator` | Fallback a valores genéricos (headline extraído, 3 escenas fallback, mood genérico) | Sigue el pipeline |
| Cualquiera de los 3 clips de Veo | 1 reintento con prompt fallback; si sigue fallando | **Aborta el reel** → día 1 se genera como imagen normal (`ImageGenerator.generate()`) |
| Lyria (música) | Se omite la pista de música | Reel sin música, sigue el pipeline |
| TTS (narración) | Se omite la narración | Reel sin voz, sigue el pipeline |
| Ensamblaje ffmpeg | — | **Aborta el reel** → fallback a imagen normal |

## Componentes

### 1. Modelo — `ContentPost` (`core/content_pipeline/models.py`)

```python
FORMAT_REEL = 'reel'
FORMAT_CHOICES = [
    (FORMAT_SINGLE, 'Imagen única'),
    (FORMAT_CAROUSEL, 'Carrusel'),
    (FORMAT_REEL, 'Reel'),
]
video_url = models.URLField(max_length=1000, blank=True, default='')
```

`image_url` se sigue llenando (con el poster frame extraído del segundo 1 del reel vía ffmpeg) para retrocompatibilidad total — ningún template existente se rompe si ignora `video_url`. Migración nueva (`AddField` + `AlterField` de `format`).

### 2. `ReelScriptGenerator` (nuevo) — `core/content_pipeline/generators/reel_script_generator.py`

Mismo patrón que `TextGenerator`: una llamada a Gemini (`_vertex_client()`, `track_external_api`, `record_tokens`) con un prompt que recibe el `post_data` del día 1 (caption/pillar ya generados) + brand context, y devuelve JSON:

```python
{
    "hook_text": str,          # 3-8 palabras, para el overlay 0-3s
    "highlight_word": str,     # palabra dentro del hook a resaltar
    "tag_cta": str,            # 2-4 palabras, para el overlay 21-24s
    "narration_script": str,   # guion para TTS, ~15-20s hablado
    "scene_prompts": [str, str, str],  # 3 prompts para Veo, en inglés (igual que el resto de prompts de Imagen en el proyecto)
    "music_mood": str,         # prompt corto para Lyria (ej. "upbeat corporate, optimistic")
}
```

Aplica la misma regla de seguridad de nicho sensible que ya usa `TextGenerator._ensure_safe_caption` sobre `narration_script` y `hook_text` (reutiliza `_is_sensitive_niche`, `_validate_caption_safety`-equivalente). Fallback si Gemini falla: `hook_text` = headline extraído del caption ya generado (reutiliza `ImageGenerator._extract_headline`), `scene_prompts` = 3 prompts genéricos fijos (mismo patrón que `_SCENE_FALLBACKS`/`_PRODUCT_FALLBACKS` en `image_generator.py`), `music_mood` genérico según `brand_dna.tone`.

### 3. `ReelGenerator` (nuevo) — `core/content_pipeline/generators/reel_generator.py`

Método público:

```python
def generate(self, script: dict, colors: list[str], tone: str, filename_prefix: str,
             brand_name: str = '', max_qc_retries: int = 1) -> tuple[str, str]:
    """Retorna (video_url, poster_image_url). Vacíos ('', '') si falla."""
```

Pasos internos (privados, cada uno testeable en aislado vía mocks):
- `_generate_video_clips(scene_prompts) -> list[bytes]` — 3x Veo, `aspect_ratio='9:16'`, sin `generate_audio`.
- `_generate_music(music_mood) -> bytes | None` — Lyria 3 Clip vía `client.interactions.create(model='lyria-3-clip-preview', ...)`.
- `_generate_narration(narration_script) -> bytes | None` — TTS vía `SpeechConfig`.
- `_render_text_overlay(text, highlight_word, colors, font_seed) -> bytes` — reutiliza `_choose_font_preset`/patrón de `_render_html_template`, nuevos templates HTML (`reel_hook.html`, `reel_cta.html`) basados en los estilos ya aprobados del prototipo (pill resaltador, contorno impacto), `page.screenshot(omit_background=True)`.
- `_assemble_reel(clips, music, narration, hook_png, cta_png) -> bytes` — `subprocess.run(['ffmpeg', ...])` directo (sin librería wrapper nueva): concat de clips, overlay de `hook_png` en `[0,3]`, overlay de `cta_png` en `[21,24]`, mezcla de audio (música + narración), output MP4 H.264 1080x1920.
- `_extract_poster_frame(video_bytes) -> bytes` — `ffmpeg -ss 1 -vframes 1` sobre el video ensamblado.
- `_upload_video_to_storage(video_bytes, filename) -> str` — mismo patrón que `_upload_to_storage` pero `content_type='video/mp4'`, extensión `.mp4`.

### 4. Wiring — `core/content_pipeline/tasks.py`

**Cambio de firma (afecta código ya existente de esta misma sesión):** `_generate_post_media()` retorna hoy una tupla de 2 (`image_url, image_urls`). Pasa a retornar una tupla de 3: `(image_url, image_urls, video_url)` — `video_url` es `''` para los formatos `single`/`carousel`. Los 3 call sites que ya la usan (`content_generation_task`, `generate_next_week`, `_generate_missing_image`, más la llamada desde `post_action_api` en `views.py`) se actualizan para desempacar 3 valores y pasar `video_url` a `ContentPost.objects.create(...)`/`post.video_url = ...`.

```python
def _generate_post_media(image_gen, reel_gen, fmt, filename, max_qc_retries=2, **kwargs) -> tuple[str, list[str], str]:
    if fmt == ContentPost.FORMAT_REEL:
        script = reel_script_gen.generate(...)
        video_url, poster_url = reel_gen.generate(script=script, filename_prefix=filename, ...)
        if not video_url:
            # fallback: día 1 como imagen normal
            url = image_gen.generate(filename=filename, max_qc_retries=max_qc_retries, **kwargs)
            return url, [], ''
        return poster_url, [], video_url
    if fmt == ContentPost.FORMAT_CAROUSEL:
        urls = image_gen.generate_carousel(filename_prefix=filename, max_qc_retries=max_qc_retries, **kwargs)
        return (urls[0] if urls else ''), urls, ''
    url = image_gen.generate(filename=filename, max_qc_retries=max_qc_retries, **kwargs)
    return url, [], ''
```

`content_generation_task` decide `format='reel'` para el día 1 solo si `_product_image_for_day(1, product_images_bytes) is None` (reutiliza la función ya existente, sin cambios en su lógica). Llama a `ReelScriptGenerator` antes de `_generate_post_media` cuando el día es reel, y le pasa el `script` resultante.

### 5. UI — `calendar_review.html`

Card del día 1 cuando `post.format == 'reel'`:
- `<video controls poster="{{ post.image_url }}"><source src="{{ post.video_url }}" type="video/mp4"></video>` en vez de imagen/grid.
- Badge "🎬 Reel" (mismo patrón visual que el badge "🎠 Carrusel").
- Botón "Descargar reel (.mp4)".
- **Sin botón "Regenera el reel"** — oculto para posts con `format == 'reel'` (decisión de producto, ver arriba).

### 6. Descarga — `core/brand_dna/views.py::download_post_image`

Tercera rama junto a la de imagen simple y la de carrusel (.zip):

```python
if post.format == ContentPost.FORMAT_REEL and post.video_url:
    with urllib.request.urlopen(post.video_url, timeout=30) as resp:
        data = resp.read()
    response = HttpResponse(data, content_type='video/mp4')
    response['Content-Disposition'] = f'attachment; filename="post-dia-{post.day_number}-reel.mp4"'
    return response
```

### 7. Correo diario — `email_daily.html`

Cuando `post.format == 'reel'`: se muestra `post.image_url` (poster) con un ícono ▶ superpuesto (CSS, sin JS), y el enlace de "ver imagen completa" apunta directo a `post.video_url` (se abre y reproduce en el navegador, mismo patrón que hoy con imágenes en GCS).

### 8. Bloqueo defensivo — `core/brand_dna/views.py::post_action_api`

La acción `'regenerate'` retorna `400` si `post.format == ContentPost.FORMAT_REEL` (mensaje: "La regeneración no está disponible para reels todavía") — red de seguridad por si el botón se oculta en UI pero alguien manda la petición directo a la API.

## Settings nuevos (`saas_chatbot/settings.py`)

```python
VERTEX_VIDEO_MODEL = 'veo-3.0-fast-generate-001'
VERTEX_MUSIC_MODEL = 'lyria-3-clip-preview'
VERTEX_TTS_MODEL = 'publishers/google/models/gemini-2.5-flash-tts'
```

## Testing

- `test_reel_script_generator.py` — espejo de `test_text_generator.py`: fallback válido con todas las claves requeridas, parseo de JSON real, QC de seguridad aplicado a `hook_text`/`narration_script` en nichos sensibles, prompt incluye contexto de marca.
- `test_reel_generator.py` — espejo de `test_image_generator.py`: cada paso (`_generate_video_clips`, `_generate_music`, `_generate_narration`, `_render_text_overlay`, `_assemble_reel`, `_extract_poster_frame`) mockeado individualmente vía `patch.object`; se prueban las 5 rutas de fallback de la tabla de arriba. `subprocess.run` de ffmpeg SIEMPRE mockeado — nunca se invoca el binario real en tests.
- `test_tasks.py` — extiende `_generate_post_media`: día 1 usa `ReelGenerator` sin foto de producto; usa `ImageGenerator` normal si hay foto (reutiliza `_product_image_for_day`); cae a `ImageGenerator` si `ReelGenerator` devuelve `('', ...)`.
- `test_views.py` — `download_post_image` sirve MP4 con content-type correcto para reels; `post_action_api` rechaza `action='regenerate'` con 400 si `format == 'reel'`.
- Ningún test hace una llamada real a Veo/Lyria/TTS ni invoca ffmpeg de verdad — mismo estándar que el resto del proyecto.

## Fuera de alcance (explícito)

- Regeneración manual de reels — decisión de producto de esta sesión, se revisita después de validar con datos reales. Si se construye después, necesitará ser asíncrona con polling (a diferencia del flujo síncrono actual de imagen/carrusel) porque un reel tarda minutos, no ~34s.
- QC visual del video final con Gemini Vision — riesgo aceptado explícitamente por costo de reintento.
- Subtítulos/captions de la narración quemados en el video.
- Más de 1 reel por semana / reels en días distintos al 1.
- Confirmación de costo antes de generar — se genera automático.
- Usar la foto de producto real como input de Veo (`reference_images`) — se descartó: si hay foto real, se omite el reel por completo en vez de intentar animarla.
