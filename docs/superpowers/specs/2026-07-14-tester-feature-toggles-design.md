# Toggles de tester para carrusel/reels — Diseño

## Contexto

Reels y carrusel son features en beta, con varios bugs reales encontrados y
corregidos en producción hoy mismo (timeout de Veo sin límite, job huérfano por
`--force-recreate`, fallback de subtítulo roto por falta de reintento ante `429`).
Anuar espera una ola de registros nuevos pronto y quiere limitar el blast radius
mientras estas features se estabilizan.

Hoy, el día 1 del calendario semanal usa `format='reel'` fijo (salvo que el usuario
suba una foto de producto para ese día) y el día 3 usa `format='carousel'` fijo
(salvo que suba 7 fotos de producto) — sin ningún control de usuario. Los testers
se asignan por código de invitación (`InvitationCode`, `COSMIC-XXXXXX`,
`core/tenant_management/models.py`) — ese mecanismo **no cambia**, sigue siendo la
única forma de convertirse en tester.

## Decisiones de producto (Anuar, explícitas)

- Los testers deben poder **activar y desactivar reels y carrusel de forma
  independiente**, desde el producto, sin que Anuar los asigne caso por caso.
- Alcance: **solo generaciones futuras**. El toggle decide qué formato usa el día
  1/3 la próxima vez que se genere contenido (próxima semana vía
  `generate_next_week`, o un nuevo análisis) — los posts ya generados esta semana
  no cambian retroactivamente. Convertir posts ya existentes es explícitamente
  fuera de alcance de este spec.
- Default para un tester nuevo: **activado** (mismo comportamiento que hoy) —
  recomendación de Claude sin objeción de Anuar.
- El estado del toggle vive en el **usuario** (no en `BrandDNA`) — decisión
  explícita de Anuar. Si un tester analiza varios negocios, la preferencia aplica
  a todos por igual.
- Afecta solo a usuarios en los grupos `tester` o `admin`. Usuarios normales no ven
  ningún control — siguen con el comportamiento actual sin cambios.

## Arquitectura

Cambio de datos + UI acotado, siguiendo patrones ya existentes en el proyecto:

1. **Modelo**: 2 campos nuevos en `tenant_management.User` (el modelo de usuario
   custom del proyecto, `AUTH_USER_MODEL = 'tenant_management.User'`).
2. **Lógica de negocio**: una función de post-procesado en `tasks.py`, siguiendo
   exactamente el mismo patrón que `_disable_carousel_if_full_product_week`
   (ya existente, ya se llama en los mismos 2 puntos) — downgradea `format` a
   `single` para el día correspondiente si el usuario desactivó esa feature.
3. **UI**: nueva sección en `dashboard.html`, reutilizando el bloque
   `{% if %}` de grupo tester/admin que ya existe en ese archivo.
4. **Vista**: nueva vista siguiendo exactamente el patrón de `apply_code_view`
   (`@login_required`, solo POST, `redirect('dashboard')`).

Verificado: `AnalysisJob.user` es `null=True, blank=True` (flujo anónimo existe) —
la función de post-procesado debe tolerar `user=None` sin fallar, dejando el
comportamiento actual sin cambios en ese caso (igual que para usuarios no-tester).

## Componentes

### Migración — `core/tenant_management/migrations/0018_user_reels_carousel_toggles.py`

Agrega a `User` (`core/tenant_management/models.py`):

```python
reels_enabled = models.BooleanField(default=True)
carousel_enabled = models.BooleanField(default=True)
```

### `core/content_pipeline/tasks.py`

Nueva función, ubicada junto a `_disable_carousel_if_full_product_week`
(línea ~74):

```python
def _disable_reel_and_carousel_for_tester_preference(posts_data: list[dict], user) -> None:
    """Si el usuario es tester/admin y desactivo reels o carrusel en su perfil,
    esos dias caen a 'single' — mismo patron que _disable_carousel_if_full_product_week."""
    if user is None:
        return
    if not user.groups.filter(name__in=['tester', 'admin']).exists():
        return
    for post in posts_data:
        fmt = post.get('format')
        if fmt == ContentPost.FORMAT_REEL and not user.reels_enabled:
            post['format'] = ContentPost.FORMAT_SINGLE
        elif fmt == ContentPost.FORMAT_CAROUSEL and not user.carousel_enabled:
            post['format'] = ContentPost.FORMAT_SINGLE
```

Se llama inmediatamente después de la llamada existente a
`_disable_carousel_if_full_product_week(...)`, en los 2 mismos puntos donde esa
función ya se invoca:

- `content_generation_task` (línea ~109): `_disable_reel_and_carousel_for_tester_preference(posts_data, job.user)`
  — `job` ya está en scope (`job = AnalysisJob.objects.get(id=job_id)`).
