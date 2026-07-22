# Auditor de consistencia de marca — Diseño

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Detectar y corregir, antes de que lleguen al usuario, casos donde el texto que Gemini genera dentro del pipeline de contenido (captions, narración de reels, hooks, CTAs, prompts de escena) es técnicamente válido pero perjudica el posicionamiento de la marca — por ejemplo, parafrasea un término propio de la marca (ej. "upcycling") por un sinónimo con connotación inferior (ej. "materiales reutilizados"), o usa un tono/registro distinto al de la marca.

**Contexto / motivación:** HALLAZGO 78 (`hallazgos.txt`) — el `narration_script` de un reel real dijo "materiales reutilizados" en vez de "upcycling", dañando el posicionamiento premium/sostenible de esa marca, pese a que `BrandDNA.description` y la caption del mismo post sí usan "upcycling" correctamente. Mismo patrón que un reporte anterior sin hallazgo formal sobre narración con "voz muy catalana". Ya existe QC para imágenes (descarta alucinaciones) y para seguridad/compliance de captions (`caption_safety_qc` en `text_generator.py` — promesas absolutas, afirmaciones no verificables), pero ningún QC evalúa si el texto generado es *consistente con la identidad de esa marca en particular*.

**Fuera de alcance de este spec** (decisión explícita, ver discusión de brainstorming): variabilidad/anti-repetición entre generaciones (ej. un reel reutilizando la misma imaginería que uno anterior). Ese problema requiere memoria/historial entre generaciones — un cambio más grande, ligado al gap ya diferido de "TextGenerator sin memoria entre semanas" — y se diseñará en un spec separado.

**Arquitectura:** Un módulo nuevo y compartido, `core/content_pipeline/generators/brand_consistency_qc.py`, con dos funciones puras (sin estado) que tanto `TextGenerator` como `ReelScriptGenerator` importan y usan tras generar su contenido, antes de devolverlo a su llamador. Sigue el mismo patrón ya establecido en el pipeline (`caption_safety_qc` en `text_generator.py`, `_scrub_brand_leak` en `reel_script_generator.py`): un paso de QC post-generación, con reintento de reescritura si falla, y fail-open ante cualquier error propio.

**Tech Stack:** Django, `google-genai` SDK contra Vertex AI (mismo cliente/modelo `settings.VERTEX_TEXT_MODEL` que el resto del pipeline), sin dependencias nuevas.

## Global Constraints

- El auditor corre **siempre**, en toda generación (no solo en nicho sensible o sin `business_url`, a diferencia de `caption_safety_qc`) — decisión explícita del dueño del producto, confirmada en brainstorming: los 2 casos reales que motivan este spec no caían en esas condiciones.
- Fail-open: cualquier error en la llamada de auditoría (red, JSON no parseable, excepción) hace que la función devuelva "todo ok" (`{}`) y el pipeline continúa con el texto original — nunca bloquea ni falla la generación de contenido por un error propio del auditor. Mismo principio ya usado en `moderation.py` y en `_validate_caption_safety`.
- Una sola llamada a Gemini por auditoría (todos los campos de una generación juntos en un solo JSON de respuesta), no una llamada por campo — eficiencia de costo y contexto compartido entre campos.
- Reescritura (`rewrite_for_brand_consistency`) es por campo individual — solo se reescribe el campo marcado, los demás quedan intactos.
- Una sola pasada: audita una vez, y si un campo se marca, lo reescribe una vez — sin re-auditar el resultado de la reescritura. A diferencia de `caption_safety_qc` (que es un gate de seguridad obligatorio y sí reintenta hasta `max_qc_retries`), este auditor es una mejora de calidad best-effort — no vale la pena el costo/latencia de una segunda vuelta de verificación. El texto reescrito se usa tal cual, incluso si en teoría pudiera seguir sin ser perfecto.
- `music_mood` (campo de `ReelScriptGenerator`) queda **excluido** del auditor — es un parámetro de producción musical, no texto que represente voz o posicionamiento de marca.
- Todas las llamadas a Gemini nuevas deben usar `labels=vertex_labels()` (convención ya establecida en todo el pipeline para separar costos por origen en BigQuery).
- El criterio de "problema" debe ser conservador: solo casos claros donde el cambio daña activamente el posicionamiento (sustitución de término propio de marca por sinónimo de connotación inferior, o tono que no coincide con `brand_dna.tone`) — nunca preferencias de estilo menores. Mismo espíritu de "solo casos claros" ya usado en `moderation.py::check_business_legitimacy`.

---

## Componentes

### 1. `core/content_pipeline/generators/brand_consistency_qc.py` (nuevo)

Módulo compartido, sin clase — funciones sueltas, siguiendo el estilo de `text_generator.py`/`reel_script_generator.py` (que también mezclan funciones de módulo con una clase generadora).

