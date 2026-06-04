# Rate Limits + Delete Calendar Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Proteger créditos de API con rate limits por plan (2 calendarios/semana, 2 regeneraciones/post, 2 edits/post), permitir eliminar calendarios, y tener un plan "Admin" sin límites para pruebas internas.

**Architecture:** Se extiende el modelo `Plan` existente con 3 campos nuevos de límites de contenido. `ContentPost` recibe contadores de uso. Un helper `rate_limits.py` centraliza la lógica de verificación. Las views `analyze_submit` y `post_action_api` aplican los límites devolviendo 429 cuando se supera. Un plan "Admin" (max=999) se crea via data migration para pruebas sin límites.

**Tech Stack:** Django 5.2, PostgreSQL, Django migrations, Python 3.12

---

## File Structure

| Archivo | Acción | Responsabilidad |
|---|---|---|
| `core/tenant_management/models.py` | Modificar | Añadir 3 campos a `Plan` |
| `core/tenant_management/migrations/0012_plan_content_limits.py` | Crear | Schema + data migration (crea planes Free y Admin) |
| `core/content_pipeline/models.py` | Modificar | Añadir `regen_count`, `edit_count` a `ContentPost` |
| `core/content_pipeline/migrations/0003_contentpost_counts.py` | Crear | Migración de contadores |
| `core/brand_dna/rate_limits.py` | Crear | Helpers: `get_user_plan()`, `can_create_calendar()`, `can_regenerate()`, `can_edit()` |
| `core/brand_dna/tests/test_rate_limits.py` | Crear | Tests de rate limits |
| `core/brand_dna/views.py` | Modificar | Aplicar límites en `analyze_submit` y `post_action_api` |
| `core/brand_dna/urls.py` | Modificar | Añadir URL de eliminar calendario |
| `core/brand_dna/templates/brand_dna/calendar_review.html` | Modificar | Botón eliminar + contadores de uso + manejo 429 en JS |
| `core/brand_dna/templates/brand_dna/landing.html` | Modificar | Mostrar error de límite de calendarios |

---

## Task 1: Extender Plan + ContentPost + Migraciones

**Files:**
- Modify: `core/tenant_management/models.py`
- Create: `core/tenant_management/migrations/0012_plan_content_limits.py`
- Modify: `core/content_pipeline/models.py`
- Create: `core/content_pipeline/migrations/0003_contentpost_counts.py`

- [ ] **Step 1: Añadir campos a Plan model**

En `core/tenant_management/models.py`, reemplazar la clase `Plan` entera:

```python
class Plan(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    max_daily_interactions = models.PositiveIntegerField(default=100)
    max_monthly_interactions = models.PositiveIntegerField(default=1000)
    max_calendars_per_week = models.PositiveIntegerField(default=2)
    max_post_regenerations = models.PositiveIntegerField(default=2)
    max_post_edits = models.PositiveIntegerField(default=2)
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0.0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    class Meta:
        db_table = 'plans'
        verbose_name = 'Plan'
        verbose_name_plural = 'Plans'
```

- [ ] **Step 2: Añadir campos regen_count y edit_count a ContentPost**

En `core/content_pipeline/models.py`, añadir los dos campos dentro de la clase `ContentPost`, después de `user_note`:

```python
    user_note = models.TextField(blank=True, default='')
    regen_count = models.PositiveIntegerField(default=0)
    edit_count = models.PositiveIntegerField(default=0)
```

- [ ] **Step 3: Generar ambas migraciones automáticamente**

```bash
docker compose exec backend python manage.py makemigrations tenant_management --name plan_content_limits
docker compose exec backend python manage.py makemigrations content_pipeline --name contentpost_counts
```

Verificar que ambos archivos se crearon:
```bash
ls core/tenant_management/migrations/ | tail -3
ls core/content_pipeline/migrations/ | tail -3
```

- [ ] **Step 4: Añadir data migration a 0012 para crear planes Free y Admin**

Abrir el archivo `core/tenant_management/migrations/0012_plan_content_limits.py` y añadir `RunPython` al final de `operations`:

