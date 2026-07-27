# Pipeline de producto real como referencia (solo admin) — Diseño

**Fecha:** 2026-07-27
**Origen:** Feedback repetido de testers pidiendo subir fotos de su producto real para que
la IA las integre en el video/imagen — mismo pedido de fondo que motivó BGSWAP
(HALLAZGO 65, eliminado 2026-07-15 por baja tasa de éxito real y una marca de agua que se
filtró al resultado con Jorge). Anuar validó hoy mismo, con llamadas reales contra la API
de Vertex AI y fotos reales de Gelatinas Marba, que existe un mecanismo técnico distinto al
de BGSWAP que sí funciona — pero con al menos 1 alucinación real confirmada, así que se
construye como pipeline separado, solo-admin, antes de considerar testers/usuarios finales.

## Contexto: validación técnica real (2026-07-27, con fotos reales de Gelatinas Marba)

Se probaron 3 mecanismos con llamadas reales (no simuladas) contra el proyecto de Vertex AI
de Cosmic:

- **Prueba A — imagen con referencia** (`client.models.generate_content`, modelo
  `publishers/google/models/gemini-2.5-flash-image`, pasando la foto real + un prompt
  dentro de `contents`): ✅ genera una escena 100% nueva (fondo, luz, composición
  distintos), preserva forma/color del producto y el texto del logo es legible aunque
  simplificado. Nota: los modelos `gemini-3-pro-image` y `gemini-3.1-flash-image` (más
  nuevos, aparecen en `client.models.list()`) devolvieron `404 NOT_FOUND` — el proyecto no
  tiene acceso todavía, se usó `gemini-2.5-flash-image` que sí funciona.
- **Prueba B — video con `reference_images`/`ASSET`** (`client.models.generate_videos`,
  `GenerateVideosConfig(reference_images=[VideoGenerationReferenceImage(...)])`, pasando la
  foto CRUDA del usuario): ⚠️ funciona técnicamente (el modelo GA `veo-3.1-fast-generate-001`
  ya configurado en producción acepta el parámetro, no hace falta ningún modelo preview) pero
  el resultado es casi un edit del original — mismo fondo, misma composición, solo agrega
  movimiento de cámara. NO es lo que se busca (no genera una escena nueva).
- **Prueba C — video encadenado** (la imagen de la Prueba A pasada a `generate_videos` vía
  el parámetro `image=` de primer frame, NO `reference_images`): ✅ el mejor resultado —
  escena nueva de verdad, formato 9:16 correcto para reel. **Pero se confirmó una
  alucinación real**: en un frame intermedio, Veo inventó un SEGUNDO logo en el costado del
  vaso con texto garabateado ("GelaTiMaS MARBA 🌿4BD OSI") que nunca existió en la foto
  original ni en el frame inicial del mismo video.

**Conclusión técnica**: la cadena ganadora es **A → C** (Gemini genera la escena nueva,
Veo la anima con `image=` como primer frame clásico) — nunca usar `reference_images`/`ASSET`
de Veo directo sobre la foto cruda. Y el auditor de resultados (QC visual, mismo mecanismo
que `_validate_scene_still` construido hoy mismo para el reel de producción) sigue siendo
necesario aquí también — la alucinación de logos no desapareció con este mecanismo nuevo.

## Decisiones de Anuar

- **Pipeline separado, no se toca producción**: módulo nuevo, no se modifica
  `image_generator.py`/`reel_generator.py` de producción.
- **Solo admin por ahora**: se valida con variedad de fotos reales antes de considerar
  testers, luego usuario final — mismo criterio que ya evitó repetir el error de BGSWAP.
- **Reusar la UI existente de admin en producción** — el resultado debe verse dentro del
  calendario (`calendar_review.html`), igual que ya funciona hoy para la generación de
  muestra (`allows_sample_generation`/`generation_mode=sample_image|sample_reel`). Nada de
  un log/carpeta de resultados aparte.
