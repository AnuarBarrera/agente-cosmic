# Reversión del toggle de tester para reels/carrusel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminar por completo el toggle de tester para reels/carrusel construido hoy — reels (día 1) y carrusel (día 3) vuelven a estar activados para todos los usuarios por defecto, sin control individual. La diferenciación entre tipos de usuario vuelve a ser por límite de uso (`Plan.max_calendars_per_week`, etc., ya existente).

**Architecture:** Reversión en orden inverso al de construcción (UI → lógica de negocio → modelo), para que en cada paso intermedio el código siga corriendo sin referencias rotas. No es TDD rojo-verde tradicional — es eliminación de código, verificada corriendo la suite completa del archivo afectado tras cada remoción.

**Tech Stack:** Django, pytest.

## Global Constraints

- Reversión **completa** — no dejar campos de modelo, código, ni tests sin uso. Si se quiere reactivar en el futuro, el spec de hoy (`docs/superpowers/specs/2026-07-14-tester-feature-toggles-design.md`) queda documentado como referencia.
- Los testers se siguen asignando por código de invitación (`InvitationCode`) — eso NO se toca en este plan.
- Orden de ejecución obligatorio: Task 1 (UI) → Task 2 (lógica) → Task 3 (modelo) — el modelo se elimina último porque las tareas 1 y 2 todavía referencian sus campos.
- No tocar ningún test ni código agregado en otros planes de hoy (ej. `test_content_generation_passes_business_url_to_image_gen`, del plan de seguridad de URL) — solo lo que pertenece específicamente al toggle de tester.

---

### Task 1: Quitar la UI (vista, URL, sección del dashboard)

**Files:**
- Modify: `core/brand_dna/auth_views.py`
- Modify: `core/brand_dna/urls.py`
- Modify: `core/brand_dna/templates/brand_dna/dashboard.html`
- Test: `core/brand_dna/tests/test_auth_views.py`

**Interfaces:**
- Consumes: nada de otra tarea.
- Produces: nada — es remoción pura. La ruta `dashboard/tester-preferences/` y el nombre de URL `update_tester_preferences` dejan de existir tras esta tarea.

- [ ] **Step 1: Eliminar la clase de tests**

En `core/brand_dna/tests/test_auth_views.py`, eliminar el bloque completo desde
la línea en blanco antes de `@pytest.mark.django_db` hasta el final del archivo
— es decir, eliminar exactamente esto (la clase `TestUpdateTesterPreferencesView`
completa, que es lo último en el archivo):

```python
@pytest.mark.django_db
class TestUpdateTesterPreferencesView:
    def test_tester_can_update_preferences(self, client, setup_plans_and_groups):
        user = User.objects.create_user(
            email='pref@test.com', password=_TEST_PWD, username='pref@test.com'
        )
        _make_tenant(user)
        user.groups.add(Group.objects.get(name='tester'))
        client.force_login(user)

        resp = client.post('/dashboard/tester-preferences/', {
            'reels_enabled': 'on', 'carousel_enabled': 'on',
        })
        assert resp.status_code == 302
        user.refresh_from_db()
        assert user.reels_enabled is True
        assert user.carousel_enabled is True

        resp = client.post('/dashboard/tester-preferences/', {})
        assert resp.status_code == 302
        user.refresh_from_db()
        assert user.reels_enabled is False
        assert user.carousel_enabled is False

    def test_non_tester_cannot_update_preferences(self, client, setup_plans_and_groups):
        user = User.objects.create_user(
            email='normal2@test.com', password=_TEST_PWD, username='normal2@test.com'
        )
        _make_tenant(user)
        user.groups.add(Group.objects.get(name='user'))
        user.reels_enabled = False
        user.carousel_enabled = False
        user.save(update_fields=['reels_enabled', 'carousel_enabled'])
        client.force_login(user)

        resp = client.post('/dashboard/tester-preferences/', {
            'reels_enabled': 'on', 'carousel_enabled': 'on',
        })
        assert resp.status_code == 302
        user.refresh_from_db()
        assert user.reels_enabled is False
        assert user.carousel_enabled is False

    def test_requires_login(self, client, setup_plans_and_groups):
        resp = client.post('/dashboard/tester-preferences/', {'reels_enabled': 'on'})
        assert resp.status_code == 302
        assert '/auth/login/' in resp.url

    def test_get_redirects_without_changes(self, client, setup_plans_and_groups):
        user = User.objects.create_user(
            email='getuser@test.com', password=_TEST_PWD, username='getuser@test.com'
        )
        _make_tenant(user)
        user.groups.add(Group.objects.get(name='tester'))
        client.force_login(user)
        resp = client.get('/dashboard/tester-preferences/')
        assert resp.status_code == 302
```