```python
from django.db import migrations, models


def create_default_plans(apps, schema_editor):
    Plan = apps.get_model('tenant_management', 'Plan')
    Plan.objects.get_or_create(
        name='Free',
        defaults={
            'max_daily_interactions': 100,
            'max_monthly_interactions': 1000,
            'max_calendars_per_week': 2,
            'max_post_regenerations': 2,
            'max_post_edits': 2,
            'price': '0.00',
        },
    )
    Plan.objects.get_or_create(
        name='Admin',
        defaults={
            'max_daily_interactions': 99999,
            'max_monthly_interactions': 99999,
            'max_calendars_per_week': 99999,
            'max_post_regenerations': 99999,
            'max_post_edits': 99999,
            'price': '0.00',
        },
    )


def remove_default_plans(apps, schema_editor):
    Plan = apps.get_model('tenant_management', 'Plan')
    Plan.objects.filter(name__in=['Free', 'Admin']).delete()
```

Y en `operations` añadir al final (después del último `AddField`):
```python
        migrations.RunPython(create_default_plans, remove_default_plans),
```

> **IMPORTANTE:** El archivo de migración generado en Step 3 tendrá la estructura correcta de `AddField`. Solo hay que agregar las funciones y el `RunPython` al final. Añadir las funciones `create_default_plans` y `remove_default_plans` arriba de la clase `Migration`.

- [ ] **Step 5: Aplicar migraciones**

```bash
docker compose exec backend python manage.py migrate tenant_management
docker compose exec backend python manage.py migrate content_pipeline
```

Expected output: `Running migrations: Applying tenant_management.0012_plan_content_limits... OK` y `Applying content_pipeline.0003_contentpost_counts... OK`

- [ ] **Step 6: Verificar que los planes se crearon**

```bash
docker compose exec backend python manage.py shell -c "
from core.tenant_management.models import Plan
for p in Plan.objects.all():
    print(p.name, p.max_calendars_per_week, p.max_post_regenerations, p.max_post_edits)
"
```

Expected: líneas `Free 2 2 2` y `Admin 99999 99999 99999`

- [ ] **Step 7: Commit**

```bash
git add core/tenant_management/models.py core/tenant_management/migrations/ \
        core/content_pipeline/models.py core/content_pipeline/migrations/
git commit -m "feat: Plan content limits + ContentPost counters + planes Free/Admin"
```

---

## Task 2: Rate Limits Helper + Tests

**Files:**
- Create: `core/brand_dna/rate_limits.py`
- Create: `core/brand_dna/tests/test_rate_limits.py`

- [ ] **Step 1: Escribir los tests primero**

Crear `core/brand_dna/tests/test_rate_limits.py`:

