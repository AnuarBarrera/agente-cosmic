# Correcciones de calidad de imagen/reel — ronda 2 (IMG-09, IMG-05, IMG-08, IMG-10)

## Contexto

`hallazgosImagen.txt` acumuló 10 hallazgos + 1 propuesta de triage desde
las pruebas reales del pipeline de producto-referencia (2026-07-27 en
adelante). De esos, este spec cubre 4: **IMG-09** (QC sin criterio de
contenido sensible), **IMG-05** (mensajes de rechazo poco específicos —
en particular, marcas de agua), **IMG-08** (falso positivo del filtro de
nicho sensible), **IMG-10** (narración en registro peninsular + bug de
placeholder). Quedan fuera de este spec, por decisión explícita de
Anuar:

- **IMG-06** (productos donde el texto es parte del producto) — se
  resuelve como consecuencia de implementar bien la **propuesta de
  triage** (spec separado, siguiente en la secuencia): si la foto
  subida ya es una foto profesional, el triage la debe enrutar a
  "solo mejorar" (usarla tal cual con procesamiento clásico) en vez de
  forzarla por el pipeline de regeneración con IA que siempre la va a
  rechazar.
- **IMG-07** (narración TTS cortada) — sin evidencia nueva todavía,
  el logging ya está activo esperando el próximo caso real.
- El "efecto elefante rosa" de `product_reference_generator.py` — sin
  evidencia de código, solo hipótesis.
- Migración de modelo Gemini 2.5→3.x — diferida por separado.

## A. IMG-09 — Criterio de contenido sensible + reencuadre cinematográfico

**Decisión de Anuar**: en vez de que el prompt de generación de escenas
liste explícitamente qué NO mostrar (instrucción negativa/descriptiva,
que puede llamar la atención del modelo sobre lo que se quiere evitar —
mismo "efecto elefante rosa" ya documentado como hipótesis en otro
hallazgo), el prompt debe reencuadrarse hacia **efectos cinematográficos
de cámara + la sensación final del cliente**, en vez de narrar
descriptivamente la interacción/el servicio en sí. Esto reduce el riesgo
en el origen: si nunca se le pide al modelo describir el momento de
contacto físico, es menos probable que dibuje algo problemático. Se
mantiene 1 línea breve y puntual de resguardo para el caso real que
originó el hallazgo (tratamientos con contacto físico), sin que domine
el tono del prompt. El criterio de QC nuevo queda como red de seguridad
detrás de este reencuadre, no como la única defensa.

Aplica a 2 archivos (generación de escenas de reel Y de imagen simple) +
QC duplicado en 3 archivos.

### A.1 — `reel_script_generator.py`, instrucción 5 de `_PROMPT` (scene_prompts)

Reemplazar únicamente la primera mitad del párrafo (desde
"para un GENERADOR DE IMAGEN FIJA" hasta antes de "Los 5 deben mostrar
variedad visual"), preservando el resto del párrafo (variedad visual,
consistencia de estilo, evitar proceso de fabricación) tal cual está
hoy:

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

### A.2 — `image_generator.py`, `_analyze_brand_scene`, rama `mode="lifestyle"` de `gemini_prompt`

Reemplazar la línea del modo lifestyle (dentro del bloque STEP 2):

```python
    "- If risk=NO  → mode=\"lifestyle\": DO NOT feature this business's exact product/craft as the main "
    "subject either — focus on how a customer FEELS after using/consuming it (satisfaction, comfort, a "
    "genuine expression, the environment/mood of the experience), captured with cinematic lighting and "
    "depth of field, not a literal/descriptive shot of the product or service interaction itself. Avoid "
    "depicting a client mid-treatment during hands-on physical services (massage, spa, body treatments) — "
    "focus on the environment or the after-effect instead. NO offices or screens.\n\n"
```

### A.3 — Criterio QC nuevo, duplicado en 3 archivos

Agregar antes de la línea `"ok: true ONLY if..."` en los 3 prompts QC
(texto idéntico en los 3):

```python
    "has_suggestive_or_exposed_content: true if the image shows exposed intimate body parts, implied "
    "nudity, partial nudity, or content that could be perceived as sexually suggestive, even if not "
    "explicit. Be conservative and strict — prefer a false rejection over a false pass.\n"
```

Y reemplazar la línea de `ok` en los 3 (agregar la 6ta condición):

```python
    "ok: true ONLY if has_text=false AND is_abstract_3d=false AND has_screen_content=false "
    "AND has_malformed_object=false AND has_unrealistic_grounding=false AND "
    "has_suggestive_or_exposed_content=false."
```

Y agregar el campo al schema Pydantic correspondiente en los 3 archivos
(`ImageQCSchema` en `image_generator.py`, `SceneQCSchema` en
`reel_generator.py`, `ProductQCSchema` en `product_reference_generator.py`):

```python
    has_suggestive_or_exposed_content: bool
```

(Los 3 archivos ya tienen estos schemas desde el plan de sandbox/schema
recién completado — este spec solo agrega 1 campo a cada uno, mismo
patrón de duplicación deliberada.)

## B. IMG-05 — Mensajes específicos de por qué se rechazó (prioriza marcas de agua)