```python
def audit_brand_consistency(fields: dict[str, str], brand_dna: BrandDNA) -> dict[str, str]:
    """
    fields: {nombre_de_campo: texto_a_revisar}
    Devuelve {nombre_de_campo: razon} SOLO para los campos con problema.
    Diccionario vacio = todo esta bien. Fail-open: cualquier error (red,
    parseo, excepcion) devuelve {} — nunca levanta.
    """

def rewrite_for_brand_consistency(field_name: str, text: str, reason: str, brand_dna: BrandDNA) -> str:
    """
    Reescribe un unico campo para corregir 'reason', preservando el mensaje
    y longitud aproximada. Si la llamada falla, devuelve el texto original
    sin cambios (nunca levanta, nunca devuelve texto vacio).
    """
```

**Prompt de auditoría** (`_AUDIT_PROMPT`, análogo a `_SAFETY_QC_PROMPT` de `text_generator.py`):

```python
_AUDIT_PROMPT = (
    "Eres un auditor de identidad de marca. Evalua si estos textos generados "
    "por IA son consistentes con la marca, o si accidentalmente cambiaron "
    "terminologia o tono de forma que perjudica su posicionamiento.\n\n"
    "MARCA: {business_name}\n"
    "DESCRIPCION (fuente de verdad de terminologia/posicionamiento): {description}\n"
    "TONO: {tone}\n"
    "KEYWORDS: {keywords}\n\n"
    "TEXTOS A EVALUAR:\n{fields_block}\n\n"
    "Marca un problema en un campo SOLO si:\n"
    "- Reemplaza un termino especifico de la marca (presente en la descripcion "
    "o keywords) por un sinonimo generico con connotacion distinta o inferior "
    "(ej: \"upcycling\" -> \"materiales reutilizados\" suena a segunda mano, "
    "cuando upcycling es un termino de moda sostenible premium).\n"
    "- El tono no coincide con {tone} (ej: mezcla registros, usa un acento o "
    "variante regional inesperada).\n"
    "NO marques problemas de gusto o estilo menores — solo casos donde el "
    "cambio daña activamente el posicionamiento de la marca.\n\n"
    "Responde UNICAMENTE con este JSON (sin markdown), una entrada por cada "
    "campo listado arriba:\n"
    '{{"nombre_campo": {{"ok": <bool>, "reason": "..."}}, ...}}'
)
```

`fields_block` se arma como `"{nombre}: \"{texto}\""` por línea, en el mismo orden que `fields.items()`.

**Prompt de reescritura** (`_REWRITE_PROMPT`, análogo a `_SAFETY_FIX_PROMPT`):

```python
_REWRITE_PROMPT = (
    "Reescribe el siguiente texto para corregir este problema de consistencia "
    "de marca: {reason}\n\n"
    "Texto original: \"{text}\"\n"
    "Terminologia/posicionamiento de referencia (descripcion de la marca): {description}\n"
    "Tono de la marca: {tone}\n\n"
    "Manten el mismo mensaje central y longitud aproximada. Responde "
    "UNICAMENTE con el texto corregido, sin comillas ni explicaciones."
)
```

Ambas funciones usan `client.models.generate_content(model=settings.VERTEX_TEXT_MODEL, ..., config=types.GenerateContentConfig(labels=vertex_labels()))`, envueltas en `track_external_api('gemini', operation='brand_consistency_audit')` / `operation='brand_consistency_fix'`, y registran tokens con `record_tokens(...)` — mismo patrón que cada llamada existente en el pipeline.

### 2. `core/content_pipeline/generators/reel_script_generator.py` (modificado)

En `ReelScriptGenerator.generate()`, después de construir `result` (ya con `scene_prompts` pasado por `_scrub_brand_leak`) y antes del `return result`:

```python
fields_to_audit = {
    'hook_text': result['hook_text'],
    'tag_cta': result['tag_cta'],
    'narration_script': result['narration_script'],
    'scene_prompts': ' | '.join(result['scene_prompts']),
}
issues = audit_brand_consistency(fields_to_audit, brand_dna)
for field_name, reason in issues.items():
    if field_name == 'scene_prompts':
        continue  # ver nota abajo — no se reescribe como bloque unico
    fixed = rewrite_for_brand_consistency(field_name, result[field_name], reason, brand_dna)
    result[field_name] = fixed
```

