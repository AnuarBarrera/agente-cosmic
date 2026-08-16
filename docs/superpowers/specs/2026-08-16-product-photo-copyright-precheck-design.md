# Precheck de copyright/marca antes de subir la foto de producto

## Contexto y motivación

El módulo "reel con foto real de producto" (`docs/superpowers/specs/2026-08-16-reel-product-photo-design.md`) y su hermano de posts (`docs/superpowers/specs/2026-08-16-product-photo-post-overlay-design.md`) ya están implementados y probados manualmente. Las 2 corridas reales del reel (documentadas en `hallazgosReel.md`) confirmaron un patrón consistente: nano banana (`gemini-3.1-flash-lite-image`, vía `ImageGenerator._generate_validated_photo_edit`) rechaza con frecuencia fotos de producto reales por `finish_reason=FinishReason.IMAGE_PROHIBITED_CONTENT` — contenido de terceros (logos, personajes con licencia, empaques de marca) visible en la foto que sube el usuario.

Hoy ese rechazo se descubre **después** de gastar la llamada real (y, en el caso del reel, después de haber generado texto/guion completo primero). Esta spec agrega un chequeo previo, ligero, que corre en cuanto el usuario selecciona la foto en el formulario — antes de invertir cualquier llamada cara de generación — para que pueda cambiar la foto a tiempo.

Alcance: **solo detecta riesgo de marca/copyright de terceros** (lo único que tenemos evidencia real de que falla hoy), no otras categorías de política de contenido de Gemini (violencia, contenido sexual, etc.) — esas no se han observado fallar y agregarlas ahora sería especular sin evidencia.

Nota de alcance temporal: la subida de foto de producto hoy solo es visible en el formulario cuando `Plan.allows_sample_generation=True`, activo únicamente en el plan Admin (ver memoria del proyecto, decisión "Admin/prueba por ahora" tomada en el módulo hermano). Este precheck se construye pensando también en cuando ese acceso se abra a planes de pago, pero no depende de que eso ya haya ocurrido.

## Vector de ataque considerado

`analyze_submit` (la vista que recibe la foto hoy) ya está detrás de `@login_required` — no es un endpoint anónimo. El precheck nuevo reutiliza la misma protección. El riesgo real no es manipulación del modelo (la salida está forzada por `response_schema` de Pydantic, igual que el resto del pipeline QC) sino **abuso de costo**: un usuario autenticado probando muchas fotos seguidas para gastar cuota de Vertex/Gemini. Se mitiga con un límite diario por usuario (sección 4).

## 1. Flujo end-to-end (UX)

En `core/brand_dna/templates/brand_dna/new_analysis.html`, al disparar el evento `change` del input `productPhotoInput` (línea ~123), en paralelo/después de la compresión client-side ya existente (`compressImage()`):

1. Se muestra un indicador inline junto al input: "Analizando tu foto...".
2. Se llama a `POST /api/brand-dna/product-photo-precheck/` enviando la imagen ya comprimida (los mismos bytes que luego se usarán en el submit real — no se comprime dos veces).
3. Según la respuesta:
   - `{ok: true}` → el indicador cambia a "✓ Foto lista para usar" (o desaparece).
   - `{ok: false, reason: "Detectamos una marca, logo o personaje con derechos de terceros en esta foto. Prueba con otra foto de tu producto."}` → el indicador cambia a advertencia con el `reason` devuelto, y el botón de submit del formulario se deshabilita hasta que el usuario cambie o quite la foto (**bloqueo duro**).
   - `{ok: true, skipped: true}` (el chequeo no pudo correr, ver sección 4) → el indicador desaparece silenciosamente, el submit permanece habilitado. Es indistinguible en el frontend de un `{ok: true}` normal — incluye el mismo campo `skipped` para que el frontend pueda, si quiere, mostrar un estado neutral en vez de "✓", pero no está obligado a distinguirlo.
4. Cambiar de foto reinicia el estado del indicador y vuelve a disparar el precheck contra la nueva imagen.
5. Si el usuario envía el formulario sin que el precheck haya terminado de correr (petición aún en vuelo), el submit no se bloquea — el precheck es una ayuda, no una validación de servidor que reemplace al rechazo real de nano banana en `analyze_submit`/`generate_sample_task`.

## 2. Backend: endpoint + chequeo

### Vista nueva

