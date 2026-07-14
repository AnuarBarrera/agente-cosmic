# Rediseño de pilares de contenido — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reemplazar 3 pilares de contenido del calendario semanal (días 3, 4, 7) que dependían de datos que Agente Cosmic no captura (testimonios reales, proceso interno, historia del fundador) por pilares que rinden bien con la información ya disponible (descripción breve + keywords + audiencia + tono), sin pedir nada nuevo al usuario.

**Architecture:** Cambio de datos/prompt en 2 archivos existentes — `CONTENT_PILLARS` en `text_generator.py` y el prompt de `_generate_carousel_slides_content` en `image_generator.py` (generalizado para dejar de estar atado a "prueba social/testimonio"). Sin nuevas dependencias, sin migración de base de datos.

**Tech Stack:** Django, Gemini vía `google-genai` SDK (Vertex AI), pytest + `django.test.override_settings`.

## Global Constraints

- Los 3 pilares nuevos son exactamente: día 3 `'Antes y despues'`, día 4 `'Beneficio en profundidad'`, día 7 `'Conexion emocional'` — nombres y ángulos exactos, ver Task 1.
- El pilar del día 3 sigue usando formato carrusel (`CAROUSEL_DAY = 3` no cambia).
- El prompt de carrusel debe narrar la transformación **desde la marca, nunca desde la voz de un cliente** — ni siquiera "representativa" (el framing anterior de "un cliente nos comentó..." se elimina por completo, no se ajusta).
- No pedir información adicional al usuario en ningún punto de este cambio — es puro rework de prompt/copy sobre los datos ya capturados.
- No tocar ningún otro pilar (días 1, 2, 5, 6 quedan exactamente igual).

---

### Task 1: Reemplazar 3 pilares en `CONTENT_PILLARS`

**Files:**
- Modify: `core/content_pipeline/generators/text_generator.py:16-28`
- Test: `core/content_pipeline/tests/test_text_generator.py`

**Interfaces:**
- Consumes: nada nuevo — modifica la constante `CONTENT_PILLARS: list[dict]` ya existente (cada dict tiene `day: int`, `name: str`, `angle: str`).
- Produces: `CONTENT_PILLARS` con los 3 pilares nuevos — consumido por Task 2 (el comentario de `CAROUSEL_DAY` referencia el nombre del pilar del día 3) y por `TextGenerator.generate()` (sin cambios de interfaz, ya iteraba sobre esta constante).

- [ ] **Step 1: Escribir el test que falla**

Agregar al final de `core/content_pipeline/tests/test_text_generator.py`:

```python
def test_pillars_day_3_4_7_match_rework_spec():
    from core.content_pipeline.generators.text_generator import CONTENT_PILLARS
    by_day = {p['day']: p for p in CONTENT_PILLARS}
    assert by_day[3]['name'] == 'Antes y despues'
    assert by_day[4]['name'] == 'Beneficio en profundidad'
    assert by_day[7]['name'] == 'Conexion emocional'
    assert len(CONTENT_PILLARS) == 7
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_text_generator.py::test_pillars_day_3_4_7_match_rework_spec -v`
Expected: FAIL — `assert 'Prueba social' == 'Antes y despues'`

- [ ] **Step 3: Reemplazar las 3 entradas en `CONTENT_PILLARS`**

En `core/content_pipeline/generators/text_generator.py`, el bloque actual (líneas 16-24) es:

```python
CONTENT_PILLARS = [
    {'day': 1, 'name': 'Producto', 'angle': 'Presenta que vendes o que servicio ofreces de forma directa y atractiva.'},
    {'day': 2, 'name': 'Diferenciador', 'angle': 'Explica que te hace unico frente a otras opciones del mismo mercado.'},
    {'day': 3, 'name': 'Prueba social', 'angle': 'Comparte un testimonio o resultado representativo de un cliente satisfecho, sin inventar datos verificables falsos.'},
    {'day': 4, 'name': 'Detras de camaras', 'angle': 'Muestra el proceso, la fabricacion, o el dia a dia detras del negocio.'},
    {'day': 5, 'name': 'Educativo', 'angle': 'Comparte un tip o dato util relevante para tu audiencia, sin vender directamente.'},
    {'day': 6, 'name': 'CTA / Oferta', 'angle': 'Invita a la accion de forma directa — una oferta, promocion, o llamada clara a contactar.'},
    {'day': 7, 'name': 'Historia de marca', 'angle': 'Cuenta la historia del fundador o el origen del negocio — conexion personal.'},
]
```