```python
from datetime import timedelta
from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.utils import timezone


class TestGetUserPlan(TestCase):
    def test_returns_free_plan_when_no_tenant(self):
        from core.brand_dna.rate_limits import get_user_plan
        from core.tenant_management.models import Plan
        Plan.objects.get_or_create(
            name='Free',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant = None
        plan = get_user_plan(user)
        assert plan.max_calendars_per_week == 2

    def test_returns_plan_from_subscription(self):
        from core.brand_dna.rate_limits import get_user_plan
        from core.tenant_management.models import Plan
        admin_plan, _ = Plan.objects.get_or_create(
            name='Admin',
            defaults={'max_calendars_per_week': 99999, 'max_post_regenerations': 99999,
                      'max_post_edits': 99999, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant.subscription.plan = admin_plan
        plan = get_user_plan(user)
        assert plan.max_calendars_per_week == 99999


class TestCanCreateCalendar(TestCase):
    def test_allowed_when_under_limit(self):
        from core.brand_dna.rate_limits import can_create_calendar
        from core.tenant_management.models import Plan
        Plan.objects.get_or_create(
            name='Free',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant = None
        with patch('core.brand_dna.rate_limits.AnalysisJob') as MockJob:
            MockJob.objects.filter.return_value.count.return_value = 1
            allowed, remaining = can_create_calendar(user)
        assert allowed is True
        assert remaining == 1

    def test_blocked_when_at_limit(self):
        from core.brand_dna.rate_limits import can_create_calendar
        from core.tenant_management.models import Plan
        Plan.objects.get_or_create(
            name='Free',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant = None
        with patch('core.brand_dna.rate_limits.AnalysisJob') as MockJob:
            MockJob.objects.filter.return_value.count.return_value = 2
            allowed, remaining = can_create_calendar(user)
        assert allowed is False
        assert remaining == 0


class TestCanRegenerate(TestCase):
    def test_allowed_when_under_limit(self):
        from core.brand_dna.rate_limits import can_regenerate
        from core.tenant_management.models import Plan
        Plan.objects.get_or_create(
            name='Free',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant = None
        post = MagicMock()
        post.regen_count = 1
        allowed, remaining = can_regenerate(post, user)
        assert allowed is True
        assert remaining == 1

    def test_blocked_when_at_limit(self):
        from core.brand_dna.rate_limits import can_regenerate
        from core.tenant_management.models import Plan
        Plan.objects.get_or_create(
            name='Free',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant = None
        post = MagicMock()
        post.regen_count = 2
        allowed, remaining = can_regenerate(post, user)
        assert allowed is False
        assert remaining == 0


class TestCanEdit(TestCase):
    def test_allowed_when_under_limit(self):
        from core.brand_dna.rate_limits import can_edit
        from core.tenant_management.models import Plan
        Plan.objects.get_or_create(
            name='Free',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant = None
        post = MagicMock()
        post.edit_count = 0
        allowed, remaining = can_edit(post, user)
        assert allowed is True
        assert remaining == 2

    def test_blocked_when_at_limit(self):
        from core.brand_dna.rate_limits import can_edit
        from core.tenant_management.models import Plan
        Plan.objects.get_or_create(
            name='Free',
            defaults={'max_calendars_per_week': 2, 'max_post_regenerations': 2,
                      'max_post_edits': 2, 'price': '0.00'},
        )
        user = MagicMock()
        user.tenant = None
        post = MagicMock()
        post.edit_count = 2
        allowed, remaining = can_edit(post, user)
        assert allowed is False
        assert remaining == 0
```

- [ ] **Step 2: Verificar que los tests fallan**

```bash
docker compose exec backend python -m pytest core/brand_dna/tests/test_rate_limits.py -v 2>&1 | tail -20
```

Expected: errores de importación `No module named 'core.brand_dna.rate_limits'`

- [ ] **Step 3: Crear `core/brand_dna/rate_limits.py`**

```python
from datetime import timedelta
from django.utils import timezone


def get_user_plan(user):
    """Retorna el Plan del usuario. Fallback: plan 'Free' de la DB."""
    from core.tenant_management.models import Plan
    try:
        return user.tenant.subscription.plan
    except Exception:
        return Plan.objects.filter(name='Free').first() or Plan(
            max_calendars_per_week=2,
            max_post_regenerations=2,
            max_post_edits=2,
        )


def can_create_calendar(user) -> tuple[bool, int]:
    """Verifica si el usuario puede crear un calendario esta semana.
    Retorna (permitido, restantes).
    """
    from core.brand_dna.models import AnalysisJob
    plan = get_user_plan(user)
    week_ago = timezone.now() - timedelta(days=7)
    used = AnalysisJob.objects.filter(user=user, created_at__gte=week_ago).count()
    remaining = max(0, plan.max_calendars_per_week - used)
    return remaining > 0, remaining


def can_regenerate(post, user) -> tuple[bool, int]:
    """Verifica si el post puede regenerarse. Retorna (permitido, restantes)."""
    plan = get_user_plan(user)
    remaining = max(0, plan.max_post_regenerations - post.regen_count)
    return remaining > 0, remaining


def can_edit(post, user) -> tuple[bool, int]:
    """Verifica si el post puede editarse. Retorna (permitido, restantes)."""
    plan = get_user_plan(user)
    remaining = max(0, plan.max_post_edits - post.edit_count)
    return remaining > 0, remaining
```

- [ ] **Step 4: Correr tests y verificar que pasan**

