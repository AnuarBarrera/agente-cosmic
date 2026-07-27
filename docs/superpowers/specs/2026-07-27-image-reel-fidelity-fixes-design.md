# Fidelidad de imagen/reel al producto real — Diseño

**Fecha:** 2026-07-27
**Origen:** Feedback real de 3 negocios en la misma semana (Sony, Abraham/Gelatinas Marba,
Jorge/Trajes Dayian) — los 3 dicen una versión del mismo problema: "las imágenes/el reel no
reflejan lo que yo vendo". Cruzado con el punto 7 de `ultimosCambios.md` (auditor de
consistencia de marca sin enganchar en `image_generator.py`) y con evidencia visual directa
de Anuar sobre videos reales (logos inventados, alucinaciones de física/zoom).

## Contexto

Testimonios reales:
- **Sony**: "Me gusta, el diálogo está muy bien solo que siento que las imágenes no son lo
  [que] tengo, te hubiera mandado fotos de mi trabajo."
- **Abraham** (Gelatinas Marba): "Las gelatinas que muestra no son las que yo hago, hay una
  en forma de espiral y yo no hago personalizadas. La más parecida es la de frutos rojos."
- **Jorge** (Trajes Dayian): pidió que se muestre la elaboración real de las prendas, "algo
  creíble y no una nave o máquinas que no se utilizan en la confección" (mismo problema ya
  atacado parcialmente por el plan `2026-07-26-reel-image-quality-fixes` en
  `reel_script_generator.py`, pero solo para el reel — nunca se tocó `image_generator.py`).

Anuar (síntesis): "Tenemos dos versiones a mostrar: la interna (cómo construyo, qué
construyo, cómo es mi producto) y la externa (cómo se siente el consumidor después de
usarnos). Dado el poco contexto que tenemos, la externa es la que debemos usar — no podemos
saber ni alucinar el producto que vendemos, pero sí podemos generar una sensación de cómo se
sentirá el usuario final."

Además, Anuar reportó evidencia visual directa (revisión manual de videos reales) de 3
categorías de alucinación adicionales: logos/marcas inventadas en productos, zoom continuo
sin límite hacia un punto, y física de tela/objetos poco natural (una sábana con movimiento
imposible al tenderse).

## Decisiones de Anuar

- **Alcance**: se atacan los 3 problemas en un solo diseño (comparten contexto), aunque se
  implementan como tareas separadas.
- **Punto 7 (auditor)**: NO se reemplaza por el cambio de prompt — se hacen **ambos**: cambiar
  la estrategia de prompt en `image_generator.py` (Sección A) Y agregar un auditor de
  resultados (Sección B), ya que el cambio de prompt reduce el riesgo pero Imagen 3 puede
  seguir generando logos/texto no pedidos aunque el prompt no los solicite.
- **Qué debe detectar el auditor**: tanto texto/logo inventado (objetivamente detectable)
  como diseño de producto que no coincide con lo real (NO detectable sin fotos de
  referencia reales) — para lo segundo, la única mitigación posible es el cambio de prompt
  de la Sección A, no un auditor.
- **Inconsistencia espacial entre tomas** (ej. la misma cama de masajes con geometría
  distinta entre las 5 tomas independientes del reel): se documenta como limitación
  conocida, NO se ataca en este plan — requeriría un cambio de arquitectura mayor
  (referencia compartida entre generaciones independientes), no un ajuste de prompt.

## Diseño técnico

### A. Reenfoque de `_analyze_brand_scene` en `image_generator.py` (producto → experiencia)

**Problema confirmado en código**: `_analyze_brand_scene`
(`core/content_pipeline/generators/image_generator.py:265-330`) decide entre modo
`"product"` y `"lifestyle"`. El modo `"product"` (activado cuando el negocio o su
audiencia podría triggerear el content-safety de Imagen 3 con menores, ej. Gelatinas
Marba) pide explícitamente "focus on the product/food/objects only... artful food
arrangement" — esto es exactamente lo que falla: Imagen 3 no tiene ninguna referencia real
del diseño específico de ESE negocio, e inventa un diseño concreto y equivocado (la
gelatina en espiral de Abraham).

**Cambio 1** — instrucción de Gemini (STEP 2), líneas 293-297:

Antes:
```python
                f"STEP 2 — Generate a background prompt (max 80 words):\n"
                f"- If risk=YES → mode=\"product\": focus on the product/food/objects only, NO people of any age, NO hands.\n"
                f"  Think: overhead flat lay, artful food arrangement, colorful props matching brand palette.\n"
                f"- If risk=NO  → mode=\"lifestyle\": real-world scene reflecting the brand's world and customers.\n"
                f"  Think: service environment, lifestyle moment, nature matching brand values. NO offices or screens.\n\n"
```

Después:
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

**Cambio 2** — `_FALLBACK_PROMPT` (usado solo si la llamada a Gemini falla por completo),
líneas 275-283:

Antes:
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

Después:
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

