# No inventar sitio web en el copy generado — Diseño

## Contexto

Anuar probó el pipeline de carrusel en producción real y encontró que el CTA
invitaba a "visitar nuestra web" cuando el negocio nunca proporcionó una URL
durante el análisis de Brand DNA (`BrandDNA.business_url` quedó vacío,
`URLField(blank=True, default='')`). El dato existe en el modelo, pero **no se
pasa nunca** hacia los generadores de texto/copy — el `brand_context` que sí
reciben `_generate_post_content`/`_generate_carousel_slides_content`
(`core/content_pipeline/generators/image_generator.py`) se arma solo con
`description`/`tone`/`keywords`.

El mismo riesgo existe en 3 lugares más, no solo en el carrusel donde se vio el
bug: el CTA de posts individuales (`_generate_post_content`), el
headline/subtítulo (podrían mencionar "www.marca.com" también), y el caption
principal del post (`text_generator.py`, un generador de texto completamente
distinto, sin relación de código con `image_generator.py`).

## Decisiones de diseño (validadas con Anuar)

- **Alcance**: cubrir los 4 lugares (CTA/headline/subtítulo en posts
  individuales y carrusel, más el caption principal) — no solo el carrusel
  donde se confirmó el bug. Dejar los otros 3 sin arreglar sería dejar el
  mismo riesgo esperando a aparecer.
- **Mecanismo para CTA/headline/subtítulo**: chequeo determinístico (regex) +
  reemplazo por un valor seguro ya existente en el código — **sin llamada
  extra a Gemini**. Se descartó explícitamente la alternativa de un "auditor"
  vía Gemini para estos campos porque implicaría una llamada extra **en el
  100% de las generaciones, tengan o no URL** — el patrón de frases a detectar
  es acotado y no requiere juicio abierto de un LLM.
- **Excepción justificada para el caption**: el caption es prosa libre de
  varias oraciones — recortar una frase específica con regex es frágil y
  puede dejar el texto mal armado gramaticalmente. Para el caption se
  reutiliza el mecanismo de reescritura que YA EXISTE
  (`_validate_caption_safety`/`_SAFETY_FIX_PROMPT` en `text_generator.py`),
  extendido con un nuevo chequeo que corre **siempre que falte
  `business_url`** (no solo en nichos sensibles como hoy) — sigue sin pagar
  el costo extra salvo en el caso real que importa (negocio sin URL).
- Regla general: "si no la pusieron, seguro no hay, no inventemos que existe."

## Arquitectura

Cambio en 2 archivos existentes de generación de texto/copy + su plumbing en
`tasks.py`. Sin nuevas dependencias, sin cambios de modelo (el dato
`business_url` ya existe en `BrandDNA`).

1. `core/content_pipeline/generators/image_generator.py`: nueva función
   determinística `_sanitize_web_visit_mention`, aplicada en
   `_generate_post_content` y `_generate_carousel_slides_content`. Nuevo
   parámetro `business_url: str = ''` enhebrado a través de `generate()`,
   `generate_carousel()`, `_layered_pipeline()` y las 2 funciones privadas.
2. `core/content_pipeline/generators/text_generator.py`: extiende
   `_SAFETY_QC_PROMPT`, `_validate_caption_safety`, `_SAFETY_FIX_PROMPT`, y el
   trigger en `generate()` — `brand_dna` (que ya incluye `business_url`) ya
   está en scope en todos estos puntos, sin necesidad de un parámetro nuevo.
3. `core/content_pipeline/tasks.py`: agregar `business_url=brand_dna.business_url`
   en los sitios donde ya se arman los kwargs pasados a `_generate_post_media`
   (que ya reenvía `**kwargs` a `image_gen.generate()`/`generate_carousel()`
   automáticamente — no necesita tocarse esa función en sí).

## Componentes

### `image_generator.py` — detección y reemplazo determinístico

Nueva función a nivel de módulo, junto a `_truncate_at_word_boundary`:

```python
_WEB_VISIT_PATTERN = re.compile(
    r'visita(?:nos)?|entra a|nuestr[oa]s?\s+(?:sitio|p[aá]gina)|sitio\s+web|p[aá]gina\s+web|www\.',
    re.IGNORECASE,
)


def _sanitize_web_visit_mention(text: str, business_url: str, fallback: str) -> str:
    """Si no hay business_url y el texto invita a visitar un sitio web, lo
    reemplaza por un fallback seguro — evita prometer un sitio que no existe."""
    if not business_url and _WEB_VISIT_PATTERN.search(text):
        return fallback
    return text
```

Aplicada en `_generate_post_content` (al construir el dict de retorno del
camino exitoso — el camino de fallback ya usa `_FALLBACK['cta']`/etc, que son
inherentemente seguros y no necesitan sanitizarse):

- `cta` → fallback `'Contáctanos hoy'` (ya es el valor de `_FALLBACK['cta']`
  existente).
- `headline` → fallback `self._extract_headline(caption)` (función
  determinística ya existente, deriva el headline del propio caption real).
- `subtitle` → fallback `_truncate_at_word_boundary(caption.strip())` (función
  determinística construida hoy para el fix de reintento ante 429).

Misma lógica en `_generate_carousel_slides_content`, aplicada por slide.

`business_url: str = ''` se agrega como parámetro nuevo a:
`generate()`, `generate_carousel()` (métodos públicos), `_layered_pipeline()`,
`_generate_post_content()`, `_generate_carousel_slides_content()` (privados) —
pasado tal cual de método en método, sin transformación.

### `tasks.py` — pasar `business_url` hacia abajo

