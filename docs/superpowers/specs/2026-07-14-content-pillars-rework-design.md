# Rediseño de pilares de contenido — Diseño

## Contexto

El calendario semanal de Agente Cosmic genera 7 posts, uno por día, cada uno con un
pilar de contenido (propósito estratégico) distinto — definidos en `CONTENT_PILLARS`
(`core/content_pipeline/generators/text_generator.py`). Esto afecta a **todo
usuario**, no solo testers.

De los 7 pilares actuales, 3 dependen de información del negocio que Cosmic no
captura — solo cuenta con la descripción breve extraída del sitio/redes durante el
análisis de Brand DNA (`business_name`, `description`, `keywords`, `audience`,
`tone`, `posting_style`, `common_hashtags` — ver `core/brand_dna/models.py`), no
testimonios reales, no fotos de proceso interno, no la historia real del fundador:

- **Día 3 — "Prueba social"**: pide un testimonio o resultado de cliente real.
- **Día 4 — "Detrás de cámaras"**: pide mostrar el proceso/fabricación real del negocio.
- **Día 7 — "Historia de marca"**: pide la historia real del fundador.

El código ya tiene un guardrail (`has_unverifiable_claim` en el QC de seguridad) que
evita que la IA invente datos verificables falsos, pero eso solo tapa el síntoma —
el post sale genérico esos días en vez de fuerte.

## Decisión de producto (Anuar, explícita)

- **No pedir información adicional al usuario** durante el onboarding — la gente no
  quiere dar más datos. La solución es reemplazar los pilares débiles por otros que
  rindan bien con lo que ya se captura hoy.
- Afecta a todos los usuarios, no solo testers (los pilares de contenido son parte
  del calendario estándar).

## Hallazgo técnico que afecta el diseño

El día 3 (`CAROUSEL_DAY`) no solo cambia de nombre — el generador de carrusel
(`image_generator.py::_generate_carousel_slides_content`) tiene su **propio prompt
independiente**, hardcodeado a "una historia de prueba social en secuencia", con
fallback que literalmente dice `tag: 'TESTIMONIO'` y `"Testimonio {i+1}"`. Cambiar
solo el nombre del pilar en `CONTENT_PILLARS` no habría cambiado este prompt —
quedarían desalineados. Decisión: generalizar el prompt del carrusel junto con el
pilar, no moverlo a otro día.

## Pilares de reemplazo (validados con Anuar)

- **Día 3 — "Antes y después"** (mantiene formato carrusel): arco narrativo
  problema → cómo ayuda el producto/servicio → beneficio → cierre, **narrado desde
  la marca, no desde la voz de un cliente** (ni siquiera de forma "representativa" —
  descartado explícitamente por riesgo de sonar a testimonio inventado).
- **Día 4 — "Beneficio en profundidad"**: profundiza en UN beneficio o
  característica específica del producto/servicio, distinto del enfoque general del
  día 1 (Producto).
- **Día 7 — "Conexión emocional"**: describe cómo se siente el cliente al usar el
  producto/servicio (tranquilidad, confianza, orgullo, alivio, pertenencia) —
  interpretativo, no factual, sin afirmar resultados o datos verificables. Se
  descartó explícitamente un pilar de "Preguntas frecuentes" por el mismo riesgo de
  fabricación que "Prueba social" (una FAQ inventada podría sonar a política o
  proceso real que Cosmic no conoce).

Pilares finales: Producto(1) → Diferenciador(2) → Antes y después(3, carrusel) →
Beneficio en profundidad(4) → Educativo(5) → CTA/Oferta(6) → Conexión emocional(7).

## Arquitectura

Cambio acotado a 2 archivos existentes. Sin nuevas dependencias, sin migración de
base de datos — el nombre del pilar nunca se persiste en `ContentPost` (es un campo
efímero en el dict `post` durante la generación, no un campo del modelo).