```bash
docker compose exec backend python -m pytest core/brand_dna/tests/test_rate_limits.py -v 2>&1 | tail -20
```

Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add core/brand_dna/rate_limits.py core/brand_dna/tests/test_rate_limits.py
git commit -m "feat: rate_limits helper — get_user_plan, can_create_calendar, can_regenerate, can_edit"
```

---

## Task 3: Límite en crear calendario (analyze_submit)

**Files:**
- Modify: `core/brand_dna/views.py`
- Modify: `core/brand_dna/templates/brand_dna/landing.html`

- [ ] **Step 1: Añadir enforcement en `analyze_submit`**

En `core/brand_dna/views.py`, al inicio de la función `analyze_submit` (después de `if request.method != 'POST': return redirect('landing')`), añadir el bloque de verificación:

```python
@login_required
def analyze_submit(request):
    if request.method != 'POST':
        return redirect('landing')

    from core.brand_dna.rate_limits import can_create_calendar, get_user_plan
    allowed, remaining = can_create_calendar(request.user)
    if not allowed:
        plan = get_user_plan(request.user)
        return render(request, 'brand_dna/landing.html', {
            'error': f'Límite alcanzado: máximo {plan.max_calendars_per_week} calendarios por semana. Vuelve en 7 días o contacta soporte para ampliar tu plan.',
        })

    email = request.user.email
    # ... resto del código sin cambios
```

- [ ] **Step 2: Añadir bloque de error en landing.html**

En `core/brand_dna/templates/brand_dna/landing.html`, añadir inmediatamente antes de `<div class="form-card">` (o dentro, justo antes del `<form>`):

```html
    {% if error %}
    <div style="background:#3a1a1a;border:1px solid #c0392b;color:#e74c3c;padding:16px 20px;border-radius:10px;margin-bottom:20px;max-width:600px;margin-left:auto;margin-right:auto;">
      ⚠️ {{ error }}
    </div>
    {% endif %}
```

- [ ] **Step 3: Verificar manualmente**

Con una cuenta Free (sin `Subscription` asignada), crear 2 calendarios. Al intentar el tercero debe aparecer el banner rojo con el mensaje de límite.

Con una cuenta Admin (con `Subscription` al plan Admin), verificar que puede crear calendarios sin límite.

> Para asignar el plan Admin a tu cuenta de prueba desde Django shell:
> ```bash
> docker compose exec backend python manage.py shell -c "
> from core.tenant_management.models import Plan, TenantModel, Subscription
> from django.contrib.auth import get_user_model
> User = get_user_model()
> u = User.objects.get(email='tu@email.com')
> plan = Plan.objects.get(name='Admin')
> # Si el usuario no tiene tenant, crear uno
> if not u.tenant:
>     t = TenantModel.objects.create(name=u.email, status='active')
>     u.tenant = t
>     u.save(update_fields=['tenant'])
> Subscription.objects.get_or_create(tenant=u.tenant, defaults={'plan': plan, 'status': 'active'})
> print('Plan Admin asignado a', u.email)
> "
> ```

- [ ] **Step 4: Commit**

```bash
git add core/brand_dna/views.py core/brand_dna/templates/brand_dna/landing.html
git commit -m "feat: rate limit en crear calendario — bloquea en 429 con mensaje claro"
```

---

## Task 4: Límites en editar y regenerar post

**Files:**
- Modify: `core/brand_dna/views.py` (función `post_action_api`)

- [ ] **Step 1: Añadir enforcement en la acción `edit`**

En `core/brand_dna/views.py`, en la función `post_action_api`, reemplazar el bloque `if action == 'edit':` completo:

```python
    if action == 'edit':
        if not value:
            return JsonResponse({'error': 'Caption vacío'}, status=400)
        from core.brand_dna.rate_limits import can_edit
        allowed, remaining = can_edit(post, request.user)
        if not allowed:
            return JsonResponse({
                'error': 'Límite de ediciones alcanzado para este post (máximo 2).',
                'limit_reached': True,
            }, status=429)
        post.caption = value
        post.user_status = ContentPost.USER_STATUS_EDITED
        post.edit_count += 1
        post.save(update_fields=['caption', 'user_status', 'edit_count'])
        return JsonResponse({'status': 'ok', 'caption': post.caption, 'remaining_edits': remaining - 1})
