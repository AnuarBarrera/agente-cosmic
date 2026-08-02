# Correcciones de calidad de imagen/reel — ronda 2 (IMG-09, IMG-05, IMG-08, IMG-10) — Implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Implementar los 4 hallazgos de `hallazgosImagen.txt` cubiertos en el spec `2026-08-02-image-reel-quality-fixes-round2-design.md`: criterio de QC de contenido sensible + reencuadre cinematográfico (IMG-09), mensajes de rechazo específicos priorizando marcas de agua (IMG-05), filtro de nicho sensible por proximidad + reescritura granular (IMG-08), y español latinoamericano + backstop de placeholder (IMG-10).

**Architecture:** 3 tareas secuenciales. Task 1 toca 4 archivos (1 hallazgo, cambios pequeños en cada uno). Task 2 cambia el contrato de retorno de `ProductReferenceGenerator` (agrega razón de rechazo) — va después de Task 1 por tocar el mismo archivo. Task 3 combina 2 hallazgos que comparten archivo y tienen dependencia real de orden de ejecución dentro del mismo método.

**Tech Stack:** Django, `google-genai`, `pydantic` (ya en uso desde el plan de sandbox/schema).

## Global Constraints

- **NO hacer `git commit` en ningún paso de este plan.** El trabajo se deja sin commitear en el working tree — decisión explícita de Anuar de esperar a terminar toda la sesión.
- El texto de los prompts se copia EXACTO de este plan — no parafrasear, no "mejorar" la redacción.
- Todo `try/except`/fail-open existente se mantiene igual salvo donde el spec pide explícitamente lo contrario (Task 2 cambia el contrato de retorno, no el comportamiento de fail-open en sí).
- Suite completa (`docker compose exec backend pytest core/ -q`) debe quedar en verde al final del plan.
- Backend Docker ya corriendo — todos los comandos van vía `docker compose exec backend ...`.

---

### Task 1: IMG-09 — Criterio de contenido sensible + reencuadre cinematográfico

**Files:**
- Modify: `core/content_pipeline/generators/reel_script_generator.py`
- Modify: `core/content_pipeline/generators/image_generator.py`
- Modify: `core/content_pipeline/generators/reel_generator.py`
- Modify: `core/content_pipeline/generators/product_reference_generator.py`
- Test: `core/content_pipeline/tests/test_reel_script_generator.py`
- Test: `core/content_pipeline/tests/test_image_generator.py`
- Test: `core/content_pipeline/tests/test_reel_generator.py`
- Test: `core/content_pipeline/tests/test_product_reference_generator.py`

**Interfaces:**
- Consumes: `ImageQCSchema` (image_generator.py), `SceneQCSchema` (reel_generator.py), `ProductQCSchema` (product_reference_generator.py) — los 3 ya existen con 5 campos booleanos + `ok`, definidos en el plan de sandbox/schema recién completado.
- Produces: los 3 schemas ganan un 6to campo `has_suggestive_or_exposed_content: bool`. Ningún otro archivo de este plan depende de esto directamente (Task 2 no lo usa por nombre, solo lee `data` como dict genérico).

- [x] **Step 1: `reel_script_generator.py` — reencuadre cinematográfico en `scene_prompts` (instrucción 5)**

Reemplazar dentro de `_PROMPT`, el fragmento que empieza en
`"   - scene_prompts[1] a scene_prompts[5]: para un GENERADOR DE IMAGEN FIJA, 5 shots "`
y termina justo antes de `"6. music_mood:"` — el bloque completo a reemplazar es:

```python
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

por:

```python
    "   - scene_prompts[1] a scene_prompts[5]: para un GENERADOR DE IMAGEN FIJA, 5 shots "
    "cortos e independientes (~2 segundos cada uno en el reel final) — como una rafaga "
    "de tomas distintas en un comercial. Prioriza la SENSACION FINAL del cliente y "
    "efectos cinematograficos de camara (luz calida, profundidad de campo, movimiento "
    "suave) por encima de una narracion descriptiva literal de la interaccion o el "
    "servicio: detalles del producto, el resultado final, la expresion de satisfaccion "
    "del cliente DESPUES de la experiencia, texturas, ambiente. Evita describir al "
    "cliente en pleno momento de un tratamiento o servicio de contacto fisico directo "
    "(masajes, tratamientos corporales) — enfoca esas escenas en el ambiente o el "
    "resultado, no en el momento del contacto. Los 5 deben mostrar variedad visual real "
    "entre si, no la misma composicion repetida, y TODOS deben compartir un mismo estilo "
    "fotografico consistente (todas fotorrealistas, o todas el mismo estilo de render/"
    "ilustracion — nunca mezclar fotorrealismo con render 3D o ilustracion entre tomas "
    "del mismo reel). Evita escenas de proceso de fabricacion o manufactura (maquinaria, "
    "herramientas de produccion) salvo que la descripcion del negocio lo mencione "
    "explicitamente — sin datos reales del proceso, el modelo inventa imaginaria "
    "industrial generica no creible.\n"
```

- [x] **Step 2: Verificar que los 2 tests de substring exacto siguen pasando**

```bash
docker compose exec backend pytest core/content_pipeline/tests/test_reel_script_generator.py -k "differentiates_veo_scene or manufacturing_process" -v
```

Expected: PASS (el texto de variedad visual/estilo/fabricación no cambió, solo la primera mitad del párrafo).

- [x] **Step 3: `image_generator.py` — reencuadre cinematográfico en modo lifestyle de `_analyze_brand_scene`**

Dentro del método `_analyze_brand_scene`, en la construcción de `gemini_prompt`, reemplazar la línea:

```python
                f"- If risk=NO  → mode=\"lifestyle\": DO NOT feature this business's exact product/craft as the main "
                f"subject either — focus on how a customer FEELS after using/consuming it (satisfaction, comfort, a "
                f"genuine expression, the environment/mood of the experience), not a literal shot of the product "
                f"itself. NO offices or screens.\n\n"
