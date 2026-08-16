# Cerrar el post con foto real de producto (overlay de producción) — Diseño

## Contexto

El módulo 1 (`docs/superpowers/specs/2026-08-15-product-photo-image-module-design.md`,
implementado y en `main`) validó la pieza difícil: nano banana
(`gemini-3.1-flash-lite-image`) puede editar/componer una foto real de
producto directamente, siempre que la petición incluya `thinking_config`
(`thinking_budget=-1`, AUTOMATIC) — confirmado con pruebas reales en Vertex
AI Studio y en producción el 2026-08-16 (root cause de los rechazos
`finish_reason=OTHER` que se veían antes).

El spec original de ese módulo decidió, a propósito, que la imagen generada
con foto real **no** llevara overlay de texto — el prompt a nano banana dice
literalmente "Do not add text of any kind either — no new headline, no CTA,
no captions, no labels." Resultado: un post con foto real se ve distinto a
un post normal (sin foto), que sí lleva headline/subtitle/CTA/tag
renderizados encima vía `_layered_pipeline` → `_generate_post_content` →
`_render_html_template`.

Este documento cubre cerrar esa diferencia: que un post con foto real de
producto se vea igual que un post normal de producción, reusando el pipeline
de overlay existente en vez de construir uno nuevo.

## Alcance

**Dentro de este cambio:**
1. `ImageGenerator.generate_from_product_photo` (primera generación) aplica
   el mismo overlay de contenido que un post normal.
2. `ImageGenerator.regenerate_with_reference` (botón "Regenera la imagen")
   también queda con overlay, editando siempre el **fondo limpio** (la foto
   ya editada por nano banana, sin overlay) y no la imagen final compuesta.
3. Campo nuevo en `ContentPost` para guardar el fondo limpio por separado de
   la imagen final — necesario para que la regeneración pueda editarlo sin
   pasarle a nano banana un texto horneado que no sabe que es "nuestro".

**Fuera de alcance (explícitamente diferido):**
- Reel con foto real de producto y pipeline completo de 7 días — specs y
  planes separados, después de este módulo (decisión ya tomada con Anuar).
- Activar `_validate_final_image` (QC final del post compuesto) — hoy es
  código muerto, no lo llama ningún camino de producción (ni normal ni con
  foto). Este cambio no lo activa para ninguno de los dos; si se decide
  activarlo alguna vez, debe beneficiar a ambos caminos por igual, en un
  cambio aparte.

## Decisiones de diseño

Resueltas con Anuar durante el brainstorm (2026-08-16):

- **Alcance de la regeneración:** este cambio cubre tanto la primera
  generación como la regeneración en el mismo pase (no se difiere).
- **QC final del post compuesto:** no se activa — se confirmó que no corre
  hoy en NINGÚN camino de producción; activarlo solo para el camino con foto
  real crearía una inconsistencia nueva sin justificación.
- **Si el overlay falla** (Playwright, plantilla rota) después de que nano
  banana ya entregó un fondo válido: se degrada a la foto sin overlay (el
  comportamiento de hoy) en vez de perder el post — nunca se descarta el
  trabajo de nano banana por un fallo de renderizado.

## Arquitectura

### `_layered_pipeline` gana un parámetro opcional

Hoy `_layered_pipeline` siempre genera su propio fondo con
`_generate_background`. Se le agrega `background_bytes: bytes = None`: si se
lo pasan, se salta `_generate_background` y usa ese fondo directo; si no
(el camino normal, sin cambios), genera el suyo como siempre. El resto de la
función (`_generate_post_content` → `_render_html_template`) no cambia.

```python
def _layered_pipeline(self, caption: str, colors: list[str], tone: str,
                       keywords: list[str] = None, description: str = '',
                       audience: str = '', max_qc_retries: int = 2,
                       font_seed: str = '', business_url: str = '',
                       background_bytes: bytes = None) -> bytes:
    if background_bytes is None:
        background_bytes = self._generate_background(
            caption, colors, tone, keywords or [], description,
            audience=audience, max_qc_retries=max_qc_retries,
        )
    kw_str = ', '.join((keywords or [])[:4])
    brand_ctx = f"{description[:150]}. Tono: {tone}. Palabras clave: {kw_str}." if description else f"Tono: {tone}."
    content = self._generate_post_content(caption, brand_context=brand_ctx, business_url=business_url)
    return self._render_html_template(background_bytes, content, colors, svg_overlay='', font_seed=font_seed)
```

`_render_html_template` ya recorta a cuadrado y ya elige la zona con menos
interferencia visual vía `_choose_template_for_image` (analiza la imagen y
evita tapar "sujeto principal, producto, rostros, logos, detalles") — no
necesita ningún cambio para aceptar un fondo que es una foto real editada en
vez de un fondo generado desde cero.