```

- [ ] **Step 2: Añadir enforcement en la acción `regenerate`**

Reemplazar el bloque `if action == 'regenerate':` (el `if not value` al inicio), añadiendo el check antes de proceder:

```python
    if action == 'regenerate':
        if not value:
            return JsonResponse({'error': 'Feedback vacío'}, status=400)
        from core.brand_dna.rate_limits import can_regenerate
        allowed, remaining = can_regenerate(post, request.user)
        if not allowed:
            return JsonResponse({
                'error': 'Límite de regeneraciones alcanzado para este post (máximo 2).',
                'limit_reached': True,
            }, status=429)
        new_caption = _regenerate_caption(post, value)
        post.caption = new_caption
        post.user_note = value
        post.user_status = ContentPost.USER_STATUS_CHANGE_REQUESTED
        post.regen_count += 1
        # ... resto del bloque de regeneración sin cambios hasta el save ...
```

> El resto del bloque `regenerate` (generación de imagen, etc.) permanece igual. Solo hay que añadir el check al inicio y `post.regen_count += 1` antes de los `save()` calls. El `save` existente ya guarda `caption`, `user_note`, `user_status` e `image_url`. Agregar `regen_count` al `update_fields`:
> ```python
> post.save(update_fields=['caption', 'user_note', 'user_status', 'image_url', 'regen_count'])
> # y en el except:
> post.save(update_fields=['caption', 'user_note', 'user_status', 'regen_count'])
> ```

- [ ] **Step 3: Añadir respuesta de remaining en regenerate**

Al final del `return JsonResponse` del `regenerate` exitoso, añadir `remaining_regens`:
```python
        return JsonResponse({
            'status': 'ok',
            'caption': new_caption,
            'image_url': new_image_url,
            'remaining_regens': remaining - 1,
        })
```

- [ ] **Step 4: Correr tests existentes para asegurar no hay regresión**

```bash
docker compose exec backend python -m pytest core/brand_dna/ core/content_pipeline/ -v 2>&1 | tail -15
```

Expected: todos los tests pasan.

- [ ] **Step 5: Commit**

```bash
git add core/brand_dna/views.py
git commit -m "feat: rate limits en edit y regenerate post — 429 con limite_reached"
```

---

## Task 5: Eliminar calendario

**Files:**
- Modify: `core/brand_dna/views.py`
- Modify: `core/brand_dna/urls.py`

- [ ] **Step 1: Añadir la view `delete_calendar_api`**

En `core/brand_dna/views.py`, añadir después de la función `calendar_review_view`:

```python
@login_required
@require_POST
def delete_calendar_api(request, job_id):
    from core.content_pipeline.models import ContentCalendar
    job = get_object_or_404(AnalysisJob, id=job_id, user=request.user)
    brand_dna = getattr(job, 'brand_dna', None)
    if not brand_dna:
        return JsonResponse({'error': 'No existe Brand DNA para este job'}, status=404)
    calendar = getattr(brand_dna, 'calendar', None)
    if not calendar:
        return JsonResponse({'error': 'No hay calendario que eliminar'}, status=404)
    calendar.delete()  # cascade elimina todos los ContentPost
    return JsonResponse({'status': 'ok'})
```

- [ ] **Step 2: Añadir la URL**

En `core/brand_dna/urls.py`, añadir dentro de `urlpatterns`:

```python
    path('api/calendar/<uuid:job_id>/delete/', views.delete_calendar_api, name='delete_calendar_api'),
```

- [ ] **Step 3: Verificar que la URL responde correctamente**

```bash
docker compose exec backend python manage.py check
```

Expected: `System check identified no issues (0 silenced).`

- [ ] **Step 4: Commit**

```bash
git add core/brand_dna/views.py core/brand_dna/urls.py
git commit -m "feat: delete_calendar_api — elimina calendario con cascade"
```

---

## Task 6: UI — Botón eliminar + contadores de uso + mensajes 429

**Files:**
- Modify: `core/brand_dna/views.py` (función `calendar_review_view`)
- Modify: `core/brand_dna/templates/brand_dna/calendar_review.html`

- [ ] **Step 1: Pasar datos de plan al template**

En `core/brand_dna/views.py`, modificar `calendar_review_view` para incluir los límites del plan:

```python
@login_required
def calendar_review_view(request, job_id):
    from core.content_pipeline.models import ContentPost
    from core.brand_dna.rate_limits import get_user_plan
    job = get_object_or_404(AnalysisJob, id=job_id, user=request.user)
    brand_dna = getattr(job, 'brand_dna', None)
    calendar = getattr(brand_dna, 'calendar', None) if brand_dna else None
    posts = list(calendar.posts.order_by('day_number')) if calendar else []
    plan = get_user_plan(request.user)
    return render(request, 'brand_dna/calendar_review.html', {
        'job': job,
        'brand_dna': brand_dna,
        'posts': posts,
        'max_regenerations': plan.max_post_regenerations,
        'max_edits': plan.max_post_edits,
    })