```

por:

```python
                f"- If risk=NO  → mode=\"lifestyle\": DO NOT feature this business's exact product/craft as the main "
                f"subject either — focus on how a customer FEELS after using/consuming it (satisfaction, comfort, a "
                f"genuine expression, the environment/mood of the experience), captured with cinematic lighting and "
                f"depth of field, not a literal/descriptive shot of the product or service interaction itself. Avoid "
                f"depicting a client mid-treatment during hands-on physical services (massage, spa, body treatments) — "
                f"focus on the environment or the after-effect instead. NO offices or screens.\n\n"
```

- [x] **Step 4: `image_generator.py` — agregar criterio QC nuevo en `_validate_background`**

Dentro del método `_validate_background`, en la construcción de `prompt`, insertar una línea nueva DESPUÉS de la línea de `has_unrealistic_grounding` y ANTES de la línea de `ok`:

```python
                "has_unrealistic_grounding: true if the main subject (person, animal, or product) appears to float, "
                "hover, or is otherwise disconnected from the surface/floor/background it should be resting or "
                "standing on — no visible contact point, no matching contact shadow directly beneath it, wrong "
                "scale or perspective versus the background, or a dynamic mid-air pose (jumping, running) composited "
                "onto a background that implies the subject is stationary. This commonly happens when a subject's "
                "pose doesn't match its new background. Only flag clear, obvious cases where it looks physically wrong.\n"
                "has_suggestive_or_exposed_content: true if the image shows exposed intimate body parts, implied "
                "nudity, partial nudity, or content that could be perceived as sexually suggestive, even if not "
                "explicit. Be conservative and strict — prefer a false rejection over a false pass.\n"
                "ok: true ONLY if has_text=false AND is_abstract_3d=false AND has_screen_content=false "
                "AND has_malformed_object=false AND has_unrealistic_grounding=false AND "
                "has_suggestive_or_exposed_content=false."
```

(Reemplaza las 2 últimas líneas del bloque de `has_unrealistic_grounding`/`ok` por las 4 líneas de arriba — la línea de `has_unrealistic_grounding` NO cambia de texto, solo se le agregan 2 líneas nuevas después.)

Y en la clase `ImageQCSchema` (definida cerca del inicio del archivo, junto a los demás schemas), agregar el campo:

```python
class ImageQCSchema(BaseModel):
    has_text: bool
    is_abstract_3d: bool
    has_screen_content: bool
    has_malformed_object: bool
    has_unrealistic_grounding: bool
    has_suggestive_or_exposed_content: bool
    ok: bool