Reemplazarlo por (solo cambian las entradas de los días 3, 4 y 7):

```python
CONTENT_PILLARS = [
    {'day': 1, 'name': 'Producto', 'angle': 'Presenta que vendes o que servicio ofreces de forma directa y atractiva.'},
    {'day': 2, 'name': 'Diferenciador', 'angle': 'Explica que te hace unico frente a otras opciones del mismo mercado.'},
    {'day': 3, 'name': 'Antes y despues', 'angle': 'Cuenta una transformacion tipica: el problema que enfrenta tu audiencia antes de conocerte y como tu producto/servicio cambia esa situacion — ilustrativo, sin inventar datos verificables falsos.'},
    {'day': 4, 'name': 'Beneficio en profundidad', 'angle': 'Profundiza en UN beneficio o caracteristica especifica de tu producto/servicio (distinto al enfoque general del dia 1).'},
    {'day': 5, 'name': 'Educativo', 'angle': 'Comparte un tip o dato util relevante para tu audiencia, sin vender directamente.'},
    {'day': 6, 'name': 'CTA / Oferta', 'angle': 'Invita a la accion de forma directa — una oferta, promocion, o llamada clara a contactar.'},
    {'day': 7, 'name': 'Conexion emocional', 'angle': 'Describe como se siente tu cliente al usar tu producto/servicio — la emocion o sensacion que genera (tranquilidad, confianza, orgullo, alivio), sin afirmar resultados o datos verificables.'},
]
```

Inmediatamente después, el comentario que precede a `CAROUSEL_DAY = 3` (líneas 26-28) cambia de:

```python
# El pilar "Prueba social" se presta naturalmente a un formato de varias slides
# (antes/despues, cita del cliente, resultado, CTA) — es el unico dia que usa carrusel.
CAROUSEL_DAY = 3
```

a:

```python
# El pilar "Antes y despues" se presta naturalmente a un formato de varias slides
# (problema, transicion, beneficio, CTA) — es el unico dia que usa carrusel.
CAROUSEL_DAY = 3
```

- [ ] **Step 4: Correr el test y verificar que pasa**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_text_generator.py::test_pillars_day_3_4_7_match_rework_spec -v`
Expected: PASS

- [ ] **Step 5: Correr toda la suite de `test_text_generator.py` para verificar que nada se rompió**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_text_generator.py -v`
Expected: todos los tests pasan — `test_generate_tags_each_post_with_its_pillar`,
`test_generate_marks_only_carousel_day_as_carousel_format` y
`test_generate_prompt_includes_pillars_block` referencian `CONTENT_PILLARS`
dinámicamente y deben seguir en verde sin modificarlos.

- [ ] **Step 6: Commit**

```bash
git add core/content_pipeline/generators/text_generator.py core/content_pipeline/tests/test_text_generator.py
git commit -m "feat(pilares): reemplazar dias 3/4/7 por pilares que rinden con poca informacion"
```

---

### Task 2: Generalizar el prompt de carrusel (dejar de depender de "prueba social/testimonio")

**Files:**
- Modify: `core/content_pipeline/generators/image_generator.py` (método `_generate_carousel_slides_content`)
- Test: `core/content_pipeline/tests/test_image_generator.py`

**Interfaces:**
- Consumes: nada de Task 1 en tiempo de ejecución (el nombre del pilar del día 3 solo aparece en un comentario y en la línea de contexto del prompt de este método — no hay import ni dependencia real entre ambos archivos).
- Produces: `ImageGenerator._generate_carousel_slides_content(self, caption: str, brand_context: str = '', num_slides: int = 4) -> list[dict]` — misma firma que ya existe, sin cambios de tipo. Cada dict sigue teniendo `{'headline': str, 'subtitle': str, 'cta': str, 'tag': str}`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de la clase `TestGenerateCarouselSlidesContent` en
`core/content_pipeline/tests/test_image_generator.py` (después del método
`test_fills_missing_items_with_fallback_when_gemini_returns_fewer`, respetando la
indentación de 4 espacios de la clase):