### `generate_from_product_photo`

Mismo bucle de reintentos de hoy (nano banana + `_validate_product_photo_generation`)
para conseguir un fondo válido (`last_bytes`). Al conseguirlo, en vez de
subirlo directo:

1. Sube `last_bytes` a storage → `background_url`.
2. Deriva `font_seed` igual que `generate()` (`filename.rsplit('-day', 1)[0]`).
3. Intenta `self._layered_pipeline(caption, colors, tone, keywords, description,
   business_url=business_url, font_seed=font_seed, background_bytes=last_bytes)`.
   - Si tiene éxito: sube el resultado → `final_url`.
   - Si falla (excepción): `final_url = background_url` (degradado, sin
     overlay — se loggea como warning, no como error).
4. Devuelve `(background_url, final_url)`.

Gana 3 parámetros nuevos: `description: str = ''`, `keywords: list[str] = None`,
`business_url: str = ''` — todos ya disponibles en `brand_dna` desde el
caller (`generate_sample_task`), no requieren nada nuevo del usuario.

En fallo total (nano banana nunca entrega fondo usable), devuelve `('', '')`
— igual que hoy devuelve `''`.

### `regenerate_with_reference`

Mismo cambio de forma. Además, el parámetro `current_image_bytes` pasa a ser
`current_background_bytes` — recibe el **fondo limpio** (no la imagen final
compuesta), para que nano banana edite sobre la foto real sin overlay
horneado encima. Gana los mismos parámetros nuevos que `generate_from_product_photo`
(`caption`, `colors`, `tone`, `description`, `keywords`, `business_url`) más
los que ya tenía (`feedback`, `vision_context`, `filename`, `max_qc_retries`).

Devuelve `(background_url, final_url)` igual que `generate_from_product_photo`.
En fallo total, `('', '')` — el caller conserva la imagen/fondo anterior sin
cambios (mismo patrón ya existente).

### Modelo: `ContentPost.product_photo_background_url`

```python
product_photo_background_url = models.URLField(max_length=1000, blank=True, default='')
```

Vacío para todo post que no venga del camino de foto real (igual que
`image_urls` hoy solo se llena para carruseles). Nueva migración.

### Callers

- **`generate_sample_task`** (`core/content_pipeline/tasks.py`): pasa
  `description=brand_dna.description`, `keywords=brand_dna.keywords`,
  `business_url=brand_dna.business_url` a `generate_from_product_photo`;
  desempaqueta la tupla y guarda `product_photo_background_url` además de
  `image_url` en el `ContentPost.objects.create(...)`.
- **`regenerate_post_image_task`** (mismo archivo): en vez de descargar
  `post.image_url` con `read_upload_from_public_url`, descarga
  `post.product_photo_background_url`. Pasa `caption=post.caption` (ya
  regenerado por la vista antes de encolar la tarea), más
  `brand_dna.primary_colors`, `brand_dna.tone`, `brand_dna.description`,
  `brand_dna.keywords`, `brand_dna.business_url`. Desempaqueta la tupla y
  actualiza `product_photo_background_url` + `image_url` juntos (o ninguno
  de los dos si `background_url` viene vacío — conserva lo anterior, mismo
  patrón que ya existe para `regenerating`).

No hace falta tocar el gate de ruteo en `post_action_api` (`views.py`) — ya
identifica correctamente qué posts usan este camino, y cambiarlo reabriría
la superficie que costó 2 rondas de fix en el módulo 1.

## Manejo de errores

| Caso | Resultado |
|---|---|
| Nano banana nunca entrega fondo usable (agota reintentos) | `('', '')` — igual que hoy |
| Fondo válido, overlay falla (Playwright/plantilla) | `(background_url, background_url)` — se usa la foto sin overlay |
| Fondo válido, overlay exitoso | `(background_url, final_url)` — ambas URLs reales, distintas |

## Testing

Reutiliza los tests ya existentes de `_generate_post_content`,
`_render_html_template`, `_choose_template_for_image` (sin cambios en su
lógica). Los tests de `generate_from_product_photo`/`regenerate_with_reference`
se actualizan para desempaquetar tupla en vez de string, más casos nuevos:
overlay exitoso (verifica ambas URLs y que el contenido del overlay usa
caption/tono reales), overlay que falla (verifica degradado a fondo limpio,
`final_url == background_url`), y `_layered_pipeline` con
`background_bytes` explícito (verifica que NO llama a `_generate_background`).
Migración nueva con su test de `makemigrations --check`.
