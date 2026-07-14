# Toggles de tester para reels/carrusel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que usuarios de los grupos `tester`/`admin` activen y desactiven reels (día 1) y carrusel (día 3) de forma independiente, desde el dashboard, sin que Anuar los asigne caso por caso.

**Architecture:** 2 campos booleanos nuevos en el modelo `User` (`tenant_management`) + una función de post-procesado en `tasks.py` que sigue exactamente el mismo patrón que `_disable_carousel_if_full_product_week` ya existente + una vista/URL/sección de UI en el dashboard, siguiendo el patrón exacto de `apply_code_view`.

**Tech Stack:** Django 5.2.3, pytest + `pytest.mark.django_db`, `django.test.Client`.

## Global Constraints

- Alcance: **solo generaciones futuras** — no convierte posts ya generados esta semana. No tocar `views.py:430-432` (bloqueo de regeneración de reels), eso queda fuera de este plan.
- Default para un usuario nuevo: `reels_enabled=True`, `carousel_enabled=True` (mismo comportamiento que hoy).
- El estado vive en el **usuario** (`tenant_management.User`), no en `BrandDNA`.
- Afecta solo a usuarios en los grupos `tester` o `admin`. Usuarios normales (grupo `user`) no tienen forma de cambiar esto — la vista debe ignorar silenciosamente sus intentos.
- `AnalysisJob.user` puede ser `None` (flujo anónimo) — toda la lógica nueva debe tolerar esto sin lanzar excepciones.
- Los testers se siguen asignando por código de invitación (`InvitationCode`) — ese mecanismo no cambia, no es parte de este plan.

---

### Task 1: Campos `reels_enabled`/`carousel_enabled` en `User`

**Files:**
- Modify: `core/tenant_management/models.py:133-144` (clase `User`)
- Create: `core/tenant_management/migrations/0018_user_reels_carousel_toggles.py`
- Test: `core/tenant_management/tests/test_user_feature_toggles.py`

**Interfaces:**
- Produces: `User.reels_enabled: bool` (default `True`), `User.carousel_enabled: bool` (default `True`) — consumidos por Task 2 (lógica de negocio) y Task 3 (vista/UI).

- [ ] **Step 1: Escribir el test que falla**

Crear `core/tenant_management/tests/test_user_feature_toggles.py`:

```python
import secrets
import pytest
from django.contrib.auth import get_user_model

User = get_user_model()

pytestmark = pytest.mark.django_db


def test_new_user_has_reels_and_carousel_enabled_by_default():
    pwd = f"T3st-{secrets.token_urlsafe(10)}!"
    email = f'newuser-{secrets.token_hex(4)}@test.com'
    user = User.objects.create_user(email=email, password=pwd, username=email)
    assert user.reels_enabled is True
    assert user.carousel_enabled is True
```

- [ ] **Step 2: Correr el test y verificar que falla**

Run: `docker compose exec -T backend python -m pytest core/tenant_management/tests/test_user_feature_toggles.py -v`
Expected: FAIL — `AttributeError: 'User' object has no attribute 'reels_enabled'`

- [ ] **Step 3: Agregar los campos al modelo**

En `core/tenant_management/models.py`, el bloque actual de la clase `User` es:

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

Reemplazarlo por (se agregan `reels_enabled` y `carousel_enabled` después de `deactivated_at`):

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

- [ ] **Step 4: Crear la migración**

Crear `core/tenant_management/migrations/0018_user_reels_carousel_toggles.py` con este contenido exacto:

```python
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenant_management', '0017_alter_invitationcode_created_by'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='reels_enabled',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='user',
            name='carousel_enabled',
            field=models.BooleanField(default=True),
        ),
    ]
```

- [ ] **Step 5: Aplicar la migración**

Run: `docker compose exec -T backend python manage.py migrate tenant_management`
Expected: `Applying tenant_management.0018_user_reels_carousel_toggles... OK`

Verificar que no queden cambios de modelo sin migrar:

Run: `docker compose exec -T backend python manage.py makemigrations --check --dry-run`
Expected: exit code 0, sin output de migraciones faltantes (si imprime algo sobre `tenant_management`, la migración de Step 4 no coincide con el modelo — revisar).

- [ ] **Step 6: Correr el test y verificar que pasa**

Run: `docker compose exec -T backend python -m pytest core/tenant_management/tests/test_user_feature_toggles.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add core/tenant_management/models.py core/tenant_management/migrations/0018_user_reels_carousel_toggles.py core/tenant_management/tests/test_user_feature_toggles.py
git commit -m "feat(tester-toggles): agregar reels_enabled/carousel_enabled a User"
```

---

### Task 2: Lógica de negocio — respetar el toggle al generar contenido

**Files:**
- Modify: `core/content_pipeline/tasks.py` (nueva función + 2 puntos de llamada)
- Test: `core/content_pipeline/tests/test_tasks.py`

**Interfaces:**
- Consumes: `User.reels_enabled`, `User.carousel_enabled` (Task 1).
- Produces: `_disable_reel_and_carousel_for_tester_preference(posts_data: list[dict], user) -> None` — muta `posts_data` in-place, no se usa fuera de `tasks.py`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `core/content_pipeline/tests/test_tasks.py`:

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

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_tasks.py -k disable_reel_and_carousel -v`
Expected: FAIL — `ImportError: cannot import name '_disable_reel_and_carousel_for_tester_preference'`

- [ ] **Step 3: Implementar la función**

En `core/content_pipeline/tasks.py`, agregar inmediatamente después de la función
`_disable_carousel_if_full_product_week` (la que termina con
`post['format'] = ContentPost.FORMAT_SINGLE`, justo antes de
`def content_generation_task(job_id: str) -> None:`):

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

- [ ] **Step 4: Conectar en `content_generation_task`**

En `core/content_pipeline/tasks.py`, dentro de `content_generation_task`, este bloque:

```python
        # Cargar imágenes de producto (hasta 7, una por día)
        product_images_bytes = _load_product_images(calendar.active_product_images)
        _disable_carousel_if_full_product_week(posts_data, product_images_bytes)
        if _product_image_for_day(1, product_images_bytes) is not None:
            posts_data[0]['format'] = ContentPost.FORMAT_SINGLE
```

Cambia a:

```python
        # Cargar imágenes de producto (hasta 7, una por día)
        product_images_bytes = _load_product_images(calendar.active_product_images)
        _disable_carousel_if_full_product_week(posts_data, product_images_bytes)
        _disable_reel_and_carousel_for_tester_preference(posts_data, job.user)
        if _product_image_for_day(1, product_images_bytes) is not None:
            posts_data[0]['format'] = ContentPost.FORMAT_SINGLE
```

(`job` ya está en scope al inicio de la función: `job = AnalysisJob.objects.get(id=job_id)`.)

- [ ] **Step 5: Conectar en `generate_next_week`**

En la misma función `generate_next_week`, este bloque:

```python
        base_day = (week_number - 1) * 7
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        product_images_bytes = _load_product_images(calendar.active_product_images)
        _disable_carousel_if_full_product_week(posts_data, product_images_bytes)
        if _product_image_for_day(1, product_images_bytes) is not None:
            posts_data[0]['format'] = ContentPost.FORMAT_SINGLE
```

Cambia a:

```python
        base_day = (week_number - 1) * 7
        image_gen = ImageGenerator(bucket_name=settings.GOOGLE_CLOUD_STORAGE_BUCKET)
        product_images_bytes = _load_product_images(calendar.active_product_images)
        _disable_carousel_if_full_product_week(posts_data, product_images_bytes)
        _disable_reel_and_carousel_for_tester_preference(posts_data, brand_dna.job.user)
        if _product_image_for_day(1, product_images_bytes) is not None:
            posts_data[0]['format'] = ContentPost.FORMAT_SINGLE