El archivo debe terminar justo después de `test_apply_code_requires_login` (la
última línea que queda es `assert '/auth/login/' in resp.url` de esa función,
seguida de una sola línea en blanco final).

- [ ] **Step 2: Eliminar la vista**

En `core/brand_dna/auth_views.py`, eliminar este bloque completo (entre
`apply_code_view` y `deactivate_account_view`):

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

Deja exactamente una línea en blanco doble entre `apply_code_view` y
`deactivate_account_view` (el mismo espaciado que separa cualquier otro par de
vistas en el archivo).

- [ ] **Step 3: Eliminar la URL**

En `core/brand_dna/urls.py`, eliminar esta línea:

```python
    path('dashboard/tester-preferences/', auth_views.update_tester_preferences_view, name='update_tester_preferences'),
```

- [ ] **Step 4: Eliminar la sección del dashboard**

En `core/brand_dna/templates/brand_dna/dashboard.html`, este bloque:

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

Eliminarlo por completo (incluida la línea en blanco que lo sigue), dejando el
bloque del código de invitación (`{% if not user.groups.all.0.name == 'tester'...`)
seguido directamente por `{% if jobs %}`.

- [ ] **Step 5: Correr la suite de `test_auth_views.py` y verificar que pasa**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_auth_views.py -v`
Expected: todos los tests pasan (los de `TestRegisterView`, `TestVerifyEmailView`,
`TestNotifyAdmin`, `TestApplyCodeView` no se tocaron y no deben verse afectados;
la clase `TestUpdateTesterPreferencesView` ya no existe, así que no debe aparecer
en la lista de tests recolectados).

- [ ] **Step 6: Commit**

```bash
git add core/brand_dna/auth_views.py core/brand_dna/urls.py core/brand_dna/templates/brand_dna/dashboard.html core/brand_dna/tests/test_auth_views.py
git commit -m "revert(tester-toggles): quitar vista, URL y UI del toggle de reels/carrusel"
```

---

### Task 2: Quitar la lógica de negocio en `tasks.py`

**Files:**
- Modify: `core/content_pipeline/tasks.py`
- Test: `core/content_pipeline/tests/test_tasks.py`

**Interfaces:**
- Consumes: nada de Task 1.
- Produces: nada — remoción pura. `_disable_reel_and_carousel_for_tester_preference`
  deja de existir tras esta tarea.

- [ ] **Step 1: Eliminar los tests y el helper**

En `core/content_pipeline/tests/test_tasks.py`, eliminar este bloque completo
(el helper `_create_tester` y los 5 tests que le siguen — está ubicado entre
`test_generate_next_week_resets_flag_even_on_failure` y el `@override_settings`
que precede a `test_content_generation_passes_business_url_to_image_gen`, que
NO se toca):

```python
def _create_tester(reels_enabled=True, carousel_enabled=True):
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group
    import secrets
    User = get_user_model()
    email = f'tester-{secrets.token_hex(4)}@test.com'
    pwd = f"T3st-{secrets.token_urlsafe(10)}!"
    user = User.objects.create_user(email=email, password=pwd, username=email)
    user.reels_enabled = reels_enabled
    user.carousel_enabled = carousel_enabled
    user.save(update_fields=['reels_enabled', 'carousel_enabled'])
    group, _ = Group.objects.get_or_create(name='tester')
    user.groups.add(group)
    return user