```

- [ ] **Step 2: Añadir botón "Eliminar calendario" en el nav del template**

En `core/brand_dna/templates/brand_dna/calendar_review.html`, dentro de `<div class="nav-actions">` (línea ~96), añadir el botón de eliminar:

```html
  <div class="nav-actions">
    <button onclick="deleteCalendar('{{ job.id }}')"
            style="background:#3a1a1a;color:#e74c3c;border:1px solid #c0392b;padding:8px 16px;border-radius:8px;cursor:pointer;font-size:14px;">
      🗑 Eliminar calendario
    </button>
    <!-- botones existentes que ya están aquí -->
  </div>
```

- [ ] **Step 3: Añadir indicadores de uso por post**

En el template, dentro de cada `<div class="actions">` (donde están los botones de aprobar/editar/regen), añadir los badges de uso después de los botones:

```html
          <div class="actions">
            <button class="btn-action btn-approve" onclick="approvePost('{{ post.id }}')">✓ Aprobar</button>
            <button class="btn-action btn-edit" onclick="startEdit('{{ post.id }}')">✏ Editar</button>
            <button class="btn-action btn-regen" onclick="toggleRegen('{{ post.id }}')">↺ Cambio</button>
            <button class="btn-action btn-save" id="save-{{ post.id }}" onclick="saveEdit('{{ post.id }}')">Guardar</button>
            <span style="font-size:11px;color:#888;margin-left:4px;" id="limits-{{ post.id }}">
              Edits: {{ post.edit_count }}/{{ max_edits }} · Regens: {{ post.regen_count }}/{{ max_regenerations }}
            </span>
          </div>
