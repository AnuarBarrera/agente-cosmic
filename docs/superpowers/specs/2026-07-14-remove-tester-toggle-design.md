# Reversión del toggle de tester para reels/carrusel — Diseño

## Contexto

El toggle de tester (`docs/superpowers/specs/2026-07-14-tester-feature-toggles-design.md`,
implementado y desplegado hoy mismo) se construyó como freno de emergencia
mientras el pipeline de reels/carrusel tenía bugs reales: timeout de Veo sin
límite, jobs huérfanos por `--force-recreate`, fallback de subtítulo roto, y el
prompt de carrusel atado a "prueba social". Todos esos bugs se corrigieron hoy
mismo, en la misma sesión, y Anuar probó el pipeline con resultados
significativamente mejores (reel "casi tipo comercial", contenido menos genérico).

## Decisión de producto (Anuar, explícita)

Con la calidad ya no ameritando gatear por feature, la diferenciación entre
tipos de usuario vuelve a ser por **límite de uso** — el sistema de `Plan` ya
existente (`Plan.max_calendars_per_week`, `max_post_regenerations`,
`max_post_edits`, con planes User/Tester/Admin ya definidos en
`core/tenant_management/models.py`) — en vez de por activar/desactivar
features individualmente. Es como se había pensado originalmente antes de
agregar el toggle.

- Los testers se siguen asignando por código de invitación
  (`InvitationCode`, `COSMIC-XXXXXX`) — eso NO cambia.
- Reels (día 1) y carrusel (día 3) vuelven a estar activados para **todos**
  los usuarios por defecto, sin control individual — mismo comportamiento
  que existía antes de la Task 3 de la sesión de hoy.
- **Reversión completa**: se eliminan también los campos del modelo
  (`reels_enabled`/`carousel_enabled`) y su migración — no dejar código ni
  schema sin uso. Si se quiere reactivar en el futuro, el spec/plan de hoy
  ya queda documentado como referencia.

## Arquitectura

Reversión en 3 pasos, en **orden inverso** al de construcción original
(UI → lógica de negocio → modelo), para que en cada paso intermedio el
código siga corriendo sin referencias rotas a algo ya eliminado:

1. UI: vista, URL, sección del dashboard.
2. Lógica de negocio: función de override en `tasks.py` y sus 2 llamadas.
3. Modelo: campos en `User` + migración de reversión.

## Componentes

### 1. UI

- `core/brand_dna/auth_views.py`: eliminar la función `update_tester_preferences_view`
  completa (líneas ~541-550, el bloque `@login_required` seguido de la función).
- `core/brand_dna/urls.py`: eliminar la línea
  `path('dashboard/tester-preferences/', auth_views.update_tester_preferences_view, name='update_tester_preferences'),`.
- `core/brand_dna/templates/brand_dna/dashboard.html`: eliminar el bloque completo
  `{% if user.groups.all.0.name == 'tester' or user.groups.all.0.name == 'admin' %}`
  ... `{% endif %}` que contiene la sección "Funciones beta" (agregado hoy,
  inmediatamente después del bloque del código de invitación).
- `core/brand_dna/tests/test_auth_views.py`: eliminar la clase completa
  `TestUpdateTesterPreferencesView` (4 tests).

### 2. Lógica de negocio

- `core/content_pipeline/tasks.py`: eliminar la función completa
  `_disable_reel_and_carousel_for_tester_preference` (junto a
  `_disable_carousel_if_full_product_week`, antes de `content_generation_task`).
  Eliminar sus 2 llamadas: `_disable_reel_and_carousel_for_tester_preference(posts_data, job.user)`
  dentro de `content_generation_task`, y
  `_disable_reel_and_carousel_for_tester_preference(posts_data, brand_dna.job.user)`
  dentro de `generate_next_week`.
- `core/content_pipeline/tests/test_tasks.py`: eliminar los 5 tests
  `test_disable_reel_and_carousel_for_tester_preference_*` y el helper
  `_create_tester` que solo ellos usan.

### 3. Modelo

- `core/tenant_management/models.py`: eliminar las 2 líneas
  `reels_enabled = models.BooleanField(default=True)` y
  `carousel_enabled = models.BooleanField(default=True)` de la clase `User`.
- Nueva migración `core/tenant_management/migrations/0019_remove_user_reels_carousel_toggles.py`:

```python
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tenant_management', '0018_user_reels_carousel_toggles'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='user',
            name='reels_enabled',
        ),
        migrations.RemoveField(
            model_name='user',
            name='carousel_enabled',
        ),
    ]
```

- `core/tenant_management/tests/test_user_feature_toggles.py`: eliminar el
  archivo completo — solo probaba los campos que se están quitando.

## Manejo de errores

No aplica — es remoción de código, no hay nuevo manejo de errores.

## Testing

No hay ciclo TDD rojo-verde tradicional (es una eliminación, no una
funcionalidad nueva). La verificación es: después de cada uno de los 3 pasos,
correr la suite completa del/los archivo(s) de test afectados y confirmar que
**todo pasa** — si algo sigue referenciando código eliminado, el archivo de
test ni siquiera colectará (`ImportError`/`AttributeError`), lo cual es la
señal de que falta limpiar algo. Verificación final: correr
`python manage.py makemigrations --check --dry-run` para confirmar que la
migración de reversión deja el modelo exactamente sincronizado.

Verificación manual post-implementación: entrar al dashboard con una cuenta
tester real y confirmar que la sección "Funciones beta" ya no aparece;
generar un calendario nuevo y confirmar que el día 1 es reel y el día 3 es
carrusel sin necesidad de ningún toggle.