def test_disable_reel_and_carousel_for_tester_preference_noop_with_none_user():
    from core.content_pipeline.tasks import _disable_reel_and_carousel_for_tester_preference
    posts_data = [{'format': 'reel'}, {'format': 'carousel'}, {'format': 'single'}]
    _disable_reel_and_carousel_for_tester_preference(posts_data, None)
    assert posts_data[0]['format'] == 'reel'
    assert posts_data[1]['format'] == 'carousel'


def test_disable_reel_and_carousel_for_tester_preference_noop_for_non_tester():
    from core.content_pipeline.tasks import _disable_reel_and_carousel_for_tester_preference
    from django.contrib.auth import get_user_model
    import secrets
    User = get_user_model()
    email = f'normal-{secrets.token_hex(4)}@test.com'
    pwd = f"T3st-{secrets.token_urlsafe(10)}!"
    user = User.objects.create_user(email=email, password=pwd, username=email)
    user.reels_enabled = False
    user.carousel_enabled = False
    user.save(update_fields=['reels_enabled', 'carousel_enabled'])
    posts_data = [{'format': 'reel'}, {'format': 'carousel'}]
    _disable_reel_and_carousel_for_tester_preference(posts_data, user)
    assert posts_data[0]['format'] == 'reel'
    assert posts_data[1]['format'] == 'carousel'


def test_disable_reel_and_carousel_for_tester_preference_disables_reel_when_off():
    from core.content_pipeline.tasks import _disable_reel_and_carousel_for_tester_preference
    user = _create_tester(reels_enabled=False, carousel_enabled=True)
    posts_data = [{'format': 'reel'}, {'format': 'carousel'}, {'format': 'single'}]
    _disable_reel_and_carousel_for_tester_preference(posts_data, user)
    assert posts_data[0]['format'] == 'single'
    assert posts_data[1]['format'] == 'carousel'
    assert posts_data[2]['format'] == 'single'


def test_disable_reel_and_carousel_for_tester_preference_disables_carousel_when_off():
    from core.content_pipeline.tasks import _disable_reel_and_carousel_for_tester_preference
    user = _create_tester(reels_enabled=True, carousel_enabled=False)
    posts_data = [{'format': 'reel'}, {'format': 'carousel'}]
    _disable_reel_and_carousel_for_tester_preference(posts_data, user)
    assert posts_data[0]['format'] == 'reel'
    assert posts_data[1]['format'] == 'single'


def test_disable_reel_and_carousel_for_tester_preference_noop_when_both_enabled():
    from core.content_pipeline.tasks import _disable_reel_and_carousel_for_tester_preference
    user = _create_tester(reels_enabled=True, carousel_enabled=True)
    posts_data = [{'format': 'reel'}, {'format': 'carousel'}]
    _disable_reel_and_carousel_for_tester_preference(posts_data, user)
    assert posts_data[0]['format'] == 'reel'
    assert posts_data[1]['format'] == 'carousel'
```

Deja exactamente una línea en blanco doble entre
`test_generate_next_week_resets_flag_even_on_failure` y el `@override_settings`
de `test_content_generation_passes_business_url_to_image_gen`.

- [ ] **Step 2: Eliminar la función y sus 2 llamadas**

En `core/content_pipeline/tasks.py`, eliminar este bloque completo (entre
`_disable_carousel_if_full_product_week` y `content_generation_task`):

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

Y eliminar esta línea, dentro de `content_generation_task` (justo después de
`_disable_carousel_if_full_product_week(posts_data, product_images_bytes)` y
antes de `if _product_image_for_day(1, product_images_bytes) is not None:`):

```python
        _disable_reel_and_carousel_for_tester_preference(posts_data, job.user)
```

Y esta otra línea, dentro de `generate_next_week` (mismo patrón, después de su
propia llamada a `_disable_carousel_if_full_product_week`):

```python
        _disable_reel_and_carousel_for_tester_preference(posts_data, brand_dna.job.user)
```

- [ ] **Step 3: Correr la suite de `test_tasks.py` y verificar que pasa**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_tasks.py -v`
Expected: todos los tests pasan — incluido `test_content_generation_passes_business_url_to_image_gen`,
que no se tocó. Ningún test debe mencionar `_disable_reel_and_carousel_for_tester_preference`.

