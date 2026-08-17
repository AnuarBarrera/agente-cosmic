# Pool de fotos reales de producto para el calendario completo

## Contexto y motivación

Hoy la subida de foto real de producto (`product_reference_photo`) solo existe para la "muestra individual" de prospección (`generate_sample_task`, campo único `AnalysisJob.product_reference_image_path`), y solo está visible en el formulario para el plan Admin (`Plan.allows_sample_generation=True`, verificado en la DB real: `Premium/Tester/Free/User = False`, `Admin = True`). El calendario completo de 7 días (`content_generation_task` → `backfill_image_task` → `_generate_missing_image`) **nunca** usa esa foto — cada día genera su imagen/reel desde cero con IA, sin ninguna foto real de por medio.

Este spec abre esa capacidad al calendario completo, para los 3 planes gratuitos/de prueba (User, Tester, Admin) y para el plan pagado, con más límite en pagado.

### Lección del mecanismo anterior (eliminado 2026-07-15)

Existió antes un mecanismo de "hasta 7 fotos, una por día", eliminado por baja calidad real de resultados (tecnología Imagen 3 BGSWAP, "2 de 7 resultados aceptables"), un caso de watermark filtrado que hizo desistir a un usuario real, y fricción en el formulario de alta (ver `docs/superpowers/specs/2026-07-14-remove-product-photo-upload-design.md`). La tecnología actual (nano banana / `gemini-3.1-flash-lite-image` con `thinking_config`, más el precheck de copyright y el QC ya construidos) ya fue validada extensamente esta sesión con fotos reales de producción — el problema de calidad no debería repetirse. El diseño de este spec evita además el patrón "1 foto = 1 día" que generaba la fricción original: aquí es un **pool** sin asociación manual a días específicos, el sistema decide dónde aplicar cada foto.

## 1. Modelo de datos y límites

`AnalysisJob.product_reference_image_path` (`CharField` único) se reemplaza por `product_reference_image_paths` (`JSONField(default=list)`), mismo patrón que `ContentPost.image_urls`. Cada elemento es una ruta GCS, igual que hoy.

Nuevo campo en `Plan`: `max_product_reference_photos` (`PositiveIntegerField`, default `7`). Valores de datos ajustados vía Django Admin: `User`/`Tester`/`Admin` (gratis) quedan en `7`; el plan `User` pagado sube a `14` (duplicado, ya que genera más posts que la semana gratis).

`Plan.max_photo_prechecks_per_day` (ya existente, default `10`) se ajusta en los planes pagados a un valor con margen sobre `14` (ej. `20`) para que subir el pool completo de una sentada no agote el cupo diario de precheck.

## 2. Asignación de fotos a días/formatos

El sistema decide automáticamente qué foto usa cada día — el usuario no asocia fotos a días manualmente. Rotación circular sobre el pool, en el orden en que se subieron:

- **Post individual (single):** usa 1 foto del pool (siguiente en la rotación).
- **Carrusel (día 3):** usa hasta 3 fotos del pool (siguientes 3 en la rotación), cada una editada individualmente en su propia slide — el carrusel pasa a tener 3 slides (una por foto) en vez de las 4 fijas de hoy (`num_slides=4`), ya que cada slide ahora corresponde 1:1 a una foto real. Si el pool tiene menos de 3 fotos, la rotación repite fotos (sección 2) y el carrusel igual sale con 3 slides, algunas repitiendo la misma foto editada de forma distinta (prompt/dirección creativa distinta por slide).
- **Reel:** usa hasta 3 fotos del pool, repartidas entre los 6 shots (2 shots por foto) — en vez de editar la misma foto 6 veces como hoy. Si el pool tiene menos de 3 fotos, la rotación repite fotos (sección 2) y los 6 shots igual se generan, algunos repitiendo la misma foto editada con un prompt de shot distinto.

Si el pool tiene menos fotos que las que un día necesita, la rotación da la vuelta y reusa desde el principio — nunca bloquea un día por falta de fotos. Si el pool está vacío (nadie subió foto), todo el calendario funciona exactamente igual que hoy, sin ningún cambio de comportamiento.

Alcance por formato: aplica a los 3 formatos (single, carrusel, reel) — no solo a reel/carrusel.

## 3. Reuso del código existente

El building block real ya existe y es compartido: `ImageGenerator._generate_validated_photo_edit(prompt, photo_part, max_qc_retries, aspect_ratio)` — "una foto entra, una imagen editada con QC sale". El trabajo nuevo es reorganizar cuántas veces se llama y con qué foto de la rotación, no reinventar validación/QC/reintentos:

- **Single:** `ImageGenerator.generate_from_product_photo` ya llama a este building block una vez — sin cambios de fondo, solo recibe la foto que le tocó en la rotación.
- **Carrusel:** nueva función `ImageGenerator.generate_carousel_from_product_photos(photos: list[bytes], ...)` que llama al building block una vez por foto asignada (hasta 3), cada una con su propio prompt de dirección creativa/slide — en vez del fondo único que hoy se reusa en todas las slides.
- **Reel:** `ReelGenerator._generate_video_clips_from_photo` cambia su parámetro de `photo_bytes: bytes` (una foto) a `photos: list[bytes]` (hasta 3) — la distribución "2 shots por foto entre los 6 shots" reemplaza el "editar la misma foto 6 veces" de hoy. El shot héroe sigue siendo la primera foto de la rotación asignada a ese día.

Nueva función compartida en `core/content_pipeline/tasks.py`: `_next_reference_photos(job, count) -> list[bytes]`, implementa la rotación circular de la sección 2. La reutilizan los 3 call-sites (single/carrusel/reel) del calendario completo.

## 4. UI de subida + precheck de múltiples fotos

En `new_analysis.html`, el input `productPhotoInput` gana el atributo `multiple`, con límite dinámico según el plan (`Plan.max_product_reference_photos`, pasado al template igual que `allows_sample_generation` hoy). Se muestra un contador visible ("3/7 fotos") y una miniatura por foto ya aceptada, con botón de quitar individual.

**Límite como barrera real, no solo informativa:** al llegar al límite del plan, el input deja de aceptar nuevas fotos y muestra un aviso claro ("Llegaste al límite de 7 fotos para tu plan"). El backend igual trunca al límite del plan como red de seguridad (nunca confiar solo en el cliente), pero en el flujo normal esto no debería activarse porque el frontend ya lo previene.

El precheck de copyright ya construido (`product_photo_precheck_api`) se reusa sin cambios de backend — el JS lo llama una vez por archivo en cuanto se agrega, con su propio indicador inline por foto (reusa el componente ya construido, repetido N veces). Una foto rechazada se quita automáticamente de la selección (no cuenta contra el límite ni bloquea las demás), y se muestra un mensaje visible y persistente explicando el motivo — ej. "Quitamos 1 foto porque no cumple con las políticas de contenido — sube otra si quieres reemplazarla" — para que el usuario no descubra la ausencia recién al ver el contenido generado.

Envío del formulario: `product_reference_photo` pasa de campo único a múltiples archivos (`product_reference_photo[]`), comprimidos igual que hoy antes de enviarse.

## 5. Manejo de errores

| Situación | Comportamiento |
|---|---|
| Foto rechazada por precheck | Se quita de la selección, mensaje visible y persistente explicando el motivo |
| Pool vacío (nadie subió foto) | Todo el calendario sigue igual que hoy, sin ningún cambio |
| Pool más chico que lo que un día necesita | Rotación circular reusa desde el principio, nunca bloquea |
| Falla la edición de una foto específica dentro del pool (nano banana la rechaza en generación, no en precheck) | Se omite esa foto para ese shot/slide, igual que ya hace el reel hoy (shots fallidos se omiten sin abortar el post completo) |
| Usuario intenta subir más fotos que el límite de su plan | Frontend bloquea la selección adicional con aviso visible al llegar al límite; backend trunca como red de seguridad si de todos modos llega más |

## 6. Testing

- `_next_reference_photos`: rotación circular normal, pool vacío, pool menor a lo pedido (da la vuelta), pool exactamente del tamaño pedido.
- `ImageGenerator.generate_carousel_from_product_photos`: N fotos → N slides editadas individualmente, QC por slide, fallback si una foto falla.
- `ReelGenerator._generate_video_clips_from_photo` con `photos: list[bytes]`: distribución 2 shots por foto, fallback cuando una foto de la lista falla su edición.
- `_generate_missing_image`/`backfill_image_task`: flujo E2E para los 3 formatos (single/carrusel/reel) con pool disponible y con pool vacío (comportamiento sin cambios).
- Frontend: sin suite de tests JS en este repo (confirmado en specs anteriores) — verificación manual en navegador, mismo patrón ya usado para los módulos de foto real anteriores.

## Fuera de alcance (diferido, no de esta spec)

- Asociación manual de fotos a días específicos por parte del usuario — el sistema decide automáticamente vía rotación circular, no hay UI para que el usuario elija "esta foto va en el día 3".
- Regeneración de un post individual reutilizando una foto específica del pool más allá de la rotación ya asignada — la regeneración (`regenerate_post_image_task`) sigue su comportamiento actual, no forma parte de este spec.
- Cache por hash de imagen para el precheck — ya diferido en el spec del precheck de copyright, sigue diferido aquí.