```

- [ ] **Step 4: Añadir manejo de 429 y función deleteCalendar en el JS**

En el bloque `<script>` del template, reemplazar las funciones `saveEdit` y `requestRegen` para manejar 429, y añadir `deleteCalendar`:

Reemplazar `async function saveEdit(postId)`:
```javascript
  async function saveEdit(postId) {
    const cap = document.getElementById('caption-' + postId);
    const newCaption = cap.innerText.trim();
    const res = await fetch(`/api/post/${postId}/action/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify({ action: 'edit', value: newCaption }),
    });
    const data = await res.json();
    if (res.status === 429) {
      showToast(data.error, '#e74c3c');
      cap.contentEditable = 'false';
      document.getElementById('save-' + postId).style.display = 'none';
      return;
    }
    if (data.status === 'ok') {
      cap.contentEditable = 'false';
      document.getElementById('save-' + postId).style.display = 'none';
      setCardStatus(postId, 'edited', '✏ Editado');
      showToast('Caption guardado ✏');
      const limitsEl = document.getElementById('limits-' + postId);
      if (limitsEl && data.remaining_edits !== undefined) {
        limitsEl.textContent = limitsEl.textContent.replace(/Edits: \d+/, `Edits: ${{{ max_edits }} - data.remaining_edits}`);
      }
    }
  }
```

Reemplazar `async function requestRegen(postId)`:
```javascript
  async function requestRegen(postId) {
    const note = document.getElementById('note-' + postId).value.trim();
    if (!note) { showToast('Escribe el feedback primero', '#f0c040'); return; }
    const btn = document.querySelector(`#regen-${postId} .btn-send-regen`);
    btn.textContent = '🎨 Generando imagen y caption...';
    btn.disabled = true;
    const res = await fetch(`/api/post/${postId}/action/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify({ action: 'regenerate', value: note }),
    });
    const data = await res.json();
    btn.textContent = 'Regenerar con IA ✨';
    btn.disabled = false;
    if (res.status === 429) {
      showToast(data.error, '#e74c3c');
      document.getElementById('regen-' + postId).style.display = 'none';
      return;
    }
    if (data.status === 'ok') {
      document.getElementById('caption-' + postId).textContent = data.caption;
      if (data.image_url) {
        const card = document.getElementById('card-' + postId);
        const imgEl = card.querySelector('.post-img img');
        const linkEl = card.querySelector('.post-img a');
        const freshUrl = data.image_url + '?t=' + Date.now();
        if (imgEl) imgEl.src = freshUrl;
        if (linkEl) linkEl.href = data.image_url;
      }
      document.getElementById('regen-' + postId).style.display = 'none';
      setCardStatus(postId, 'change_requested', '↺ Cambio solicitado');
      showToast('Caption e imagen regenerados ✨', '#f0c040');
    }
  }
```

Añadir función `deleteCalendar` antes de los event listeners del botón `backToTop`:
```javascript
  async function deleteCalendar(jobId) {
    if (!confirm('¿Eliminar este calendario? Se borrarán todos los posts. Esta acción no se puede deshacer.')) return;
    const res = await fetch(`/api/calendar/${jobId}/delete/`, {
      method: 'POST',
      headers: { 'X-CSRFToken': CSRF },
    });
    const data = await res.json();
    if (data.status === 'ok') {
      showToast('Calendario eliminado', '#e74c3c');
      setTimeout(() => window.location.href = '/dashboard/', 1500);
    } else {
      showToast(data.error || 'Error al eliminar', '#e74c3c');
    }
  }
```

- [ ] **Step 5: Reiniciar backend y probar en el navegador**

```bash
docker compose restart backend
```

Ir al calendario de un job existente y verificar:
1. Botón "🗑 Eliminar calendario" visible en el nav
2. Contadores `Edits: 0/2 · Regens: 0/2` visibles bajo cada post
3. Al hacer clic en eliminar: diálogo de confirmación → redirección a dashboard
4. Al editar 2 veces el mismo post: el tercer intento muestra toast rojo con mensaje de límite
5. Al regenerar 2 veces el mismo post: el tercer intento muestra toast rojo con mensaje de límite

- [ ] **Step 6: Commit**

```bash
git add core/brand_dna/views.py core/brand_dna/templates/brand_dna/calendar_review.html
git commit -m "feat: UI — botón eliminar calendario + contadores uso + manejo 429 en edit/regen"
```

---

## Post-implementación: Asignar plan Admin a cuentas de prueba

Después de las migraciones, ejecutar este comando para cada cuenta de prueba que necesite sin límites:

```bash
docker compose exec backend python manage.py shell -c "
from core.tenant_management.models import Plan, TenantModel, Subscription
from django.contrib.auth import get_user_model
User = get_user_model()

emails = ['contacto.neia@gmail.com']  # añadir más emails si necesario
admin_plan = Plan.objects.get(name='Admin')

for email in emails:
    try:
        u = User.objects.get(email=email)
        if not u.tenant:
            t = TenantModel.objects.create(name=email, status='active')
            u.tenant = t
            u.save(update_fields=['tenant'])
        sub, created = Subscription.objects.get_or_create(
            tenant=u.tenant,
            defaults={'plan': admin_plan, 'status': 'active'},
        )
        if not created:
            sub.plan = admin_plan
            sub.save(update_fields=['plan'])
        print(f'✓ {email} → Admin')
    except User.DoesNotExist:
        print(f'✗ {email} no encontrado')
"
```

---

## Self-Review

**Spec coverage:**
- ✅ 2 calendarios/semana por usuario → Task 3
- ✅ 2 regeneraciones por post (separado) → Task 4
- ✅ 2 ediciones por post (separado) → Task 4
- ✅ Eliminar calendario → Task 5
- ✅ Plan Admin sin límites → Task 1 (data migration) + Post-implementación
- ✅ UI con feedback de límites → Task 6

**Placeholder scan:** Ningún TBD, TODO o placeholder encontrado.

**Type consistency:**
- `can_create_calendar(user)` → `tuple[bool, int]` — usado correctamente en Task 3
- `can_regenerate(post, user)` → `tuple[bool, int]` — usado correctamente en Task 4
- `can_edit(post, user)` → `tuple[bool, int]` — usado correctamente en Task 4
- `regen_count`, `edit_count` en ContentPost — usados en Tasks 4 y 6