1. `core/content_pipeline/generators/text_generator.py` — reemplazar 3 entradas de
   `CONTENT_PILLARS`.
2. `core/content_pipeline/generators/image_generator.py` — generalizar el prompt
   (y el fallback) de `_generate_carousel_slides_content`.

Verificado que ningún otro archivo del proyecto referencia los nombres viejos de
pilares por string (`grep` confirmó cero coincidencias fuera de
`text_generator.py`), y que `_disable_carousel_if_full_product_week`
(`tasks.py`) opera sobre `post['format']`, no sobre el nombre del pilar — no hay
acoplamiento oculto adicional.

## Componentes

### `text_generator.py` — `CONTENT_PILLARS`

Reemplazar las entradas de los días 3, 4 y 7 (las demás no cambian):

```python
{'day': 3, 'name': 'Antes y despues', 'angle': 'Cuenta una transformacion tipica: el problema que enfrenta tu audiencia antes de conocerte y como tu producto/servicio cambia esa situacion — ilustrativo, sin inventar datos verificables falsos.'},
```
```python
{'day': 4, 'name': 'Beneficio en profundidad', 'angle': 'Profundiza en UN beneficio o caracteristica especifica de tu producto/servicio (distinto al enfoque general del dia 1).'},
```
```python
{'day': 7, 'name': 'Conexion emocional', 'angle': 'Describe como se siente tu cliente al usar tu producto/servicio — la emocion o sensacion que genera (tranquilidad, confianza, orgullo, alivio), sin afirmar resultados o datos verificables.'},
```

El comentario que precede a `CAROUSEL_DAY = 3` cambia de:

```python
# El pilar "Prueba social" se presta naturalmente a un formato de varias slides
# (antes/despues, cita del cliente, resultado, CTA) — es el unico dia que usa carrusel.
```

a:

```python
# El pilar "Antes y despues" se presta naturalmente a un formato de varias slides
# (problema, transicion, beneficio, CTA) — es el unico dia que usa carrusel.
```

### `image_generator.py` — `_generate_carousel_slides_content`

**Fallback** — cambiar:

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

**Prompt** — cambiar la línea de contexto y las instrucciones narrativas:

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

El `system_instruction` de la llamada (`"Eres 'Cosmic', Director Creativo..."`) no
cambia.

## Manejo de errores

No cambia — el `try/except` existente en `_generate_carousel_slides_content` ya
degrada al `fallback` (ahora con el nuevo framing) si Gemini falla o responde algo
no parseable. `TextGenerator.generate()` no tiene manejo de errores por pilar
individual (es una sola llamada para los 7 posts); eso no cambia con este rediseño.

## Testing

- `test_text_generator.py`: los tests existentes (`test_generate_tags_each_post_with_its_pillar`,
  `test_generate_marks_only_carousel_day_as_carousel_format`,
  `test_generate_prompt_includes_pillars_block`) referencian `CONTENT_PILLARS`
  dinámicamente, sin nombres hardcodeados — siguen pasando sin modificación y
  automáticamente cubren los 3 pilares nuevos.
- `test_image_generator.py`: nuevos tests sobre `_generate_carousel_slides_content`
  — verificar que el prompt enviado a Gemini NO contiene `"prueba social"` ni
  `"testimonio"` (case-insensitive), SÍ contiene el nuevo framing de transformación
  ("problema", "beneficio"), y que el fallback (forzando una excepción en la
  llamada a Gemini) devuelve `tag: 'TRANSFORMACION'` y headlines
  `"Antes y despues N"` en vez de `"Testimonio N"`.
- Sin llamadas reales a Gemini en la suite (mocks siempre). Verificación real
  post-implementación: generar un calendario de prueba completo (job real) y que
  Anuar revise manualmente que los 3 posts nuevos (días 3, 4, 7) lean bien y no
  suenen genéricos ni inventados — el criterio de "rinde bien con poca información"
  es de lectura humana, no automatizable.