```python
    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_fallback_uses_transformacion_tag_and_headlines(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_vc.return_value.models.generate_content.side_effect = Exception('API error')
            slides = gen._generate_carousel_slides_content('Nuestro servicio ayuda a resolver X', num_slides=3)
        assert all(s['tag'] == 'TRANSFORMACION' for s in slides)
        assert slides[0]['headline'] == 'Antes y despues 1'
        assert slides[1]['headline'] == 'Antes y despues 2'

    @override_settings(
        GOOGLE_CLOUD_PROJECT='agente-cosmic',
        GOOGLE_CLOUD_LOCATION='us-central1',
        VERTEX_TEXT_MODEL='publishers/google/models/gemini-2.5-flash',
    )
    def test_prompt_does_not_mention_prueba_social_or_testimonio(self):
        from core.content_pipeline.generators.image_generator import ImageGenerator
        gen = ImageGenerator(bucket_name='test-bucket')
        with patch('core.content_pipeline.generators.image_generator._vertex_client') as mock_vc:
            mock_resp = MagicMock()
            mock_resp.text = '[{"headline":"H","subtitle":"S","cta":"Desliza","tag":"TAG"}]'
            mock_vc.return_value.models.generate_content.return_value = mock_resp
            gen._generate_carousel_slides_content('Caption', num_slides=1)
        prompt_sent = mock_vc.return_value.models.generate_content.call_args.kwargs['contents'].lower()
        assert 'prueba social' not in prompt_sent
        assert 'testimonio' not in prompt_sent
        assert 'un cliente nos comento' not in prompt_sent
        assert 'problema' in prompt_sent
        assert 'beneficio' in prompt_sent
```

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_image_generator.py::TestGenerateCarouselSlidesContent -v`
Expected: `test_fallback_uses_transformacion_tag_and_headlines` FAIL (`'TESTIMONIO' != 'TRANSFORMACION'`)
y `test_prompt_does_not_mention_prueba_social_or_testimonio` FAIL (`'prueba social' in prompt_sent`).
Los 4 tests preexistentes de esta clase deben seguir en PASS.

- [ ] **Step 3: Reemplazar el fallback**

En `core/content_pipeline/generators/image_generator.py`, dentro de
`_generate_carousel_slides_content`, el bloque actual es:

```python
        _fallback_single = {
            'headline': self._extract_headline(caption),
            'subtitle': (caption[:120] if caption else '').strip(),
            'cta': 'Contáctanos hoy',
            'tag': 'TESTIMONIO',
        }
        fallback = [
            {
                'headline': _fallback_single['headline'] if i == num_slides - 1 else f"Testimonio {i + 1}",
                'subtitle': _fallback_single['subtitle'],
                'cta': _fallback_single['cta'] if i == num_slides - 1 else 'Desliza para ver más',
                'tag': _fallback_single['tag'],
            }
            for i in range(num_slides)
        ]
```

Reemplazarlo por:

```python
        _fallback_single = {
            'headline': self._extract_headline(caption),
            'subtitle': (caption[:120] if caption else '').strip(),
            'cta': 'Contáctanos hoy',
            'tag': 'TRANSFORMACION',
        }
        fallback = [
            {
                'headline': _fallback_single['headline'] if i == num_slides - 1 else f"Antes y despues {i + 1}",
                'subtitle': _fallback_single['subtitle'],
                'cta': _fallback_single['cta'] if i == num_slides - 1 else 'Desliza para ver más',
                'tag': _fallback_single['tag'],
            }
            for i in range(num_slides)
        ]