`_PRODUCT_FALLBACKS`/`_SCENE_FALLBACKS` (líneas 228-251, usados solo cuando Imagen rechaza
un prompt por content-safety) **no se tocan** — ya son genéricos ("colorful ice cream
scoops", "beach scene"), nunca intentaron replicar el diseño real de un negocio específico.

### B. Auditor de logos/texto — extender a `reel_generator.py` (hueco real confirmado)

**Problema confirmado en código**: `image_generator.py` ya corre cada imagen por
`_validate_background` (líneas 363-414, revisa `has_text`/`is_abstract_3d`/
`has_screen_content`/`has_malformed_object`/`has_unrealistic_grounding` vía Gemini vision,
rechaza y reintenta con fallback si falla). **`reel_generator.py` no tiene ningún
equivalente** — `_generate_scene_still` (línea 649) va directo de generación a uso. Esto
explica por qué los logos siguen apareciendo en el reel aunque ya no en imagen/carrusel.

**Cambio 1** — agregar método nuevo a `ReelGenerator` (duplicado deliberado de
`_validate_background`, mismo patrón de este proyecto de no compartir módulos entre
generadores — ver `_FALLBACK_COLOR_POOL`/`MEXICO_TZ` ya duplicados así). Insertar después
de `_generate_scene_still` (línea 670):

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

Todos los símbolos usados (`_vertex_client`, `types`, `track_external_api`, `record_tokens`,
`vertex_labels`, `json`, `re`, `logger`) ya están importados/definidos en
`reel_generator.py` — no se necesita ningún import nuevo.

**Cambio 2** — enganchar la validación en `_generate_still_scene_clip` (línea 429-436):

Antes:
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

Después:
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

**Cambio 3** — fortalecer también la descripción de `has_text` en el `_validate_background`
YA EXISTENTE de `image_generator.py` (línea 372-373), ya que Anuar confirmó que los logos
inventados también aparecen en imagen/carrusel, no solo en el reel:

Antes:
```python
                "has_text: true if ANY readable letters, words, numbers or text appear ANYWHERE in the image — "
                "including text on signs, labels, books, packaging, walls, or any surface. "
                "Even partial words or blurry text count. Be very strict.\n"
```

Después:
```python
                "has_text: true if ANY readable letters, words, numbers or text appear ANYWHERE in the image — "
                "including text on signs, labels, books, packaging, walls, or any surface — OR any logo/brand "
                "mark of any kind, even a purely graphic symbol with no letters (real or invented). Even partial "
                "words or blurry text count. Be very strict.\n"
```

**Nota de alcance**: la toma 0 del reel (portada, generada con Veo real vía
`_generate_single_clip`, línea 610) queda FUERA de este QC — auditar contenido de un video
completo es sustancialmente más caro/complejo que una imagen fija. No se ataca en este plan.

### C. Reforzar `_VEO_SAFE_CONSTRAINTS` contra zoom/física (solo toma 0)

**Alcance real**: las escenas 1-5 del reel son imágenes fijas de Imagen 3 animadas con un
zoom scripteado por ffmpeg (`_animate_still_to_clip`, línea 671, `zoompan` capado a 1.08x
máximo) — el "zoom infinito" que reportó Anuar NO puede venir de ahí (está topado). Debe
venir de la ÚNICA toma con movimiento de cámara real generado por el modelo: la toma 0
(`_generate_single_clip`, Veo real, 8s). `_VEO_SAFE_CONSTRAINTS` (línea ~349-357, ya
citada completa en sesiones anteriores) se usa como `negative_prompt` tanto en la llamada a
Veo (`_generate_single_clip`, línea 624) como en la de Imagen (`_generate_scene_still`,
línea 659) — agregar lenguaje de zoom/física ahí es inofensivo para el camino de Imagen
(son términos que simplemente no aplican a una imagen fija, no tienen efecto negativo).

Antes:
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

Después:
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

## Fuera de alcance (documentado, no se ataca en este plan)

- **Inconsistencia espacial entre las 5 tomas independientes del reel** (ej. la misma
  locación con geometría distinta entre tomas) — decisión explícita de Anuar. Requiere un
  cambio de arquitectura (referencia compartida entre generaciones independientes de
  Imagen), no un ajuste de prompt. Documentar en `hallazgos.txt` como limitación conocida.
- **QC de la toma 0 del reel** (video Veo real) — solo se ataca vía negative-prompt
  (Sección C), no vía auditor post-generación (Sección B es solo para imágenes fijas).
- **HALLAZGO 82** (TTS pronunciación) y **HALLAZGO 83** (carrusel 1 sola imagen) — problemas
  reales relacionados pero de subsistemas distintos (audio TTS, decisión de costo de
  carrusel) — no se atacan en este plan, quedan documentados en `hallazgos.txt`.
- **Auditor para diseño de producto que no coincide con lo real** (el caso Sony/Abraham en
  sí) — explícitamente NO auditable sin fotos de referencia reales del negocio. Se mitiga
  solo vía el cambio de prompt de la Sección A, no vía QC.

## Testing

- `test_image_generator.py`: confirmado por grep (2026-07-27) que NINGÚN test existente
  depende del texto literal viejo (`"artful"`, `"Focus on the product"`, `"product
  photography"`, `"lifestyle photograph"` — cero resultados) — el cambio de prompt no
  rompe tests existentes. Tests nuevos: el prompt de Gemini enviado en
  `_analyze_brand_scene` debe contener el lenguaje nuevo ("DO NOT attempt to depict this
  business's exact product design" / "focus on how a customer FEELS"), no el lenguaje
  viejo. `_FALLBACK_PROMPT` actualizado debe seguir sin mencionar "artful arrangement" ni
  prometer "Focus on the product itself."
- `test_reel_generator.py`: `_validate_scene_still` — casos ok=true/false vía mock de
  `client.models.generate_content`, igual patrón que los tests existentes de
  `_validate_background` en `test_image_generator.py` (revisarlos primero como
  referencia). `_generate_still_scene_clip` — debe reintentar cuando `_validate_scene_still`
  devuelve `False` aunque `_generate_scene_still` no sea `None`; debe usar el resultado del
  reintento aunque también falle QC (no debe devolver `None` solo por fallar QC dos veces).
  `_VEO_SAFE_CONSTRAINTS` debe contener las frases nuevas de zoom/física.
- Suite completa del proyecto tras el plan, verificada por Claude independientemente.