`core/brand_dna/views.py`, función `product_photo_precheck_api`:
- `POST`, `@login_required`.
- Ruta nueva en `core/brand_dna/urls.py`: `path('api/brand-dna/product-photo-precheck/', views.product_photo_precheck_api, name='product_photo_precheck_api')` — mismo prefijo `api/brand-dna/` que ya usan `status_api`, `brand_dna_field_action_api`, `regenerate_calendar_api`.
- Recibe la imagen igual que `analyze_submit` hoy: `request.FILES['product_reference_photo']`.
- Reusa `_validate_image_bytes()` y `_safe_extension()` (ya definidas en `views.py`, líneas 41-55) para descartar archivos rotos antes de gastar la llamada a Gemini — si `_validate_image_bytes` falla, responde `{ok: false, reason: 'Esa imagen no se pudo abrir, intenta con otra.'}` directamente, sin llamar al precheck de Gemini.
- **No sube nada a GCS.** Los bytes se validan en memoria y se descartan al terminar el request — esto es intencional: evita que fotos rechazadas ocupen storage, y evita duplicar la lógica de `save_upload` que ya vive en `analyze_submit`.
- Antes de llamar al precheck de Gemini, consulta `can_precheck_photo(request.user)` (sección 4). Si no hay cupo, responde `{ok: true, skipped: true}` sin llamar a Gemini.
- Si hay cupo, instancia `ProductPhotoCopyrightPrecheck().check(photo_bytes, mime_type)` (sección siguiente), registra una fila en `ProductPhotoPrecheckAttempt` **solo si la llamada a Gemini se completó** (éxito o rechazo — no si lanzó excepción), y devuelve el resultado como JSON.

### Clase de chequeo

Nueva clase `ProductPhotoCopyrightPrecheck` en `core/brand_dna/extractors/product_photo_copyright_precheck.py` — mismo directorio y convención de nombre que `ProductPhotoAnalyzer`/`LogoAnalyzer` (`core/brand_dna/extractors/`).

Mismo patrón de cliente que `ProductPhotoAnalyzer` (`core/brand_dna/extractors/product_photo_analyzer.py`):
```python
def _vertex_client():
    return genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT,
        location=settings.GOOGLE_CLOUD_LOCATION_TEXT,
    )
```

A diferencia de `ProductPhotoAnalyzer` (que es extracción descriptiva sin `thinking_config`), este es un veredicto de juicio — mismo tipo de tarea que las funciones QC de `image_generator.py` (`ImageQCSchema`, `ProductPhotoQCSchema`), que sí usan `thinking_config=types.ThinkingConfig(thinking_budget=0)`. Este precheck sigue ese patrón.

Schema Pydantic nuevo:
```python
class CopyrightPrecheckSchema(BaseModel):
    has_recognizable_brand_logo: bool
    has_licensed_character_or_ip: bool
    has_third_party_packaging_design: bool
    ok: bool
```

El campo `ok` del LLM se ignora — el veredicto final se re-deriva en Python, mismo patrón ya usado en `_validate_product_photo_generation` (`image_generator.py`) porque un LLM compone peor la lógica AND/OR al llenar `ok` directamente:
```python
ok = not (
    parsed.has_recognizable_brand_logo
    or parsed.has_licensed_character_or_ip
    or parsed.has_third_party_packaging_design
)
```

Prompt acotado solo a marca/copyright, con distinción explícita entre "el producto real del negocio, aunque tenga texto o diseño propio en el empaque" (aceptable) vs. "logos de terceros, personajes con licencia, empaques de marcas reconocibles" (dispara el flag) — mismo tipo de distinción que ya usa el prompt de `generate_from_product_photo` en `image_generator.py` para decidir qué texto preservar.

Fail-open ante cualquier excepción (mismo patrón que `ProductPhotoAnalyzer`, `_validate_product_photo_generation`, `_validate_background`, todas en el codebase actual): `except Exception` amplio, log del error, retorna `{'ok': True, 'skipped': True}`.

Método público:
```python
def check(self, image_bytes: bytes, mime_type: str) -> dict:
    """Retorna {'ok': bool, 'reason': str} o {'ok': True, 'skipped': True} si falló."""
```

## 3. Frontend

Cambios en `new_analysis.html`:
- Listener nuevo en `productPhotoInput` (`change`), separado del flujo de compresión existente pero corriendo después de que `compressImage()` produce los bytes finales.
- Nuevo elemento de indicador inline (spinner + texto), oculto por defecto.
- Fetch/XHR nuevo a `product_photo_precheck_api` (mismo patrón AJAX que el submit real, con `{% csrf_token %}`).
- Nuevo flag JS que deshabilita el botón de submit cuando `ok === false` (bloqueo duro), y lo re-habilita cuando el usuario cambia de foto o cuando llega una respuesta `ok === true`.

## 4. Límite de uso (mitigación del vector de costo)

### Modelo nuevo

`core/brand_dna/models.py`:
```python
class ProductPhotoPrecheckAttempt(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
```
Con su migración correspondiente.

### Campo nuevo en Plan

`core/tenant_management/models.py`, agregado junto a los demás `max_*` de `Plan`:
```python
max_photo_prechecks_per_day = models.PositiveIntegerField(default=10)
```
Con su migración correspondiente. Valor por defecto `10` — ajustable después sin cambios de código, vía Django Admin (mismo mecanismo que los demás límites de `Plan`).

### Función de límite

`core/brand_dna/rate_limits.py`, mismo estilo que `can_create_calendar`/`can_regenerate`/`can_edit`:
```python
def can_precheck_photo(user) -> tuple[bool, int]:
    plan = get_user_plan(user)
    since = timezone.now() - timedelta(hours=24)
    used = ProductPhotoPrecheckAttempt.objects.filter(user=user, created_at__gte=since).count()
    remaining = max(0, plan.max_photo_prechecks_per_day - used)
    return remaining > 0, remaining
```