Nota: `scene_prompts` se audita como un solo texto unido (para darle contexto completo al auditor) pero **no se reescribe automaticamente como bloque** si se marca — reescribir 6 escenas en un solo texto libre rompería el formato de lista que consume Veo/Imagen. Si el auditor marca `scene_prompts`, se registra un `logger.warning` con el `reason` para visibilidad, pero no se auto-corrige en esta primera versión (igual que hoy no existe ningún caso real documentado de este campo específico — ver nota de alcance en la introducción). Reescritura automática de `scene_prompts` queda fuera de este spec; si en el futuro aparece un caso real, se puede extender reescribiendo escena por escena con el mismo mecanismo que `_scrub_brand_leak`.

### 3. `core/content_pipeline/generators/text_generator.py` (modificado)

En `TextGenerator.generate()`, después de asignar `pillar`/`format` a cada post y **antes** del bloque condicional de `_ensure_safe_caption` (que sigue corriendo solo en nicho sensible/sin URL, sin cambios — es un eje distinto):

```python
captions_to_audit = {f"post_{i}": p['caption'] for i, p in enumerate(posts)}
issues = audit_brand_consistency(captions_to_audit, brand_dna)
for field_name, reason in issues.items():
    idx = int(field_name.split('_')[1])
    posts[idx]['caption'] = rewrite_for_brand_consistency(
        field_name, posts[idx]['caption'], reason, brand_dna,
    )
```

Import nuevo en ambos archivos: `from core.content_pipeline.generators.brand_consistency_qc import audit_brand_consistency, rewrite_for_brand_consistency`.

## Manejo de errores

- `audit_brand_consistency`: try/except envolviendo la llamada a Gemini + parseo de JSON. Cualquier excepción → `logger.warning(...)` + `return {}`. Nunca propaga.
- `rewrite_for_brand_consistency`: try/except envolviendo la llamada. Cualquier excepción → `logger.error(...)` + `return text` (el original, sin cambios). Nunca propaga, nunca devuelve cadena vacía.
- Ninguna de las dos funciones puede hacer que `TextGenerator.generate()` o `ReelScriptGenerator.generate()` fallen — si Vertex AI está caído, el pipeline de contenido sigue funcionando exactamente como hoy, sin auditoría.

## Testing

Nuevo archivo `core/content_pipeline/tests/test_brand_consistency_qc.py`:
- `audit_brand_consistency` devuelve `{}` cuando Gemini responde que todos los campos están `ok: true`.
- `audit_brand_consistency` devuelve `{campo: reason}` cuando Gemini marca un campo como `ok: false`.
- `audit_brand_consistency` devuelve `{}` (fail-open) si la llamada a Gemini lanza una excepción.
- `audit_brand_consistency` devuelve `{}` (fail-open) si la respuesta no es JSON parseable.
- `rewrite_for_brand_consistency` devuelve el texto reescrito cuando Gemini responde exitosamente.
- `rewrite_for_brand_consistency` devuelve el texto original si la llamada falla.

Modificar `core/content_pipeline/tests/test_reel_script_generator.py`:
- Nuevo test: si `audit_brand_consistency` marca `narration_script`, el resultado final de `generate()` usa el texto reescrito devuelto por `rewrite_for_brand_consistency` (mockeado).
- Nuevo test: si `audit_brand_consistency` marca `scene_prompts`, el resultado final mantiene los `scene_prompts` sin cambios (no se reescribe ese campo) y se loggea un warning.
- Nuevo test: si `audit_brand_consistency` devuelve `{}`, ningún campo se reescribe (se verifica que `rewrite_for_brand_consistency` — mockeado — no se llama).

Modificar `core/content_pipeline/tests/test_text_generator.py` (o el archivo de tests existente para `TextGenerator`):
- Nuevo test: si `audit_brand_consistency` marca `post_2`, solo la caption del post en índice 2 cambia; las demás quedan intactas.
- Nuevo test: la auditoría corre siempre, incluso para un negocio de nicho NO sensible con `business_url` presente (a diferencia de `_ensure_safe_caption`, que en ese caso se salta) — verificar con mocks que `audit_brand_consistency` se llama independientemente de `_is_sensitive_niche`/`business_url`.

Todos los tests mockean `genai.Client` (mismo patrón ya usado en los archivos de test existentes de este directorio — `MockClient.return_value.models.generate_content`), sin llamadas reales a Vertex AI.

## Costo estimado

1 llamada de auditoría por tarea de generación (1 para las 7 captions de `TextGenerator`, 1 para los 4 campos de `ReelScriptGenerator`) + llamadas de reescritura solo para campos marcados (caso esperado: raro). Con los costos observados esta semana (~$0.15–0.30 MXN por llamada de Gemini Flash con prompts de este tamaño), el costo adicional por calendario semanal completo es del orden de $0.30–0.60 MXN en el caso sin ningún campo marcado — insignificante frente al costo de Veo (~$100 MXN por video) e Imagen (~$37 MXN por 7 imágenes) ya medido en la prueba de costo semanal de esta sesión.