- **2 modos, mismo patrón que ya existe** (`sample_image`/`sample_reel`): uno solo-imagen
  (Prueba A, más barato/rápido para revisar fidelidad) y uno reel completo (A→C encadenado).

## Diseño técnico

### A. Módulo nuevo: `core/content_pipeline/generators/product_reference_generator.py`

Clase `ProductReferenceGenerator` (mismo patrón de constructor que `ImageGenerator`/
`ReelGenerator`: `__init__(self, bucket_name: str)`), con:

```python
_REFERENCE_IMAGE_MODEL = 'publishers/google/models/gemini-2.5-flash-image'
# gemini-3-pro-image / gemini-3.1-flash-image devuelven 404 (sin acceso) en el
# proyecto de Vertex AI de Cosmic al 2026-07-27 — usar 2.5-flash-image, confirmado
# funcional con llamada real.

_VEO_POLL_TIMEOUT_SECONDS = 300
_VEO_POLL_INTERVAL_SECONDS = 10

_SCENE_PROMPT_TEMPLATE = (
    "Using the product shown in this reference image, generate a brand-new professional "
    "product photograph for {business_name}: a completely new scene, new background, new "
    "lighting and composition — NOT an edit of the input image. Incorporate this exact "
    "product as it appears (same shape, color, texture, any visible branding) as the subject "
    "of the new photograph. Photorealistic, studio-quality, natural lighting."
)

_VIDEO_PROMPT_TEMPLATE = (
    "Cinematic slow push-in on this product photography scene for {business_name}. "
    "Gentle ambient motion (light shifting, soft background movement) — keep the product "
    "and composition stable. Photorealistic, 4k."
)


class ProductReferenceGenerator:
    def __init__(self, bucket_name: str):
        self._bucket = bucket_name

    def generate_image(self, product_photo_bytes: bytes, business_name: str, filename: str) -> str:
        """Prueba A en aislamiento: solo la escena nueva, sin animar. Retorna URL
        subida a storage, o '' si falla (mismo contrato que ImageGenerator.generate)."""
        ...

    def generate_reel(self, product_photo_bytes: bytes, business_name: str, filename_prefix: str) -> tuple[str, str]:
        """Cadena A->C completa. Retorna (video_url, poster_url) — mismo contrato
        que ReelGenerator.generate (poster = la imagen de la Prueba A, ya sirve
        como poster frame sin necesidad de extraerlo del video)."""
        ...
```

Reglas de implementación (código ya validado hoy, transcribir tal cual — NO reinventar):
- La llamada de imagen usa `client.models.generate_content(model=_REFERENCE_IMAGE_MODEL,
  contents=[image_part, prompt], config=types.GenerateContentConfig(response_modalities=['IMAGE', 'TEXT']))`.
- La llamada de video usa `client.models.generate_videos(model=settings.VERTEX_VIDEO_MODEL,
  prompt=prompt, image=types.Image(image_bytes=scene_bytes, mime_type='image/png'),
  config=types.GenerateVideosConfig(aspect_ratio='9:16', duration_seconds=8, number_of_videos=1, generate_audio=False))`
  — **NUNCA** usar `reference_images`/`VideoGenerationReferenceImage` (Prueba B, descartada).
- Reusa `_detect_mime`/`_vertex_client` de `image_generator.py` (import directo, son
  funciones de módulo, no de clase — no requiere instanciar `ImageGenerator`).
- Mismo patrón de polling que `ReelGenerator._generate_single_clip` (`operation.done`,
  timeout, `operation.error`).

### B. Auditor de resultados (obligatorio desde el día 1, no opcional)