En cada sitio donde hoy se construyen los kwargs para `_generate_post_media`
(dentro de `content_generation_task`, `generate_next_week`,
`_generate_missing_image`, y cualquier otro call site existente de esa
función), agregar `business_url=brand_dna.business_url` al diccionario de
kwargs. `_generate_post_media` no se modifica — ya reenvía `**kwargs`
automáticamente a `image_gen.generate()`/`generate_carousel()`.

### `text_generator.py` — extender el QC de seguridad existente

`_SAFETY_QC_PROMPT` gana un tercer flag, `has_website_mention` — detección
pura de si el texto invita a visitar un sitio web, **sin que el prompt decida
si eso es un problema** (esa decisión de negocio vive en código Python, según
si `business_url` existe):

```python
_SAFETY_QC_PROMPT = (
    "Analiza este texto de marketing para redes sociales de forma estricta.\n"
    "Contexto de la marca — tono: {tone}, audiencia: {audience}\n\n"
    "Texto: \"{caption}\"\n\n"
    "Responde UNICAMENTE con este JSON (sin markdown):\n"
    '{{"has_absolute_promise": <bool>, "has_unverifiable_claim": <bool>, "has_website_mention": <bool>, "ok": <bool>}}\n\n'
    "has_absolute_promise: true si usa palabras o frases como 'garantizado', 'garantizamos', "
    "'asegurar', 'aseguramos', 'asegurando', '100%', 'nunca falla', 'sin riesgo', o cualquier "
    "promesa absoluta de resultado.\n"
    "has_unverifiable_claim: true si afirma un resultado medico, financiero, legal o educativo "
    "especifico que no se puede verificar (ej: 'aseguramos un desarrollo optimo', "
    "'garantizamos tu recuperacion', 'triplica tus ingresos').\n"
    "has_website_mention: true si el texto invita a visitar un sitio web, pagina o URL "
    "(ej. 'visita nuestra web', 'entra a nuestro sitio', menciona www. o una URL).\n"
    "ok: true SOLO si has_absolute_promise y has_unverifiable_claim son false. "
    "Ignora has_website_mention para calcular ok — se evalua aparte en el codigo."
)
```

`_validate_caption_safety` gana un parámetro `business_url: str` y computa el
`ok` final combinando el JSON con la regla de negocio:

```python
def _validate_caption_safety(self, caption: str, tone: str, audience: str, business_url: str) -> bool:
    # ... llamada a Gemini sin cambios ...
    if match:
        data = json.loads(match.group())
        ok = bool(data.get('ok', True))
        if not business_url and data.get('has_website_mention'):
            ok = False
        if not ok:
            flags = [k for k in ('has_absolute_promise', 'has_unverifiable_claim', 'has_website_mention') if data.get(k)]
            logger.warning(f"Caption safety QC REJECTED: {', '.join(flags)} | caption={caption[:100]}")
        return ok
    # ... resto sin cambios ...
```

`_SAFETY_FIX_PROMPT` gana la instrucción de no invitar a visitar un sitio web
(instrucción incondicional en el prompt de reescritura — más simple que pasar
qué flag específico falló, y no tiene costo extra ya que la reescritura solo
se dispara cuando algo ya falló):

```python
_SAFETY_FIX_PROMPT = (
    "Reescribe el siguiente post de marketing para que NO haga promesas absolutas ni afirme "
    "resultados de salud, financieros, legales o educativos no verificables, y que NO invite a "
    "visitar un sitio web, pagina o URL. Mantén el mismo mensaje central y longitud aproximada, "
    "pero en tono neutro-positivo, sin palabras como 'garantizado', 'asegurar', 'aseguramos', "
    "'100%', ni frases como 'visita nuestra web'.\n\n"
    "Post original: {caption}\n\n"
    "Tono de la marca: {tone}\n"
    "Responde UNICAMENTE con el texto corregido, sin comillas ni explicaciones."
)
```

El trigger en `generate()` cambia de:

```python
if _is_sensitive_niche(brand_dna):
```

a:

```python
if _is_sensitive_niche(brand_dna) or not brand_dna.business_url:
```

`_ensure_safe_caption` y su llamada a `_validate_caption_safety` reciben
`brand_dna.business_url` (ya está en scope vía `brand_dna`, que ya se pasa
completo a `_ensure_safe_caption`).

## Manejo de errores

Sin cambios en el manejo de errores existente. `_sanitize_web_visit_mention`
es pura función de texto, no lanza. Si `_validate_caption_safety` falla (API
error), ya asume `ok=True` (comportamiento actual sin cambios) — no bloquea
la generación por un error de red.

## Testing

- `image_generator.py`: tests de `_sanitize_web_visit_mention` (con URL no
  cambia nada, sin URL y sin mención no cambia nada, sin URL y con mención
  reemplaza por el fallback). Tests de `_generate_post_content` y
  `_generate_carousel_slides_content`: Gemini devuelve un CTA/headline/subtitle
  con mención de sitio web y `business_url=''` → el campo sale saneado;
  mismo caso con `business_url` no vacío → el campo de Gemini se respeta tal
  cual, sin sanear.
- `text_generator.py`: tests de `_validate_caption_safety` con
  `has_website_mention=True` — `business_url=''` → `ok=False`; `business_url`
  no vacío → `ok` sigue dependiendo solo de los otros 2 flags. Test de
  `generate()`: un negocio SIN nicho sensible y SIN `business_url` dispara el
  loop de auditoría igual (antes solo nichos sensibles la disparaban); un
  negocio sin nicho sensible CON `business_url` no la dispara (comportamiento
  actual sin cambios).
- Sin llamadas reales a Gemini en la suite (mocks siempre). Verificación
  manual post-implementación: generar un calendario de prueba para un negocio
  sin URL y confirmar que ningún post (imagen, carrusel, ni caption) invita a
  visitar un sitio web.