```

(`brand_dna` ya está en scope al inicio de la función: `brand_dna = calendar.brand_dna`; `brand_dna.job` es la `AnalysisJob` relacionada.)

- [ ] **Step 6: Correr los tests y verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_tasks.py -k disable_reel_and_carousel -v`
Expected: PASS (5 passed)

- [ ] **Step 7: Correr toda la suite de `test_tasks.py`**

Run: `docker compose exec -T backend python -m pytest core/content_pipeline/tests/test_tasks.py -v`
Expected: todos los tests pasan — los tests existentes de `content_generation_task`/`generate_next_week` no pasan ningún `user` explícito en sus fixtures (`job_with_dna` crea el `AnalysisJob` sin `user`), así que `job.user`/`brand_dna.job.user` es `None` en esos casos y `_disable_reel_and_carousel_for_tester_preference` no hace nada — comportamiento sin cambios.

- [ ] **Step 8: Commit**

```bash
git add core/content_pipeline/tasks.py core/content_pipeline/tests/test_tasks.py
git commit -m "feat(tester-toggles): respetar reels_enabled/carousel_enabled del usuario al generar contenido"
```

---

### Task 3: Vista, URL y UI del dashboard

**Files:**
- Modify: `core/brand_dna/auth_views.py` (nueva vista)
- Modify: `core/brand_dna/urls.py` (nueva ruta)
- Modify: `core/brand_dna/templates/brand_dna/dashboard.html` (nueva sección)
- Test: `core/brand_dna/tests/test_auth_views.py`

**Interfaces:**
- Consumes: `User.reels_enabled`, `User.carousel_enabled` (Task 1). No depende de Task 2 en tiempo de ejecución (son independientes; Task 2 lee los campos que esta vista escribe, pero ninguna llama a la otra).
- Produces: URL `dashboard/tester-preferences/` (name=`update_tester_preferences`), vista `update_tester_preferences_view`.

- [ ] **Step 1: Escribir los tests que fallan**

Agregar al final de `core/brand_dna/tests/test_auth_views.py`:

```python
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

- [ ] **Step 2: Correr los tests y verificar que fallan**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_auth_views.py::TestUpdateTesterPreferencesView -v`
Expected: FAIL — `django.urls.exceptions.NoReverseMatch` o 404 (la URL todavía no existe)

- [ ] **Step 3: Agregar la vista**

En `core/brand_dna/auth_views.py`, agregar inmediatamente después de `apply_code_view`
(que termina con `return redirect('dashboard')`, antes de
`@login_required` / `def deactivate_account_view(request):`):

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

- [ ] **Step 4: Agregar la URL**

En `core/brand_dna/urls.py`, este bloque:

```python
    path('dashboard/', auth_views.dashboard_view, name='dashboard'),
    path('dashboard/apply-code/', auth_views.apply_code_view, name='apply_code'),
    path('dashboard/delete-account/', auth_views.deactivate_account_view, name='deactivate_account'),
```

Cambia a:

```python
    path('dashboard/', auth_views.dashboard_view, name='dashboard'),
    path('dashboard/apply-code/', auth_views.apply_code_view, name='apply_code'),
    path('dashboard/tester-preferences/', auth_views.update_tester_preferences_view, name='update_tester_preferences'),
    path('dashboard/delete-account/', auth_views.deactivate_account_view, name='deactivate_account'),
```

- [ ] **Step 5: Correr los tests y verificar que pasan**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_auth_views.py::TestUpdateTesterPreferencesView -v`
Expected: PASS (4 passed)

- [ ] **Step 6: Correr toda la suite de `test_auth_views.py`**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_auth_views.py -v`
Expected: todos los tests pasan.

- [ ] **Step 7: Agregar la sección al dashboard**

En `core/brand_dna/templates/brand_dna/dashboard.html`, este bloque (el que muestra
el formulario de código de invitación, entre su `{% endif %}` de cierre y el
`{% if jobs %}` siguiente):