Método `_validate_scene(image_bytes: bytes) -> bool` — copia adaptada de
`ReelGenerator._validate_scene_still` (construida hoy mismo, mismo checklist de 5 flags
incluyendo la detección de logo/marca reforzada). Se corre sobre el resultado de
`generate_image` SIEMPRE, y sobre un frame extraído del video de `generate_reel` (usar
`ffmpeg` para extraer 2-3 frames del video generado — inicio, medio, fin — y validar cada
uno; si CUALQUIERA falla el QC, se marca el resultado completo como rechazado). Si el QC
rechaza, un solo reintento completo de la cadena (no hay fallback genérico como en el
pipeline de producción — este es un pipeline de prueba, se reporta el rechazo directamente
en vez de silenciarlo).

### C. Modelo: 2 modos nuevos + campo de foto de referencia

`core/brand_dna/models.py`, `AnalysisJob`:

```python
    MODE_FULL = 'full'
    MODE_SAMPLE_IMAGE = 'sample_image'
    MODE_SAMPLE_REEL = 'sample_reel'
    MODE_SAMPLE_PRODUCT_IMAGE = 'sample_product_image'
    MODE_SAMPLE_PRODUCT_REEL = 'sample_product_reel'
    MODE_CHOICES = [
        (MODE_FULL, 'Calendario completo'),
        (MODE_SAMPLE_IMAGE, 'Muestra: imagen'),
        (MODE_SAMPLE_REEL, 'Muestra: reel'),
        (MODE_SAMPLE_PRODUCT_IMAGE, 'Muestra: imagen con producto real (solo admin)'),
        (MODE_SAMPLE_PRODUCT_REEL, 'Muestra: reel con producto real (solo admin)'),
    ]
    ...
    product_reference_image_path = models.CharField(max_length=500, blank=True, default='')
```

(`product_reference_image_path` es un campo nuevo, mismo patrón exacto que
`logo_file_path` ya existente en la misma clase — requiere migración nueva de
`brand_dna`.)

### D. Formulario: campo de subida condicional (mismo gate que ya existe)

`core/brand_dna/templates/brand_dna/new_analysis.html`, dentro del bloque
`{% if allows_sample_generation %}` (línea 106-121) — agregar las 2 opciones de radio
nuevas y, condicionado a que una de esas 2 esté seleccionada, un campo de archivo:

```html
      {% if allows_sample_generation %}
      <div class="form-group">
        <label>¿Qué quieres generar?</label>
        <div style="display:flex;flex-direction:column;gap:8px;margin-top:6px;">
          <label style="display:flex;align-items:center;gap:8px;font-weight:normal;">
            <input type="radio" name="generation_mode" value="full" checked> Calendario completo (7 días)
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-weight:normal;">
            <input type="radio" name="generation_mode" value="sample_image"> Solo 1 imagen de muestra
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-weight:normal;">
            <input type="radio" name="generation_mode" value="sample_reel"> Solo 1 reel de muestra
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-weight:normal;">
            <input type="radio" name="generation_mode" value="sample_product_image" id="modeProductImage"> [ADMIN] Imagen con producto real
          </label>
          <label style="display:flex;align-items:center;gap:8px;font-weight:normal;">
            <input type="radio" name="generation_mode" value="sample_product_reel" id="modeProductReel"> [ADMIN] Reel con producto real
          </label>
        </div>
      </div>
      <div class="form-group" id="productPhotoGroup" style="display:none;">
        <label>Foto real del producto</label>
        <input type="file" name="product_reference_photo" accept="image/*" id="productPhotoInput">
      </div>
      {% endif %}
```

JS nuevo (mismo `<script>` al final del archivo): mostrar/ocultar `#productPhotoGroup`
según el radio seleccionado, y agregar el archivo comprimido al `FormData` bajo la key
`product_reference_photo` — mismo patrón exacto que ya usa `logoInput`/`compressAll`
(líneas 177-194), solo con un input y una key distintos.

### E. Vista: manejar el nuevo upload

`core/brand_dna/views.py`, `analyze_submit`:

```python
    requested_mode = request.POST.get('generation_mode', AnalysisJob.MODE_FULL)
    valid_modes = {
        AnalysisJob.MODE_FULL, AnalysisJob.MODE_SAMPLE_IMAGE, AnalysisJob.MODE_SAMPLE_REEL,
        AnalysisJob.MODE_SAMPLE_PRODUCT_IMAGE, AnalysisJob.MODE_SAMPLE_PRODUCT_REEL,
    }
    if requested_mode not in valid_modes or not get_user_plan(request.user).allows_sample_generation:
        requested_mode = AnalysisJob.MODE_FULL
```

Después del bloque que maneja `request.FILES['logo']` (líneas 163-172), bloque paralelo
para `product_reference_photo` (mismo patrón: `_validate_image_bytes`, `_safe_extension`,
`save_upload`):

```python
    if 'product_reference_photo' in request.FILES:
        photo_file = request.FILES['product_reference_photo']
        photo_bytes = photo_file.read()
        if not _validate_image_bytes(photo_bytes):
            return render(request, 'brand_dna/new_analysis.html', {'error': 'La foto del producto no es una imagen válida.'})
        ext = _safe_extension(photo_file.name)
        photo_path = f'uploads/product_ref_{job.id}.{ext}'
        save_upload(photo_bytes, photo_path)
        job.product_reference_image_path = photo_path
        job.save(update_fields=['product_reference_image_path'])
```

### F. Tarea: enganchar el modo nuevo en `generate_sample_task`

`core/content_pipeline/tasks.py`, `generate_sample_task` (línea 97) — el bloque que hoy
decide `wanted_format` y llama `_generate_post_media` se bifurca: si
`job.generation_mode` es uno de los 2 modos nuevos, usar `ProductReferenceGenerator` en
vez de `ImageGenerator`/`ReelGenerator`, leyendo la foto desde
`job.product_reference_image_path` (storage) — si el campo está vacío (usuario no subió
foto), marcar el job como fallido con un mensaje claro en vez de intentar generar sin
producto de referencia.

### G. Vista de resultados: sin cambios

`calendar_review.html` ya renderiza cualquier `ContentPost` con `image_url`/`video_url`
poblados, sin importar qué generador los produjo — cero cambios necesarios aquí. Así se
cumple el pedido explícito de Anuar de reusar la UI existente.

## Fuera de alcance

- Acceso para Tester/User — explícitamente diferido hasta validar con más fotos reales
  (fase futura, requiere definir criterio de éxito antes de abrir).
- Cualquier cambio a `image_generator.py`/`reel_generator.py` de producción.
- El log/carpeta de resultados del script exploratorio de hoy
  (`test_product_reference_pipeline`, management command) — superado por este diseño, se
  puede eliminar una vez que este flujo esté funcionando.
- Investigar por qué `gemini-3-pro-image`/`gemini-3.1-flash-image` devuelven 404 (podría
  ser solo cuestión de acceso/allowlist) — usar `gemini-2.5-flash-image` por ahora, revisar
  más adelante si conviene actualizar.
- Mitigar la inconsistencia espacial entre tomas múltiples (no aplica aquí — este pipeline
  genera 1 sola escena base, no 5 tomas independientes como el reel de producción).

## Testing

- `test_product_reference_generator.py` (nuevo): mocks de `generate_content`/
  `generate_videos` para `generate_image`/`generate_reel`, casos de éxito, de fallo de API,
  y de QC rechazando el resultado (con y sin reintento). Mismo patrón de mocking que
  `test_reel_generator.py`/`test_image_generator.py` ya usan (`patch('...′_vertex_client')`).
- `test_views.py` (`analyze_submit`): nuevo test para el upload de
  `product_reference_photo`, y para que los 2 modos nuevos se rechacen (caen a `MODE_FULL`)
  si `allows_sample_generation` es `False`.
- `test_models.py`: default vacío de `product_reference_image_path`.
- `test_tasks.py` (`generate_sample_task`): nuevo caso para cada uno de los 2 modos nuevos,
  y el caso de job sin foto subida (falla con mensaje claro).