```

- [ ] **Step 4: Reemplazar el prompt**

Inmediatamente después (mismo método), el bloque actual es:

```python
            ctx_line = f"ADN de marca: {brand_context}\n" if brand_context else ""
            prompt = (
                f"{ctx_line}"
                f"Caption del post (pilar de prueba social/testimonio): \"{caption[:300]}\"\n\n"
                f"Genera el contenido para un CARRUSEL de Instagram de exactamente {num_slides} slides "
                "que cuenten una historia de prueba social en secuencia (ej: problema -> solucion -> "
                "resultado -> cierre). Cada slide tiene 4 elementos:\n"
                "1. headline: 3-6 palabras. Frase gancho para ese momento de la historia.\n"
                "2. subtitle: 6-14 palabras. Amplia el headline. Español correcto.\n"
                "3. cta: 2-4 palabras. En las slides intermedias usa una invitacion a seguir "
                "viendo (ej. 'Desliza para ver más'); en la ULTIMA slide usa una llamada a la "
                "accion real conectada al negocio (ej. 'Contáctanos hoy').\n"
                "4. tag: 1-3 palabras EN MAYUSCULAS. Igual en todas las slides, categoria del sector.\n\n"
                "REGLAS: Español impecable. Sin inventar palabras. No inventes datos verificables "
                "falsos (cifras exactas, nombres reales) — usa lenguaje representativo tipo "
                "'un cliente nos comento...'.\n"
                f"Responde UNICAMENTE con un array JSON de {num_slides} objetos, EN ORDEN NARRATIVO, "
                "sin markdown:\n"
                '[{"headline":"...","subtitle":"...","cta":"...","tag":"..."}]'
            )
```

Reemplazarlo por:

```python
            ctx_line = f"ADN de marca: {brand_context}\n" if brand_context else ""
            prompt = (
                f"{ctx_line}"
                f"Caption del post (pilar antes y despues): \"{caption[:300]}\"\n\n"
                f"Genera el contenido para un CARRUSEL de Instagram de exactamente {num_slides} slides "
                "que cuenten una transformacion en secuencia, narrada desde la marca (NO desde la voz "
                "de un cliente): el problema que enfrenta la audiencia -> como tu producto/servicio "
                "ayuda -> el beneficio que obtiene -> cierre. Cada slide tiene 4 elementos:\n"
                "1. headline: 3-6 palabras. Frase gancho para ese momento de la historia.\n"
                "2. subtitle: 6-14 palabras. Amplia el headline. Español correcto.\n"
                "3. cta: 2-4 palabras. En las slides intermedias usa una invitacion a seguir "
                "viendo (ej. 'Desliza para ver más'); en la ULTIMA slide usa una llamada a la "
                "accion real conectada al negocio (ej. 'Contáctanos hoy').\n"
                "4. tag: 1-3 palabras EN MAYUSCULAS. Igual en todas las slides, categoria del sector.\n\n"
                "REGLAS: Español impecable. Sin inventar palabras. No inventes datos verificables "
                "falsos (cifras exactas, nombres de clientes reales, resultados especificos) — "
                "mantente en lenguaje ilustrativo y general sobre el problema/beneficio, nunca "
                "atribuido a un cliente especifico.\n"
                f"Responde UNICAMENTE con un array JSON de {num_slides} objetos, EN ORDEN NARRATIVO, "
                "sin markdown:\n"
                '[{"headline":"...","subtitle":"...","cta":"...","tag":"..."}]'
            )
```

El resto del método (llamada a `client.models.generate_content`, `system_instruction`,
parseo de la respuesta) no cambia.

- [ ] **Step 5: Correr los tests y verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_image_generator.py::TestGenerateCarouselSlidesContent -v`
Expected: PASS (6 passed — 4 preexistentes + 2 nuevos)

- [ ] **Step 6: Correr toda la suite de `test_image_generator.py` para verificar que nada más se rompió**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_image_generator.py -v`
Expected: todos los tests pasan.

- [ ] **Step 7: Commit**

```bash
git add core/content_pipeline/generators/image_generator.py core/content_pipeline/tests/test_image_generator.py
git commit -m "fix(carrusel): generalizar prompt de 'prueba social/testimonio' a transformacion narrada desde la marca"
```

---

## Verificación manual post-implementación (no automatizable)

Después de que ambas tareas estén commiteadas y los contenedores recreados
(`docker compose up -d --force-recreate --no-deps backend rqworker` —
`DEBUG=False` cachea código, ver memoria `feedback_gunicorn_restart.md`), generar
un calendario de prueba completo (job real, no mocks) y que Anuar revise
manualmente que los posts de los días 3, 4 y 7 lean bien y no suenen genéricos ni
inventados — es un criterio de lectura humana, no automatizable con tests.