- [ ] **Step 4: Commit**

```bash
git add core/content_pipeline/tasks.py core/content_pipeline/tests/test_tasks.py
git commit -m "revert(tester-toggles): quitar override de formato por preferencia de tester"
```

---

### Task 3: Quitar los campos del modelo `User`

**Files:**
- Modify: `core/tenant_management/models.py`
- Create: `core/tenant_management/migrations/0019_remove_user_reels_carousel_toggles.py`
- Delete: `core/tenant_management/tests/test_user_feature_toggles.py`

**Interfaces:**
- Consumes: nada — Tasks 1 y 2 ya eliminaron todo lo que usaba estos campos.
- Produces: nada — `User.reels_enabled`/`User.carousel_enabled` dejan de existir
  tras esta tarea.

- [ ] **Step 1: Borrar el archivo de tests**

Eliminar el archivo completo `core/tenant_management/tests/test_user_feature_toggles.py`
(solo probaba los 2 campos que se están quitando):

```bash
rm core/tenant_management/tests/test_user_feature_toggles.py
```

- [ ] **Step 2: Eliminar los campos del modelo**

En `core/tenant_management/models.py`, el bloque actual de la clase `User` es:

```python
class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(TenantModel, on_delete=models.CASCADE, null=True, blank=True)
    email = models.EmailField(_('email address'), unique=True)
    display_name = models.CharField(max_length=255, blank=True, null=True) # New field
    email_verified = models.BooleanField(default=False)
    deactivated_at = models.DateTimeField(null=True, blank=True)
    reels_enabled = models.BooleanField(default=True)
    carousel_enabled = models.BooleanField(default=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = [] # No other fields are required for authentication
    
    objects = UserManager()
```

Reemplazarlo por (se quitan las 2 líneas de `reels_enabled`/`carousel_enabled`):

```python
class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    tenant = models.ForeignKey(TenantModel, on_delete=models.CASCADE, null=True, blank=True)
    email = models.EmailField(_('email address'), unique=True)
    display_name = models.CharField(max_length=255, blank=True, null=True) # New field
    email_verified = models.BooleanField(default=False)
    deactivated_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = [] # No other fields are required for authentication
    
    objects = UserManager()
```

- [ ] **Step 3: Crear la migración de reversión**

Crear `core/tenant_management/migrations/0019_remove_user_reels_carousel_toggles.py`
con este contenido exacto:

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

- [ ] **Step 4: Aplicar la migración y verificar sincronización**

Run: `docker compose exec -T backend python manage.py migrate tenant_management`
Expected: `Applying tenant_management.0019_remove_user_reels_carousel_toggles... OK`

Run: `docker compose exec -T backend python manage.py makemigrations --check --dry-run`
Expected: exit code 0, sin output de migraciones faltantes.

- [ ] **Step 5: Correr las suites afectadas y verificar que todo pasa**

Run: `docker compose exec -T backend python -m pytest core/tenant_management/ core/content_pipeline/tests/test_tasks.py core/brand_dna/tests/test_auth_views.py -v`
Expected: todos los tests pasan. Ningún test debe referenciar `reels_enabled`
ni `carousel_enabled` — si alguno lo hace, el archivo no colectará
(`AttributeError`) y hay que confirmar que las Tasks 1 y 2 se completaron
correctamente antes de continuar.

- [ ] **Step 6: Commit**

```bash
git add core/tenant_management/models.py core/tenant_management/migrations/0019_remove_user_reels_carousel_toggles.py
git rm core/tenant_management/tests/test_user_feature_toggles.py
git commit -m "revert(tester-toggles): quitar reels_enabled/carousel_enabled de User"
```

---

## Verificación manual post-implementación (no automatizable)

Después de que las 3 tareas estén commiteadas y los contenedores recreados
(`docker compose up -d --force-recreate --no-deps backend rqworker` — ver
memoria `feedback_gunicorn_restart.md`), entrar al dashboard con una cuenta
tester real y confirmar que la sección "Funciones beta" ya no aparece; generar
un calendario nuevo y confirmar que el día 1 es reel y el día 3 es carrusel
sin ningún control de por medio.
