STATUS: DONE

### Archivos creados/modificados:
- Modificado: `core/brand_dna/views.py`
- Modificado: `core/brand_dna/tests/test_views.py`

### Tests ejecutados:
- Comando: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_views.py -v`
- Resultado resumido: `43 passed, 12 warnings in 22.46s`
- Lista de tests clave ejecutados y aprobados:
  - `test_download_post_image_returns_mp4_for_reel` PASSED (Nuevo test: descarga de MP4 para reels)
  - `test_regenerate_action_blocked_for_reel_posts` PASSED (Nuevo test: bloqueo de regeneración manual para reels)
  - `test_regenerate_action_uses_carousel_when_post_format_is_carousel` PASSED (Corregido un error heredado donde faltaban argumentos posicionales al llamar a `_generate_post_media`)
  - `test_update_active_product_images_new_uploads` PASSED (Corregido un error heredado de almacenamiento en disco en ambiente local de pruebas)

### Concerns:
Ninguno. Las correcciones adicionales realizadas a los tests y lógica heredados garantizan la solidez y estabilidad de la suite de pruebas del backend.