- `generate_next_week` (línea ~250): `_disable_reel_and_carousel_for_tester_preference(posts_data, brand_dna.job.user)`
  — `brand_dna` ya está en scope; `brand_dna.job` es la `AnalysisJob` relacionada.

### `core/brand_dna/auth_views.py`

Nueva vista, junto a `apply_code_view` (línea ~515), mismo patrón exacto:

```python
@login_required
def update_tester_preferences_view(request):
    if request.method != 'POST':
        return redirect('dashboard')
    if not request.user.groups.filter(name__in=['tester', 'admin']).exists():
        return redirect('dashboard')
    request.user.reels_enabled = 'reels_enabled' in request.POST
    request.user.carousel_enabled = 'carousel_enabled' in request.POST
    request.user.save(update_fields=['reels_enabled', 'carousel_enabled'])
    return redirect('dashboard')
```

Nota: un checkbox HTML solo aparece en `request.POST` cuando está marcado — la
expresión `'reels_enabled' in request.POST` captura correctamente el estado
on/off (desmarcado = clave ausente = `False`), sin necesitar un input oculto
adicional.

### `core/brand_dna/urls.py`

Nueva ruta, junto a `apply_code` (línea ~24):

```python
path('dashboard/tester-preferences/', auth_views.update_tester_preferences_view, name='update_tester_preferences'),
```

### `core/brand_dna/templates/brand_dna/dashboard.html`

Nueva sección, inmediatamente después del bloque existente del código de
invitación (que cierra con `{% endif %}` antes de `{% if jobs %}`), mismo estilo
visual (fondo `#1a1a2e`, borde `#2a2a4a`, acento `#e94560`):

```html
{% if user.groups.all.0.name == 'tester' or user.groups.all.0.name == 'admin' %}
<div style="background:#1a1a2e;border:1px solid #2a2a4a;border-radius:12px;padding:20px 24px;margin-bottom:24px;">
  <div style="font-weight:600;font-size:0.95rem;margin-bottom:12px;">Funciones beta</div>
  <form method="POST" action="{% url 'update_tester_preferences' %}" style="display:flex;gap:24px;flex-wrap:wrap;align-items:center;">
    {% csrf_token %}
    <label style="display:flex;align-items:center;gap:8px;font-size:0.88rem;cursor:pointer;">
      <input type="checkbox" name="reels_enabled" {% if user.reels_enabled %}checked{% endif %}>
      Reels (día 1)
    </label>
    <label style="display:flex;align-items:center;gap:8px;font-size:0.88rem;cursor:pointer;">
      <input type="checkbox" name="carousel_enabled" {% if user.carousel_enabled %}checked{% endif %}>
      Carrusel (día 3)
    </label>
    <button type="submit" style="padding:10px 20px;background:#e94560;color:#fff;border:none;border-radius:8px;font-weight:600;cursor:pointer;white-space:nowrap;">Guardar</button>
  </form>
  <div style="font-size:0.78rem;color:#aaa;margin-top:10px;">Afecta solo a los próximos calendarios que generes — los posts ya creados esta semana no cambian.</div>
</div>
{% endif %}
```

## Manejo de errores

Ninguno nuevo. `_disable_reel_and_carousel_for_tester_preference` no lanza — si
`user` es `None` o no pertenece a los grupos correctos, retorna sin modificar
`posts_data`, igual que el patrón ya establecido. La vista no valida más allá del
método HTTP y la pertenencia a grupo — un usuario normal que intente hacer POST
directo a la URL es redirigido sin cambios, sin error 403 explícito (mismo nivel
de protección que `apply_code_view`, que tampoco distingue "no autorizado" de
"solicitud inválida").

## Testing

- `test_tasks.py` (o el archivo de tests de `tasks.py` que ya exista): tests para
  `_disable_reel_and_carousel_for_tester_preference` — usuario `None` (no cambia
  nada), usuario no-tester (no cambia nada aunque tenga los campos en `False`),
  usuario tester con `reels_enabled=False` (día con `format='reel'` baja a
  `single`, otros formatos sin tocar), usuario tester con `carousel_enabled=False`
  (análogo), usuario tester con ambos activados (no cambia nada).
- Tests para `update_tester_preferences_view`: POST válido de un tester actualiza
  ambos campos correctamente (incluido el caso de desmarcar — el campo debe
  quedar en `False`); GET redirige sin cambios; POST de un usuario normal
  (sin grupo tester/admin) no modifica sus campos y redirige.
- Sin llamadas reales a APIs externas en estos tests — es lógica de negocio y
  vista pura, sin dependencias de Vertex AI/Gemini.
- Verificación manual post-implementación: Anuar activa/desactiva ambos toggles
  en su propia cuenta tester, genera una semana nueva (`generate_next_week` o un
  análisis nuevo), y confirma que el día 1/3 respeta lo configurado.