```

- [x] **Step 5: Agregar test de QC en `test_image_generator.py`**

Buscar la clase `TestValidateBackground` existente y agregar dentro:

```python
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_false_when_suggestive_content_detected(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = (
                '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, '
                '"has_malformed_object": false, "has_unrealistic_grounding": false, '
                '"has_suggestive_or_exposed_content": true, "ok": false}'
            )
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._validate_background(b'fake-png')
        assert result is False
```

(Usar el mismo import de `MagicMock`/`patch`/`override_settings` que ya está al inicio del archivo — no agregar imports duplicados.)

- [x] **Step 6: Correr `test_image_generator.py` completo**

```bash
docker compose exec backend pytest core/content_pipeline/tests/test_image_generator.py -v
```

Expected: PASS (67 tests — 66 existentes + 1 nuevo).

- [x] **Step 7: `reel_generator.py` — mismo criterio QC en `_validate_scene_still`**

Dentro del método `_validate_scene_still`, aplicar el mismo cambio que el Step 4 (mismo texto exacto de las 2 líneas nuevas, mismo reemplazo de la línea de `ok`):

```python
                "has_unrealistic_grounding: true if the main subject (person, animal, or product) appears to float, "
                "hover, or is otherwise disconnected from the surface/floor/background it should be resting or "
                "standing on — no visible contact point, no matching contact shadow directly beneath it, wrong "
                "scale or perspective versus the background, or a dynamic mid-air pose (jumping, running) composited "
                "onto a background that implies the subject is stationary. This commonly happens when a subject's "
                "pose doesn't match its new background. Only flag clear, obvious cases where it looks physically wrong.\n"
                "has_suggestive_or_exposed_content: true if the image shows exposed intimate body parts, implied "
                "nudity, partial nudity, or content that could be perceived as sexually suggestive, even if not "
                "explicit. Be conservative and strict — prefer a false rejection over a false pass.\n"
                "ok: true ONLY if has_text=false AND is_abstract_3d=false AND has_screen_content=false "
                "AND has_malformed_object=false AND has_unrealistic_grounding=false AND "
                "has_suggestive_or_exposed_content=false."
```

Y en `SceneQCSchema`:

```python
class SceneQCSchema(BaseModel):
    has_text: bool
    is_abstract_3d: bool
    has_screen_content: bool
    has_malformed_object: bool
    has_unrealistic_grounding: bool
    has_suggestive_or_exposed_content: bool
    ok: bool
```

- [x] **Step 8: Agregar test de QC en `test_reel_generator.py`**

Buscar la clase `TestValidateSceneStill` existente y agregar dentro (mismo patrón que Step 5):

```python
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_false_when_suggestive_content_detected(self):
        from core.content_pipeline.generators.reel_generator import ReelGenerator
        gen = ReelGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.reel_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = (
                '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, '
                '"has_malformed_object": false, "has_unrealistic_grounding": false, '
                '"has_suggestive_or_exposed_content": true, "ok": false}'
            )
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._validate_scene_still(b'fake-png')
        assert result is False
```

- [x] **Step 9: Correr `test_reel_generator.py` completo**

```bash
docker compose exec backend pytest core/content_pipeline/tests/test_reel_generator.py -v
```

Expected: PASS (95 tests — 94 existentes + 1 nuevo).

- [x] **Step 10: `product_reference_generator.py` — mismo criterio QC en `_QC_PROMPT`**

Reemplazar dentro de `_QC_PROMPT`:

```python
    "has_unrealistic_grounding: true if the main subject (person, animal, or product) appears to float, "
    "hover, or is otherwise disconnected from the surface/floor/background it should be resting or "
    "standing on — no visible contact point, no matching contact shadow directly beneath it, wrong "
    "scale or perspective versus the background, or a dynamic mid-air pose (jumping, running) composited "
    "onto a background that implies the subject is stationary. This commonly happens when a subject's "
    "pose doesn't match its new background. Only flag clear, obvious cases where it looks physically wrong.\n"
    "ok: true ONLY if has_text=false AND is_abstract_3d=false AND has_screen_content=false "
    "AND has_malformed_object=false AND has_unrealistic_grounding=false."
```

por:

```python
    "has_unrealistic_grounding: true if the main subject (person, animal, or product) appears to float, "
    "hover, or is otherwise disconnected from the surface/floor/background it should be resting or "
    "standing on — no visible contact point, no matching contact shadow directly beneath it, wrong "
    "scale or perspective versus the background, or a dynamic mid-air pose (jumping, running) composited "
    "onto a background that implies the subject is stationary. This commonly happens when a subject's "
    "pose doesn't match its new background. Only flag clear, obvious cases where it looks physically wrong.\n"
    "has_suggestive_or_exposed_content: true if the image shows exposed intimate body parts, implied "
    "nudity, partial nudity, or content that could be perceived as sexually suggestive, even if not "
    "explicit. Be conservative and strict — prefer a false rejection over a false pass.\n"
    "ok: true ONLY if has_text=false AND is_abstract_3d=false AND has_screen_content=false "
    "AND has_malformed_object=false AND has_unrealistic_grounding=false AND "
    "has_suggestive_or_exposed_content=false."
```

Y en `ProductQCSchema`:

```python
class ProductQCSchema(BaseModel):
    has_text: bool
    is_abstract_3d: bool
    has_screen_content: bool
    has_malformed_object: bool
    has_unrealistic_grounding: bool
    has_suggestive_or_exposed_content: bool
    ok: bool
```

- [x] **Step 11: Agregar test de QC en `test_product_reference_generator.py`**

Buscar la clase `TestValidateScene` existente y agregar dentro:

```python
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_false_when_suggestive_content_detected(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = (
                '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, '
                '"has_malformed_object": false, "has_unrealistic_grounding": false, '
                '"has_suggestive_or_exposed_content": true, "ok": false}'
            )
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            result = gen._validate_scene(b'fake-png')
        assert result is False
```

**NOTA IMPORTANTE**: `_validate_scene` en este archivo TODAVÍA devuelve solo `bool` en esta tarea (Task 1 no toca su firma) — ese cambio a `tuple[bool, dict]` es exclusivo de la Task 2, que corre DESPUÉS. Este test debe seguir asumiendo que `_validate_scene` devuelve un `bool` plano.

- [x] **Step 12: Correr `test_product_reference_generator.py` completo**

```bash
docker compose exec backend pytest core/content_pipeline/tests/test_product_reference_generator.py -v
```

Expected: PASS (13 tests — 12 existentes + 1 nuevo).

- [x] **Step 13: Correr los 3 archivos de test de esta tarea juntos, más `test_reel_script_generator.py`**

```bash
docker compose exec backend pytest core/content_pipeline/tests/test_reel_script_generator.py core/content_pipeline/tests/test_image_generator.py core/content_pipeline/tests/test_reel_generator.py core/content_pipeline/tests/test_product_reference_generator.py -v
```

Expected: todos PASS.

---

### Task 2: IMG-05 — Mensajes de rechazo específicos (prioriza marcas de agua)

**Files:**
- Modify: `core/content_pipeline/generators/product_reference_generator.py`
- Modify: `core/content_pipeline/tasks.py`
- Test: `core/content_pipeline/tests/test_product_reference_generator.py`
- Test: `core/content_pipeline/tests/test_tasks.py`

**Interfaces:**
- Consumes: nada de Task 1 directamente (el campo nuevo del schema es transparente — esta tarea lee `data` como dict genérico vía `.get()`).
- Produces: **cambio de contrato** — `ProductReferenceGenerator._validate_scene(self, image_bytes: bytes) -> tuple[bool, dict]` (antes `-> bool`). `generate_image(...) -> tuple[str, str]` (antes `-> str`, ahora `(url, reason)`). `generate_reel(...) -> tuple[str, str, str]` (antes `-> tuple[str, str]`, ahora `(video_url, poster_url, reason)`). `reason` es `''` en éxito. Task 3 no usa estas funciones, no hay impacto cruzado.

- [x] **Step 1: Agregar `_describe_qc_failure` a `product_reference_generator.py`**

Agregar esta función cerca de `_QC_PROMPT`/`ProductQCSchema` (después de la clase `ProductQCSchema`, antes de `_QC_FRAME_OFFSETS`):

```python
def _describe_qc_failure(data: dict) -> str:
    has_text = data.get('has_text')
    has_screen = data.get('has_screen_content')
    if has_text and has_screen:
        return (
            'La foto de referencia parece ser una captura de pantalla (con interfaz de una app '
            'o red social) en vez de una foto directa del producto. Sube una foto tomada '
            'directamente del producto, no una captura de pantalla.'
        )
    if has_text:
        return (
            'El resultado generado tiene texto o logos visibles. La causa mas comun es que la '
            'foto original tenga una marca de agua (muy comun para proteger fotos de robo) y el '
            'modelo la haya heredado sin querer. Intenta con una foto sin marca de agua, o con la '
            'marca de agua recortada.'
        )
    if data.get('has_suggestive_or_exposed_content'):
        return 'El resultado fue rechazado por posible contenido sensible. Intenta con otra foto o vuelve a generar.'
    if has_screen:
        return 'El resultado generado muestra una pantalla con contenido visible. Vuelve a generar.'
    if data.get('has_malformed_object'):
        return 'El producto generado salio deformado o con partes incorrectas. Vuelve a intentar o usa otra foto.'
    if data.get('has_unrealistic_grounding'):
        return 'El producto aparece flotando o sin apoyo natural en la escena generada. Vuelve a intentar.'
    if data.get('is_abstract_3d'):
        return 'El resultado salio como una forma abstracta o render 3D en vez de una foto realista. Vuelve a intentar.'
    return 'El control de calidad rechazo el resultado. Vuelve a intentar.'
```

- [x] **Step 2: Cambiar `_validate_scene` a `tuple[bool, dict]`**

Reemplazar el método completo:

```python
    def _validate_scene(self, image_bytes: bytes) -> tuple[bool, dict]:
        try:
            client = _vertex_client()
            image_part = types.Part.from_bytes(data=image_bytes, mime_type='image/png')
            with track_external_api('gemini', operation='product_reference_qc'):
                resp = client.models.generate_content(
                    model=settings.VERTEX_TEXT_MODEL,
                    contents=[image_part, _QC_PROMPT],
                    config=types.GenerateContentConfig(
                        labels=vertex_labels(),
                        response_mime_type="application/json",
                        response_schema=ProductQCSchema,
                    ),
                )
            record_tokens(resp, operation='product_reference_qc',
                          prompt_preview=_QC_PROMPT[:500], response_preview=resp.text[:500] if resp.text else '')
            data = json.loads(resp.text)
            ok = bool(data.get('ok', True))
            if not ok:
                logger.warning(f"ProductReferenceGenerator: QC rechazo con detalle: {data}")
            return ok, data
        except Exception as e:
            logger.warning(f"ProductReferenceGenerator._validate_scene error (assuming ok): {e}")
        return True, {}
```

- [x] **Step 3: Cambiar `generate_image` y `generate_reel` para devolver la razón**

Reemplazar ambos métodos completos:

```python
    def generate_image(self, product_photo_bytes: bytes, business_name: str, filename: str) -> tuple[str, str]:
        try:
            scene_bytes = self._generate_scene(product_photo_bytes, business_name)
            if scene_bytes is None:
                return '', 'No se pudo generar la escena a partir de la foto (el modelo se nego a procesarla).'
            ok, qc_data = self._validate_scene(scene_bytes)
            if not ok:
                logger.warning("ProductReferenceGenerator: QC rechazo la escena generada (generate_image)")
                return '', _describe_qc_failure(qc_data)
            url = self._upload_to_storage(scene_bytes, filename, 'image/png', 'product-samples')
            return url, ''
        except Exception as e:
            logger.warning(f"ProductReferenceGenerator.generate_image fallo: {e}")
            return '', 'Ocurrio un error inesperado generando la imagen. Vuelve a intentar.'

    def generate_reel(self, product_photo_bytes: bytes, business_name: str, filename_prefix: str) -> tuple[str, str, str]:
        try:
            scene_bytes = self._generate_scene(product_photo_bytes, business_name)
            if scene_bytes is None:
                return '', '', 'No se pudo generar la escena a partir de la foto (el modelo se nego a procesarla).'
            ok, qc_data = self._validate_scene(scene_bytes)
            if not ok:
                logger.warning("ProductReferenceGenerator: QC rechazo la escena generada (generate_reel)")
                return '', '', _describe_qc_failure(qc_data)

            video_bytes = self._animate_scene(scene_bytes)
            if video_bytes is None:
                return '', '', 'No se pudo generar el video a partir de la escena. Vuelve a intentar.'

            for offset in _QC_FRAME_OFFSETS:
                frame_bytes = self._extract_frame(video_bytes, offset_seconds=offset)
                if frame_bytes is None:
                    logger.warning(f"ProductReferenceGenerator: no se pudo extraer el frame en {offset}s para QC — se rechaza el resultado")
                    return '', '', 'No se pudo verificar uno de los frames del video generado. Vuelve a intentar.'
                ok, qc_data = self._validate_scene(frame_bytes)
                if not ok:
                    logger.warning(f"ProductReferenceGenerator: QC rechazo el frame en {offset}s del video")
                    return '', '', _describe_qc_failure(qc_data)

            poster_url = self._upload_to_storage(scene_bytes, f'{filename_prefix}-poster', 'image/png', 'product-samples')
            video_url = self._upload_to_storage(video_bytes, filename_prefix, 'video/mp4', 'product-samples')
            return video_url, poster_url, ''
        except Exception as e:
            logger.warning(f"ProductReferenceGenerator.generate_reel fallo: {e}")
            return '', '', 'Ocurrio un error inesperado generando el reel. Vuelve a intentar.'
```

- [x] **Step 4: Reescribir `tasks.py::_generate_product_reference_sample` al nuevo contrato**

Reemplazar el bloque completo (desde `if job.generation_mode == AnalysisJob.MODE_SAMPLE_PRODUCT_REEL:` hasta el `return` del `mark_failed`):

```python
    if job.generation_mode == AnalysisJob.MODE_SAMPLE_PRODUCT_REEL:
        video_url, poster_url, reason = product_gen.generate_reel(
            photo_bytes, brand_dna.business_name, filename_prefix=f"{job.id}-product-sample",
        )
        image_url, fmt = poster_url, ContentPost.FORMAT_REEL
    else:
        image_url, reason = product_gen.generate_image(
            photo_bytes, brand_dna.business_name, filename=f"{job.id}-product-sample",
        )
        video_url, fmt = '', ContentPost.FORMAT_SINGLE

    if not image_url and not video_url:
        calendar.delete()
        job.mark_failed(reason or 'El control de calidad rechazó el resultado. Reintenta.')
        return
```

- [x] **Step 5: Reescribir `test_product_reference_generator.py` completo al nuevo contrato**

Reemplazar el archivo completo con este contenido (mismos 13 tests de Task 1 + los ajustes de contrato):

```python
import pytest
from unittest.mock import patch, MagicMock
from django.test import override_settings

pytestmark = pytest.mark.django_db


class TestGenerateImage:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_url_when_scene_and_qc_succeed(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')

        scene_resp = MagicMock()
        scene_part = MagicMock()
        scene_part.inline_data.data = b'fake-scene-png'
        scene_part.text = None
        scene_resp.candidates = [MagicMock(content=MagicMock(parts=[scene_part]))]

        qc_resp = MagicMock()
        qc_resp.text = '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": true}'

        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc, \
             patch.object(ProductReferenceGenerator, '_upload_to_storage', return_value='https://storage.test/scene.png'):
            mock_vc.return_value.models.generate_content.side_effect = [scene_resp, qc_resp]
            url, reason = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')

        assert url == 'https://storage.test/scene.png'
        assert reason == ''

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_empty_string_when_scene_generation_fails(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            url, reason = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert url == ''
        assert reason != ''

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_watermark_message_when_qc_rejects_for_text(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')

        scene_resp = MagicMock()
        scene_part = MagicMock()
        scene_part.inline_data.data = b'fake-scene-png'
        scene_part.text = None
        scene_resp.candidates = [MagicMock(content=MagicMock(parts=[scene_part]))]

        qc_resp = MagicMock()
        qc_resp.text = '{"has_text": true, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": false}'

        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = [scene_resp, qc_resp]
            url, reason = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')

        assert url == ''
        assert 'marca de agua' in reason.lower()

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_screenshot_message_when_text_and_screen_content(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')

        scene_resp = MagicMock()
        scene_part = MagicMock()
        scene_part.inline_data.data = b'fake-scene-png'
        scene_part.text = None
        scene_resp.candidates = [MagicMock(content=MagicMock(parts=[scene_part]))]

        qc_resp = MagicMock()
        qc_resp.text = '{"has_text": true, "is_abstract_3d": false, "has_screen_content": true, "has_malformed_object": false, "has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": false}'

        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = [scene_resp, qc_resp]
            url, reason = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')

        assert url == ''
        assert 'captura de pantalla' in reason.lower()

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_generate_image_returns_empty_string_when_upload_fails(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', return_value=(True, {'ok': True})), \
             patch.object(gen, '_upload_to_storage', side_effect=Exception('GCS down')):
            url, reason = gen.generate_image(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert url == ''
        assert reason != ''


class TestGenerateReel:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        VERTEX_VIDEO_MODEL='veo-3.1-fast-generate-001',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_video_and_poster_url_when_everything_succeeds(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')

        with patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', return_value=(True, {'ok': True})), \
             patch.object(gen, '_animate_scene', return_value=b'video-bytes'), \
             patch.object(gen, '_extract_frame', return_value=b'frame-bytes'), \
             patch.object(gen, '_upload_to_storage', side_effect=['https://storage.test/poster.png', 'https://storage.test/video.mp4']):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')

        assert video_url == 'https://storage.test/video.mp4'
        assert poster_url == 'https://storage.test/poster.png'
        assert reason == ''

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        VERTEX_VIDEO_MODEL='veo-3.1-fast-generate-001',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_empty_strings_when_scene_fails(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_scene', return_value=None):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == '' and poster_url == ''
        assert reason != ''

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        VERTEX_VIDEO_MODEL='veo-3.1-fast-generate-001',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_empty_strings_when_video_generation_fails(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', return_value=(True, {'ok': True})), \
             patch.object(gen, '_animate_scene', return_value=None):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == '' and poster_url == ''
        assert reason != ''

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        VERTEX_VIDEO_MODEL='veo-3.1-fast-generate-001',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_empty_strings_when_a_video_frame_fails_qc(self):
        """Reproduce el hallazgo real de hoy: un frame intermedio del video con un
        logo alucinado que no estaba en el frame inicial — debe rechazar el
        resultado completo, no solo advertir."""
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', side_effect=[
                 (True, {'ok': True}), (True, {'ok': True}), (False, {'has_text': True, 'ok': False}),
             ]), \
             patch.object(gen, '_animate_scene', return_value=b'video-bytes'), \
             patch.object(gen, '_extract_frame', return_value=b'frame-bytes'):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == '' and poster_url == ''
        assert 'marca de agua' in reason.lower()

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
        VERTEX_VIDEO_MODEL='veo-3.1-fast-generate-001',
        GOOGLE_CLOUD_STORAGE_BUCKET='test-bucket',
    )
    def test_returns_empty_strings_when_frame_extraction_fails(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch.object(gen, '_generate_scene', return_value=b'scene-bytes'), \
             patch.object(gen, '_validate_scene', return_value=(True, {'ok': True})), \
             patch.object(gen, '_animate_scene', return_value=b'video-bytes'), \
             patch.object(gen, '_extract_frame', return_value=None):
            video_url, poster_url, reason = gen.generate_reel(b'fake-photo-bytes', 'Gelatinas Marba', 'job123-sample')
        assert video_url == '' and poster_url == ''
        assert reason != ''


class TestValidateScene:
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_true_when_image_ok(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": true}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            ok, data = gen._validate_scene(b'fake-png')
        assert ok is True
        assert data.get('ok') is True

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_false_when_has_text(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '{"has_text": true, "is_abstract_3d": false, "has_screen_content": false, "has_malformed_object": false, "has_unrealistic_grounding": false, "has_suggestive_or_exposed_content": false, "ok": false}'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            ok, data = gen._validate_scene(b'fake-png')
        assert ok is False
        assert data.get('has_text') is True

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_true_on_api_error(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            ok, data = gen._validate_scene(b'fake-png')
        assert ok is True  # fail-open, mismo criterio que _validate_background
        assert data == {}

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_returns_false_when_suggestive_content_detected(self):
        from core.content_pipeline.generators.product_reference_generator import ProductReferenceGenerator
        gen = ProductReferenceGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.product_reference_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = (
                '{"has_text": false, "is_abstract_3d": false, "has_screen_content": false, '
                '"has_malformed_object": false, "has_unrealistic_grounding": false, '
                '"has_suggestive_or_exposed_content": true, "ok": false}'
            )
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            ok, data = gen._validate_scene(b'fake-png')
        assert ok is False


class TestDescribeQcFailure:
    def test_screenshot_pattern_wins_over_text_alone(self):
        from core.content_pipeline.generators.product_reference_generator import _describe_qc_failure
        msg = _describe_qc_failure({'has_text': True, 'has_screen_content': True})
        assert 'captura de pantalla' in msg.lower()

    def test_text_alone_mentions_watermark(self):
        from core.content_pipeline.generators.product_reference_generator import _describe_qc_failure
        msg = _describe_qc_failure({'has_text': True, 'has_screen_content': False})
        assert 'marca de agua' in msg.lower()

    def test_suggestive_content_message(self):
        from core.content_pipeline.generators.product_reference_generator import _describe_qc_failure
        msg = _describe_qc_failure({'has_suggestive_or_exposed_content': True})
        assert 'sensible' in msg.lower()

    def test_empty_data_returns_generic_message(self):
        from core.content_pipeline.generators.product_reference_generator import _describe_qc_failure
        msg = _describe_qc_failure({})
        assert 'calidad' in msg.lower()
```

- [x] **Step 6: Correr `test_product_reference_generator.py` completo**

```bash
docker compose exec backend pytest core/content_pipeline/tests/test_product_reference_generator.py -v
```

Expected: PASS (17 tests: 13 de `TestGenerateImage`/`TestGenerateReel`/`TestValidateScene` + 4 de `TestDescribeQcFailure`; nota: 1 test de Task 1 más los de este Step reemplazan el archivo completo — el conteo final debe ser 17 tests únicos, sin duplicados. Si Task 1 ya agregó `test_returns_false_when_suggestive_content_detected` a `TestValidateScene` con la firma vieja (`bool` plano), este Step 5 la reemplaza con la firma nueva — no debe haber 2 versiones del mismo test).

- [x] **Step 7: Actualizar los 4 mocks de `ProductReferenceGenerator` en `test_tasks.py`**

En `core/content_pipeline/tests/test_tasks.py`, ubicar y reemplazar exactamente estas 4 líneas:

Línea ~369, dentro de `test_generate_sample_task_product_image_mode_creates_post`:
```python
        MockGen.return_value.generate_image.return_value = ('https://storage.test/product-scene.png', '')
```

Línea ~398, dentro de `test_generate_sample_task_product_reel_mode_creates_post`:
```python
        MockGen.return_value.generate_reel.return_value = ('https://storage.test/video.mp4', 'https://storage.test/poster.png', '')
```

Línea ~420, dentro de `test_generate_sample_task_product_image_mode_fails_when_qc_rejects`:
```python
        MockGen.return_value.generate_image.return_value = ('', 'El control de calidad rechazó el resultado. Reintenta.')
```

Buscar la función equivalente para reel (`test_generate_sample_task_product_reel_mode_fails_when_qc_rejects`, línea ~441) y aplicar el mismo cambio:
```python
        MockGen.return_value.generate_reel.return_value = ('', '', 'El control de calidad rechazó el resultado. Reintenta.')
```

No cambiar ninguna otra parte de estos 4 tests — las aserciones existentes (`assert 'calidad' in ...error_message.lower()`, conteo de posts, status) siguen siendo válidas tal cual con estos valores de mock.

- [x] **Step 8: Correr `test_tasks.py` completo**

```bash
docker compose exec backend pytest core/content_pipeline/tests/test_tasks.py -v
```

Expected: PASS (58 tests, mismo conteo que antes — solo cambian los mocks, no la cantidad de tests).

---

### Task 3: IMG-08 + IMG-10 — Filtro por proximidad + reescritura granular, español latino + backstop de placeholder

**Files:**
- Modify: `core/content_pipeline/generators/reel_script_generator.py`
- Test: `core/content_pipeline/tests/test_reel_script_generator.py`

**Interfaces:**
- Consumes: nada de Task 1/Task 2 (archivo distinto).
- Produces: `_has_banned_promise_language(text: str) -> bool`, `_fix_marca_placeholder(narration_script: str, business_name: str) -> str` — funciones nuevas a nivel de módulo, sin uso fuera de este archivo.

**ORDEN CRÍTICO**: dentro de `generate()`, el backstop de placeholder (D.2 del spec) debe aplicarse ANTES que la revisión de nicho sensible (C) — ambos tocan `narration_script`, y el backstop es determinístico/barato mientras que la revisión de nicho sensible puede disparar una llamada extra a Gemini vía `rewrite_for_brand_consistency`. Seguir el orden exacto de los Steps de abajo.

- [x] **Step 1: Agregar `_MARCA_PLACEHOLDER_RE` y `_fix_marca_placeholder`**

Agregar cerca de `_BRAND_LEAK_KEYWORDS`/`_scrub_brand_leak` (después de `_scrub_brand_leak`, antes de `class ReelScriptGenerator:`):

```python
_MARCA_PLACEHOLDER_RE = re.compile(r'^\s*Marca\.\s*|\[Marca\]', re.IGNORECASE)


def _fix_marca_placeholder(narration_script: str, business_name: str) -> str:
    """HALLAZGO IMG-10: si Gemini falla la instruccion del prompt y deja el
    placeholder generico [Marca] o "Marca." al inicio del guion en vez del
    nombre real, se reemplaza deterministicamente — mismo patron que
    _scrub_brand_leak para logos (HALLAZGO 77)."""
    if _MARCA_PLACEHOLDER_RE.search(narration_script):
        logger.warning("ReelScriptGenerator: placeholder [Marca]/generico detectado en narration_script, reemplazado con nombre real")
        return _MARCA_PLACEHOLDER_RE.sub(f'{business_name}. ', narration_script, count=1)
    return narration_script
```

- [x] **Step 2: Test unitario de `_fix_marca_placeholder`**

Agregar a `test_reel_script_generator.py`, cerca de los tests existentes de `_scrub_brand_leak`:

```python
def test_fix_marca_placeholder_replaces_leading_marca():
    from core.content_pipeline.generators.reel_script_generator import _fix_marca_placeholder
    result = _fix_marca_placeholder('Marca. Creamos batas de carnicero disenadas para el rigor.', 'Batas de Carnicero')
    assert result.startswith('Batas de Carnicero.')
    assert 'Marca.' not in result


def test_fix_marca_placeholder_replaces_bracketed_placeholder():
    from core.content_pipeline.generators.reel_script_generator import _fix_marca_placeholder
    result = _fix_marca_placeholder('[Marca] ofrece la mejor calidad del mercado.', 'Tacos El Primo')
    assert '[Marca]' not in result
    assert 'Tacos El Primo' in result


def test_fix_marca_placeholder_leaves_legitimate_marca_mention_untouched():
    from core.content_pipeline.generators.reel_script_generator import _fix_marca_placeholder
    original = 'Nuestra marca de agua distintiva se ve en cada producto que entregamos.'
    result = _fix_marca_placeholder(original, 'Tacos El Primo')
    assert result == original
```

- [x] **Step 3: Correr los 3 tests nuevos**

```bash
docker compose exec backend pytest core/content_pipeline/tests/test_reel_script_generator.py -k "fix_marca_placeholder" -v
```

Expected: 3 PASS.

- [x] **Step 4: Agregar `_PROMISE_CONTEXT_WORDS` y `_has_banned_promise_language`**

Agregar junto a las funciones del Step 1:

```python
_PROMISE_CONTEXT_WORDS = ('garantiz', 'asegur', 'resultado', 'efectiv', 'seguro')


def _has_banned_promise_language(text: str) -> bool:
    direct_banned = ('garantizado', 'garantizamos', 'asegurar', 'aseguramos')
    if any(w in text for w in direct_banned):
        return True
    if '100%' in text:
        idx = text.find('100%')
        window = text[max(0, idx - 40):idx + 40]
        if any(ctx in window for ctx in _PROMISE_CONTEXT_WORDS):
            return True
    return False
```

- [x] **Step 5: Test unitario de `_has_banned_promise_language`**

Agregar a `test_reel_script_generator.py`:

```python
def test_has_banned_promise_language_detects_direct_words():
    from core.content_pipeline.generators.reel_script_generator import _has_banned_promise_language
    assert _has_banned_promise_language('te garantizamos el mejor servicio') is True
    assert _has_banned_promise_language('aseguramos tu satisfaccion') is True


def test_has_banned_promise_language_ignores_neutral_100_percent():
    from core.content_pipeline.generators.reel_script_generator import _has_banned_promise_language
    assert _has_banned_promise_language('somos una empresa 100% mexicana dedicada a tu bienestar') is False


def test_has_banned_promise_language_flags_100_percent_near_promise_word():
    from core.content_pipeline.generators.reel_script_generator import _has_banned_promise_language
    assert _has_banned_promise_language('resultados 100% garantizados para todos') is True
    assert _has_banned_promise_language('un tratamiento 100% efectivo desde la primera sesion') is True
```

- [x] **Step 6: Correr los 3 tests nuevos**

```bash
docker compose exec backend pytest core/content_pipeline/tests/test_reel_script_generator.py -k "has_banned_promise_language" -v
```

Expected: 3 PASS.

- [x] **Step 7: Rewritir instrucción 4 de `_PROMPT` (tuteo, español latino)**

Reemplazar dentro de `_PROMPT`:

```python
    "4. narration_script: guion de voz en off en espanol, ~15-20 segundos hablados "
    "(unas 40-50 palabras), tono conversacional, sin leer literalmente el hook ni el CTA. "
    "Si mencionas el nombre del negocio, usa el nombre real exacto tal cual (ver "
    "DATOS DEL NEGOCIO abajo) — nunca escribas la palabra generica \"marca\" ni un "
    "placeholder entre corchetes como [Marca].\n"
```

por:

```python
    "4. narration_script: guion de voz en off en espanol, ~15-20 segundos hablados "
    "(unas 40-50 palabras), tono conversacional, sin leer literalmente el hook ni el CTA. "
    "Usa espanol latinoamericano neutro, con tuteo (tu/tu, nunca 'usted' ni conjugaciones "
    "de usted), evitando vocabulario corporativo o giros tipicos del espanol de España "
    "(ej. evita 'indumentaria', 'inocuidad', imperativos formales como 'Garantice'/"
    "'Proteja'/'Solicite'). "
    "Si mencionas el nombre del negocio, usa el nombre real exacto tal cual (ver "
    "DATOS DEL NEGOCIO abajo) — nunca escribas la palabra generica \"marca\" ni un "
    "placeholder entre corchetes como [Marca].\n"
```

- [x] **Step 8: Verificar que los 2 tests de substring exacto de Task 1 siguen pasando**

```bash
docker compose exec backend pytest core/content_pipeline/tests/test_reel_script_generator.py -k "differentiates_veo_scene or manufacturing_process" -v
```

Expected: PASS (el punto 4 no interfiere con el texto de scene_prompts que esos tests verifican).

- [x] **Step 9: Aplicar el backstop de placeholder en `generate()`, ANTES de la revisión de nicho sensible**

Ubicar, dentro de `generate()`, el bloque donde se construye `result` (el diccionario con `hook_text`, `highlight_word`, etc. — justo después de la línea `'music_mood': str(data.get('music_mood', '')).strip() or fallback['music_mood'],` y el cierre `}` del dict) y ANTES del bloque `if _is_sensitive_niche(brand_dna):`.

Insertar esta línea nueva justo después de que `result` queda construido:

```python
            result['narration_script'] = _fix_marca_placeholder(result['narration_script'], brand_dna.business_name)
```

- [x] **Step 10: Reemplazar el bloque de revisión de nicho sensible por la versión granular**

Reemplazar el bloque completo:

```python
            if _is_sensitive_niche(brand_dna):
                text_to_check = _strip_accents(f"{result['hook_text']} {result['narration_script']}".lower())
                banned = ('garantizado', 'garantizamos', 'asegurar', 'aseguramos', '100%')
                if any(word in text_to_check for word in banned):
                    logger.warning("ReelScriptGenerator: guion rechazado por lenguaje prohibido en nicho sensible, usando fallback")
                    return fallback
```

por:

```python
            if _is_sensitive_niche(brand_dna):
                for field_name in ('hook_text', 'narration_script'):
                    text_to_check = _strip_accents(result[field_name].lower())
                    if _has_banned_promise_language(text_to_check):
                        logger.warning(f"ReelScriptGenerator: lenguaje prohibido detectado en {field_name} (nicho sensible), reescribiendo solo ese campo")
                        result[field_name] = rewrite_for_brand_consistency(
                            field_name, result[field_name],
                            'Usa lenguaje de promesa absoluta o resultado garantizado, prohibido en '
                            'nichos sensibles — reescribe sin palabras como "garantizado", "asegura", '
                            'o "100%" en contexto de promesa de resultado.',
                            brand_dna,
                        )
```

- [x] **Step 11: Reescribir el test de nicho sensible al nuevo comportamiento granular**

En `test_reel_script_generator.py`, reemplazar la función completa `test_generate_rejects_banned_language_in_sensitive_niche` (y su llamada a `_mock_vertex_client`, que sigue siendo válida tal cual):

```python
def test_generate_rejects_banned_language_in_sensitive_niche(sensitive_brand_dna, _mock_brand_consistency_qc):
    from core.content_pipeline.generators.reel_script_generator import ReelScriptGenerator
    _, mock_rewrite = _mock_brand_consistency_qc
    mock_rewrite.return_value = 'Cuidamos tu salud con atencion profesional y cercana.'
    post_data = {'caption': 'Atencion pediatrica de calidad'}
    response_json = (
        '{"hook_text":"Garantizamos tu salud","highlight_word":"Garantizamos","tag_cta":"Agenda hoy",'
        '"narration_script":"Aseguramos resultados en cada consulta.","scene_prompts":'
        '["s1, no text, no logos, no people speaking to camera.",'
        '"s2, no text, no logos, no people speaking to camera.",'
        '"s3, no text, no logos, no people speaking to camera.",'
        '"s4, no text, no logos, no people speaking to camera.",'
        '"s5, no text, no logos, no people speaking to camera.",'
        '"s6, no text, no logos, no people speaking to camera."],"music_mood":"calm"}'
    )
    with patch('core.content_pipeline.generators.reel_script_generator._vertex_client') as mock_vc:
        mock_vc.return_value = _mock_vertex_client(response_json)
        result = ReelScriptGenerator().generate(post_data, sensitive_brand_dna)

    assert result['hook_text'] == 'Cuidamos tu salud con atencion profesional y cercana.'
    # el resto del guion NO cae a fallback completo — sigue siendo el generado por Gemini
    assert result['tag_cta'] == 'Agenda hoy'
    assert len(result['scene_prompts']) == 6
    assert result['scene_prompts'][0].startswith('s1')
```

**IMPORTANTE**: la fixture `_mock_brand_consistency_qc` (autouse, definida al inicio del archivo) ya existe y hace `yield mock_audit, mock_rewrite` — para capturar ese valor en el test hay que declararla explícitamente como parámetro del test (como en la firma de arriba), no basta con que sea `autouse=True`.

- [x] **Step 12: Correr el test reescrito**

```bash
docker compose exec backend pytest core/content_pipeline/tests/test_reel_script_generator.py -k "test_generate_rejects_banned_language_in_sensitive_niche" -v
```

Expected: PASS.

- [x] **Step 13: Correr `test_reel_script_generator.py` completo**

```bash
docker compose exec backend pytest core/content_pipeline/tests/test_reel_script_generator.py -v
```

Expected: PASS (13 tests originales + 6 nuevos de `_fix_marca_placeholder`/`_has_banned_promise_language` = 19 tests).

---

### Task 4: Verificación final de la suite completa

**Files:** ninguno nuevo — solo verificación.

- [x] **Step 1: Correr la suite completa**

```bash
docker compose exec backend pytest core/ -q
```

Expected: todos los tests PASAN. Conteo esperado aproximado: 629 (base post plan de sandbox/schema) + 1 (Task 1, image) + 1 (Task 1, reel) + 1 (Task 1, product-ref) + 4 (Task 2, `TestDescribeQcFailure`) + 2 (Task 2, nuevos mensajes específicos en `TestGenerateImage`) + 1 (Task 2, `TestValidateScene` suggestive) + 6 (Task 3) = **645 aprox.** (el número exacto puede variar levemente según cuántos tests netos de Task 1/Task 2 se solapen — lo importante es 0 failed).

- [x] **Step 2: Confirmar que ningún paso hizo `git commit`**

```bash
git status --short
```

Expected: los archivos tocados por este plan aparecen como `M` (modified), sin ningún commit nuevo en `git log` desde antes de empezar.