### Comportamiento al agotar el cupo

Agotar el cupo **no** es un bloqueo duro — se trata igual que un fallo del chequeo (fail-open): la vista responde `{ok: true, skipped: true}` sin llamar a Gemini. Esto es intencional: quedarse sin cupo de *chequeos preventivos* no significa que la foto sea mala, solo que dejamos de gastar más llamadas de precheck para ese usuario en esa ventana de 24h. nano banana sigue siendo el filtro real y definitivo al final del pipeline, sin cambios.

## 5. Manejo de errores (tabla completa)

| Situación | Respuesta del endpoint | Efecto en el frontend | ¿Registra intento? |
|---|---|---|---|
| Foto sin riesgo detectado | `{ok: true}` | Indicador "✓ lista", submit habilitado | Sí |
| Riesgo de marca/copyright detectado | `{ok: false, reason: "Detectamos una marca, logo o personaje con derechos de terceros en esta foto. Prueba con otra foto de tu producto."}` | Indicador de advertencia, submit deshabilitado hasta cambiar foto | Sí |
| Imagen inválida (no decodifica, `_validate_image_bytes` falla) | `{ok: false, reason: "Esa imagen no se pudo abrir, intenta con otra."}` | Mismo bloqueo — no es fail-open, es un archivo roto que `analyze_submit` rechazaría igual después | No (nunca llegó a llamar a Gemini) |
| Excepción en la llamada a Gemini (red/timeout/error de API) | `{ok: true, skipped: true}` | Indicador desaparece, submit habilitado (fail-open) | No |
| Usuario agotó su cupo diario de prechecks | `{ok: true, skipped: true}` | Igual que arriba — mismo contrato, el frontend no distingue el motivo del skip | No (no se llamó a Gemini) |
| Usuario no autenticado | Redirect 302 a login (comportamiento estándar de `@login_required`, sin cambios) | — | — |

## 6. Testing

- **`ProductPhotoCopyrightPrecheck`** (`core/brand_dna/extractors/tests/`, o junto a los tests existentes de `ProductPhotoAnalyzer`): mockeando `genai.Client`.
  - `ok=True` cuando ningún flag está activo.
  - `ok=False` cuando cualquiera de los 3 flags está en `True` (un test por flag).
  - El veredicto se re-deriva en Python: un `ok=True` del LLM se ignora si algún flag individual es `True` (test explícito de esta inconsistencia, mismo patrón que ya existe para `ProductPhotoQCSchema`).
  - Excepción del cliente → `{'ok': True, 'skipped': True}`.
- **`product_photo_precheck_api`** (`core/brand_dna/tests/test_views.py` o archivo equivalente):
  - Sin login → redirect.
  - Imagen inválida → `{ok: false, reason: ...}`, sin llamar al precheck de Gemini (mock no invocado), sin fila nueva en `ProductPhotoPrecheckAttempt`.
  - Precheck exitoso (mock) → respuesta correcta y una fila nueva en `ProductPhotoPrecheckAttempt`.
  - Precheck lanza excepción (mock) → `{ok: true, skipped: true}`, sin fila nueva.
  - Cupo agotado (mockeando `can_precheck_photo` para devolver `(False, 0)`) → `{ok: true, skipped: true}`, sin llamar al mock de Gemini, sin fila nueva.
- **`can_precheck_photo`**: test de ventana de 24h — un `ProductPhotoPrecheckAttempt` con `created_at` de hace 25 horas no cuenta contra el límite.
- **Frontend**: este repo no tiene suite de tests JS (confirmar al implementar); verificación manual en navegador, mismo patrón usado para validar el módulo de reel con foto real.

## Fuera de alcance (diferido, no de esta spec)

- **Cache por hash de imagen** para evitar re-llamar a Gemini si el usuario sube la misma foto dos veces (ej. tras cambiar y volver a la foto original). Se consideró informalmente pero no es parte de esta spec — el límite diario de la sección 4 ya cubre el caso de abuso; el cache sería una optimización de costo adicional, no un requisito de seguridad. Si se vuelve necesario, el patrón natural sería reusar la conexión Redis que ya usa `core/shared/rate_limiter.py` (`django_rq.get_connection('default')`), con clave por hash SHA-256 de los bytes y TTL corto.
- **Otras categorías de contenido prohibido** (violencia, contenido sexual, etc.) más allá de marca/copyright — sin evidencia real de que ocurran hoy; agregarlas sería expandir el prompt y el schema especulativamente.
- **Precheck en la regeneración** (`regenerate_post_image_task`, que reusa la misma foto ya subida) — esta spec cubre la subida inicial (`analyze_submit`); la foto de regeneración es la misma que ya pasó por este precheck en la subida original, así que no hay una segunda oportunidad de "elegir otra foto" en ese flujo.