**Decisión de Anuar**: no solo cubrir el caso de capturas de pantalla —
priorizar el caso de **marcas de agua**, muy común entre emprendedores
que protegen sus fotos de robo. El admin debe ver siempre una razón
concreta, no un mensaje genérico.

**Cambio de contrato**: `ProductReferenceGenerator._validate_scene`
pasa de devolver solo `bool` a devolver `tuple[bool, dict]` (el detalle
completo del QC). `generate_image`/`generate_reel` pasan de devolver
solo URLs a devolver también un string de razón (vacío en éxito). El
caller (`tasks.py::_generate_product_reference_sample`) usa esa razón
en `job.mark_failed(...)` en vez del mensaje genérico fijo de hoy.

### B.1 — Función de traducción de QC a mensaje (nueva, en `product_reference_generator.py`)

Agregar cerca de `_QC_PROMPT`/los schemas:

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

### B.2 — `_validate_scene` devuelve `(ok, data)`

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

### B.3 — `generate_image`/`generate_reel` devuelven razón

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

### B.4 — `tasks.py::_generate_product_reference_sample` usa la razón

Estado actual (post plan de sandbox/schema + fix RQ):

```python
    if job.generation_mode == AnalysisJob.MODE_SAMPLE_PRODUCT_REEL:
        video_url, poster_url = product_gen.generate_reel(
            photo_bytes, brand_dna.business_name, filename_prefix=f"{job.id}-product-sample",
        )
        image_url, fmt = poster_url, ContentPost.FORMAT_REEL
    else:
        image_url = product_gen.generate_image(
            photo_bytes, brand_dna.business_name, filename=f"{job.id}-product-sample",
        )
        video_url, fmt = '', ContentPost.FORMAT_SINGLE

    if not image_url and not video_url:
        calendar.delete()
        job.mark_failed('El control de calidad rechazó el resultado (posible alucinación de logo/texto). Reintenta.')
        return
```

Nuevo (adapta las llamadas al nuevo contrato con razón):

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

## C. IMG-08 — Filtro de nicho sensible: proximidad + reescritura granular

En `reel_script_generator.py`, dentro de `ReelScriptGenerator.generate()`,
reemplazar el bloque actual:

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

Y agregar la función `_has_banned_promise_language` (módulo-level, cerca
de `_BRAND_LEAK_KEYWORDS`):

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

`garantizado`/`garantizamos`/`asegurar`/`aseguramos` se quedan como
detección directa (no ambiguos en español, siempre implican promesa).
`100%` solo cuenta si aparece a ~40 caracteres de una palabra de
promesa de resultado — evita el falso positivo real de "empresa 100%
mexicana" sin perder cobertura de "resultados 100% garantizados" o
"100% efectivo".

**Nota de orden de ejecución**: este cambio va DESPUÉS del backstop de
placeholder de la sección D (D.2) en el flujo de `generate()`, ya que
ambos tocan `narration_script` — el backstop de placeholder corre
primero (determinístico, barato), la revisión de nicho sensible
después (puede requerir una llamada extra a Gemini vía
`rewrite_for_brand_consistency`).

## D. IMG-10 — Español latinoamericano + backstop de placeholder

### D.1 — Instrucción de tuteo en `_PROMPT`, punto 4 (narration_script)

Reemplazar el punto 4 completo:

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

### D.2 — Backstop determinista del placeholder (nuevo, mismo patrón que `_scrub_brand_leak`)

Agregar cerca de `_scrub_brand_leak`:

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

Aplicar inmediatamente después de construir `result['narration_script']`
en `generate()` (antes de la revisión de nicho sensible de la sección C,
ver nota de orden arriba):

```python
            result['narration_script'] = _fix_marca_placeholder(result['narration_script'], brand_dna.business_name)
```

## Testing (todas las secciones)

- Tests nuevos por función/comportamiento agregado — no reemplazar
  tests existentes salvo que el contrato cambie (B cambia el contrato de
  `_validate_scene`/`generate_image`/`generate_reel`, todos los tests
  existentes de `test_product_reference_generator.py` que llaman estos
  métodos necesitan actualizar sus asserts al nuevo shape de retorno).
- `_has_banned_promise_language`: casos de tabla — "garantizado" solo
  → True; "100% mexicana" → False; "100% garantizado" → True; "resultados
  100% efectivos" → True.
- `_fix_marca_placeholder`: "Marca. Creamos..." → reemplazado; "[Marca]
  ofrece..." → reemplazado; "Nuestra marca de agua es..." (uso legítimo
  de la palabra) → sin cambio (no calza con el regex, que exige inicio
  de oración o corchetes).
- `_describe_qc_failure`: 1 test por cada rama de mensaje + el caso
  `{}` (fail-open) → mensaje genérico.
- Los 3 QC prompts + schemas: test que confirma que el campo
  `has_suggestive_or_exposed_content=true` hace que `ok` se evalúe
  como rechazado (reutilizar patrón de tests existentes de
  `has_text`/etc. en cada archivo).

## Fuera de alcance

- IMG-06, IMG-07, efecto elefante rosa, migración de modelo — ver
  Contexto arriba.
- Detección programática de marcas de agua en la foto de entrada
  (análisis de imagen antes de generar) — el mensaje de B.1 es
  educativo/orientativo, no una detección real; si se decide construir
  detección real más adelante, es una feature nueva, no parte de este
  spec.
