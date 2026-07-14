# Eliminación de la carga de fotos de producto — Diseño

## Contexto

Agente Cosmic permite subir hasta 7 fotos de producto al crear un análisis de marca y al regenerar cada semana. El pipeline las usa para: (a) BGSWAP de Imagen 3 (mantener el producto exacto, reemplazar el fondo), (b) forzar formato `single` cuando hay foto en el día correspondiente (día 1 bloquea reel, semana completa de 7 fotos bloquea carrusel).

En uso real la calidad fue baja e inconsistente (ej. "el perro rebelde": 2 de 7 resultados aceptables) y un caso de watermark filtrado en las imágenes generadas hizo que un usuario ("Jorge") desistiera de la feature por completo. Un bug de detección de rotación ya se corrigió, pero el problema de fondo persiste: la feature promete "mejores resultados" sin entregarlos de forma confiable, y agrega fricción al formulario de alta.

Decisión de Anuar: eliminar la feature por completo (formulario, almacenamiento, flujo BGSWAP) hasta que exista una forma confiable de insertar el producto real dentro del post generado. La diferenciación entre tipos de usuario ya no depende de esto — ver `docs/superpowers/specs/2026-07-14-remove-tester-toggle-design.md` (trabajo relacionado, ya completado, revierte el mecanismo de toggles por feature en favor de límites de uso).

Esta es la segunda de dos iniciativas de simplificación del mismo día; son subsistemas independientes y se manejan en specs/planes separados.

## Alcance

**Se elimina:**
- El campo de subida de fotos de producto en el formulario de alta (`new_analysis.html`) y su compresión client-side.
- La sección de "imágenes de producto para tu próxima semana" en la vista de revisión semanal (`calendar_review.html`): radios reutilizar/subir nuevas, galería de pool, input de archivo, y el JS que las maneja.
- El manejo de subida de archivos de producto en las vistas de `brand_dna/views.py`: `analyze_submit` (alta inicial), `calendar_feedback_api` + `_update_active_product_images` (regen semanal), y las lecturas de `job.product_image_path` en `post_action_api` (regen de un post) y `regenerate_calendar_api` (regen día 1 tras editar Brand DNA).
- Toda la lógica de mapeo/branching por foto en `content_pipeline/tasks.py`: `_load_product_images`, `_product_image_for_day`, `_disable_carousel_if_full_product_week`, y el bloque que fuerza `single` cuando hay foto en día 1 — en las dos funciones que generan el calendario (`content_generation_task`, `generate_next_week`).
- El parámetro `product_image_bytes` de las 5 firmas en `image_generator.py` (`generate`, `generate_carousel`, `_layered_pipeline`, `_generate_post_content`, `_generate_carousel_slides_content`) y los 3 métodos que solo existen para servirlo: `_generate_product_scene`, `_analyze_product_style`, `_bgswap_product`.
- Los campos de BD `AnalysisJob.product_image_path` / `product_image_paths` y `ContentCalendar.active_product_images`, con sus migraciones `RemoveField`.
- Los 2 bucles de limpieza de fotos de producto en `cleanup_deactivated_images.py` (el logo se queda).
- La mención a "foto de producto" en `privacy.html`.

**No se toca:**
- El upload de logo de marca (`logo_file_path`) — feature separada, se queda igual.
- El branching Art Director `mode: product/lifestyle` en `image_generator.py` (líneas ~437-512) — decide el *estilo* del fondo generado por IA desde cero, no depende de que el usuario suba nada.
- Las etiquetas de métricas Prometheus (`img_type='bgswap'` en `metrics.py`) — quedan definidas aunque dejen de reportarse; tocar la cardinalidad de labels es riesgo innecesario sobre dashboards existentes.
- Los archivos ya subidos por usuarios en GCS/almacenamiento local — quedan huérfanos (sin referencia en BD) pero no se borran activamente. No es una acción urgente ni se justifica el riesgo de una operación destructiva sobre datos de usuarios reales.

## Comportamiento resultante

Después del cambio, todo el contenido generado (posts individuales, carruseles, reels) se genera siempre desde cero con el flujo de fondo generado por IA (Art Director `product`/`lifestyle` mode), igual que hoy sucede para usuarios que nunca subieron fotos. Un día 1 ya no puede forzarse a `single` por tener foto — usa la misma lógica de asignación de formato que cualquier otro día. Una semana ya no puede desactivar el carrusel por tener 7 fotos.

Usuarios que ya tienen `product_image_path`/`product_image_paths`/`active_product_images` guardados en BD pierden esos campos en la migración — sus posts ya generados (imágenes ya renderizadas y subidas a GCS, referenciadas por `ContentPost.image_url`/`image_urls`) no se ven afectados, porque esos campos no se leen en tiempo de visualización, solo en tiempo de generación.

## Descomposición en tareas

Mismo patrón usado en la reversión del toggle de tester (capas: UI → lógica de generación → núcleo del generador de imágenes → modelo/BD), para que cada tarea sea desplegable y verificable de forma independiente:

1. **UI + intake de subida**: templates (`new_analysis.html`, `calendar_review.html`) y las partes de `views.py` que reciben/guardan archivos nuevos (`analyze_submit`, `calendar_feedback_api`, `_update_active_product_images`, el context var `product_pool` en `calendar_review_view`).
2. **Lógica de generación en tiempo de contenido**: `tasks.py` completo (las 3 funciones helper + sus call sites) + las lecturas de `job.product_image_path` en `post_action_api`/`regenerate_calendar_api`.
3. **Núcleo de `image_generator.py`**: quitar el parámetro `product_image_bytes` de las 5 firmas + borrar los 3 métodos BGSWAP + simplificar el branching `if product_image_bytes: ... else: ...` en `_layered_pipeline` a solo la rama sin foto.
4. **Modelo + migración + limpieza + copy legal**: los 2 campos de `AnalysisJob`, el campo de `ContentCalendar`, las 2 migraciones `RemoveField`, `cleanup_deactivated_images.py`, `privacy.html`.

El orden importa: la Tarea 1 deja de aceptar fotos nuevas pero el resto del pipeline (tareas 2-4) sigue funcionando con lo que ya había en BD hasta que se ejecuten; cada tarea deja el sistema en un estado consistente y con tests pasando, igual que en el plan de reversión de toggles.

## Testing

Sin ciclo TDD rojo-verde — es eliminación pura, igual que la reversión de toggles. La verificación es: tests existentes en `core/brand_dna/tests/test_views.py`, `core/content_pipeline/tests/test_tasks.py`, `core/content_pipeline/tests/test_image_generator.py`, `core/content_pipeline/tests/test_models.py` deben seguir pasando después de quitar cualquier test que dependiera específicamente de fotos de producto, y `makemigrations --check --dry-run` debe confirmar sincronía tras la Tarea 4.

## Riesgos / decisiones explícitas

- **Archivos huérfanos en storage**: aceptado, no se limpian como parte de este trabajo (decisión explícita del usuario — no es una acción destructiva urgente).
- **Etiquetas de métricas sin tocar**: decisión de diseño para no arriesgar dashboards; el label `bgswap` simplemente deja de incrementarse.
- **Logo intacto**: confirmado como fuera de alcance — es una feature de marca separada, no de producto.