```html
    {% if not user.groups.all.0.name == 'tester' and not user.groups.all.0.name == 'admin' %}
    <div style="background:#1a1a2e;border:1px solid #2a2a4a;border-radius:12px;padding:20px 24px;margin-bottom:24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap;">
      <div style="flex:1;min-width:200px;">
        <div style="font-weight:600;font-size:0.95rem;margin-bottom:4px;">¿Tienes un código de invitación?</div>
        <div style="font-size:0.8rem;color:#aaa;">Ingresa tu código para obtener acceso ampliado como tester.</div>
      </div>
      <form method="POST" action="{% url 'apply_code' %}" style="display:flex;gap:8px;align-items:center;">
        {% csrf_token %}
        <input type="text" name="code" placeholder="COSMIC-XXXXXX" required
               style="padding:10px 14px;border-radius:8px;border:1px solid #333;background:#0d0d1a;color:#f0f0f0;font-size:0.9rem;width:170px;text-transform:uppercase;">
        <button type="submit" style="padding:10px 20px;background:#e94560;color:#fff;border:none;border-radius:8px;font-weight:600;cursor:pointer;white-space:nowrap;">Aplicar</button>
      </form>
    </div>
    {% endif %}

    {% if jobs %}
```

Cambia a (se agrega la nueva sección justo después del `{% endif %}` del código de
invitación, antes de `{% if jobs %}`):

```html
    {% if not user.groups.all.0.name == 'tester' and not user.groups.all.0.name == 'admin' %}
    <div style="background:#1a1a2e;border:1px solid #2a2a4a;border-radius:12px;padding:20px 24px;margin-bottom:24px;display:flex;align-items:center;gap:16px;flex-wrap:wrap;">
      <div style="flex:1;min-width:200px;">
        <div style="font-weight:600;font-size:0.95rem;margin-bottom:4px;">¿Tienes un código de invitación?</div>
        <div style="font-size:0.8rem;color:#aaa;">Ingresa tu código para obtener acceso ampliado como tester.</div>
      </div>
      <form method="POST" action="{% url 'apply_code' %}" style="display:flex;gap:8px;align-items:center;">
        {% csrf_token %}
        <input type="text" name="code" placeholder="COSMIC-XXXXXX" required
               style="padding:10px 14px;border-radius:8px;border:1px solid #333;background:#0d0d1a;color:#f0f0f0;font-size:0.9rem;width:170px;text-transform:uppercase;">
        <button type="submit" style="padding:10px 20px;background:#e94560;color:#fff;border:none;border-radius:8px;font-weight:600;cursor:pointer;white-space:nowrap;">Aplicar</button>
      </form>
    </div>
    {% endif %}

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

    {% if jobs %}
```

No hay test automatizado para el HTML en sí (no hay tests de template en este
proyecto para `dashboard.html`) — la verificación es visual, ver el paso de
verificación manual al final de este plan.

- [ ] **Step 8: Commit**

```bash
git add core/brand_dna/auth_views.py core/brand_dna/urls.py core/brand_dna/templates/brand_dna/dashboard.html core/brand_dna/tests/test_auth_views.py
git commit -m "feat(tester-toggles): vista, URL y UI del dashboard para activar/desactivar reels y carrusel"
```

---

## Verificación manual post-implementación (no automatizable)

Después de que las 3 tareas estén commiteadas y los contenedores recreados
(`docker compose up -d --force-recreate --no-deps backend rqworker` — ver memoria
`feedback_gunicorn_restart.md`):

1. Entrar al dashboard con una cuenta tester real, confirmar que aparece la nueva
   sección "Funciones beta" con los 2 checkboxes (activados por default).
2. Desactivar reels, guardar, generar un calendario nuevo (nuevo análisis o
   `generate_next_week`), confirmar que el día 1 sale como `single` en vez de `reel`.
3. Reactivar reels, desactivar carrusel, generar otro calendario, confirmar que el
   día 3 sale como `single` en vez de `carousel` y el día 1 vuelve a ser `reel`.
4. Confirmar que una cuenta normal (grupo `user`) no ve la sección "Funciones beta"
   en absoluto.
