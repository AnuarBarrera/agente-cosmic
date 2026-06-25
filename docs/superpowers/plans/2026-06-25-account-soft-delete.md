# Account Soft Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add account deactivation (soft delete) with a Danger Zone in the dashboard, reactivation on re-registration, and a management command that cleans up GCS images after 30 days.

**Architecture:** Uses Django's built-in `User.is_active` field plus a new `deactivated_at` timestamp. Deactivation sets `is_active=False` (Django blocks login automatically), flips tenant/subscription status, and flushes the session. Re-registration with the same email detects the deactivated account and offers reactivation. A management command deletes GCS images for accounts deactivated more than 30 days ago.

**Tech Stack:** Django 5.2, google-cloud-storage, PostgreSQL, pytest, Docker Compose

## Global Constraints

- Git commits MUST use `GIT_EDITOR=true git commit -m "msg"` — never heredoc (hangs).
- Container reload: `docker compose up --force-recreate --no-deps backend rqworker nginx`.
- Tests run with: `docker compose exec backend pytest <path> -v`.
- `AUTH_USER_MODEL = 'tenant_management.User'` — custom User with UUID pk, email as USERNAME_FIELD.
- `User` extends `AbstractUser` — already has `is_active` (BooleanField, default=True). Django's `authenticate()` returns `None` for `is_active=False`.
- `TenantModel.status` is CharField — use values `'active'` and `'deactivated'`.
- `Subscription.status` is CharField — use values `'active'` and `'canceled'`.
- GCS bucket: `settings.GOOGLE_CLOUD_STORAGE_BUCKET` (default `'agente-cosmic-assets'`). Blobs at `posts/{job_uuid}-day{N}.png`.
- All test user fixtures must include tenant creation (TenantIsolationMiddleware is active).

---

### Task 1: Model change — add deactivated_at to User + migration

**Files:**
- Modify: `core/tenant_management/models.py:133-143` (User class)
- Create: `core/tenant_management/migrations/0016_user_deactivated_at.py` (auto-generated)
- Test: `core/brand_dna/tests/test_tenant_provisioning.py` (add test)

**Interfaces:**
- Consumes: nothing
- Produces: `User.deactivated_at` (DateTimeField, null=True, blank=True)

- [ ] **Step 1: Write failing test**

Add to `core/brand_dna/tests/test_tenant_provisioning.py`:

```python
def test_user_has_deactivated_at_field(free_plan, django_user_model):
    from core.brand_dna.auth_views import provision_tenant
    user = django_user_model.objects.create_user(
        email='deact@test.com', username='deact@test.com', password='pass1234'
    )
    provision_tenant(user)
    assert user.deactivated_at is None
    from django.utils import timezone
    user.deactivated_at = timezone.now()
    user.save(update_fields=['deactivated_at'])
    user.refresh_from_db()
    assert user.deactivated_at is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_tenant_provisioning.py::test_user_has_deactivated_at_field -v`
Expected: FAIL — `User has no field named 'deactivated_at'`

- [ ] **Step 3: Add deactivated_at field to User**

In `core/tenant_management/models.py`, add after `email_verified` (line 138):

```python
    deactivated_at = models.DateTimeField(null=True, blank=True)
```

- [ ] **Step 4: Generate and run migration**

Run: `docker compose exec backend python manage.py makemigrations tenant_management -n user_deactivated_at`
Run: `docker compose exec backend python manage.py migrate tenant_management`

- [ ] **Step 5: Run test to verify it passes**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_tenant_provisioning.py -v`
Expected: ALL pass.

- [ ] **Step 6: Commit**

```bash
git add core/tenant_management/models.py core/tenant_management/migrations/ core/brand_dna/tests/test_tenant_provisioning.py
GIT_EDITOR=true git commit -m "feat: add deactivated_at field to User model for soft delete"
```

---

### Task 2: Deactivation view + Danger Zone UI

**Files:**
- Modify: `core/brand_dna/auth_views.py` (add `deactivate_account_view`)
- Modify: `core/brand_dna/urls.py` (add URL)
- Modify: `core/brand_dna/templates/brand_dna/dashboard.html` (add Danger Zone)
- Modify: `core/brand_dna/templates/brand_dna/auth/login.html` (add deactivated message)
- Create: `core/brand_dna/tests/test_account_deactivation.py`

**Interfaces:**
- Consumes: `User.deactivated_at` from Task 1
- Produces: `deactivate_account_view(request) -> HttpResponse` — POST with `confirmation=ELIMINAR` deactivates user, flushes session, redirects to login

- [ ] **Step 1: Write failing tests**

Create `core/brand_dna/tests/test_account_deactivation.py`:

```python
import pytest
from django.test import Client
from django.utils import timezone
from core.tenant_management.models import TenantModel, Plan, Subscription

pytestmark = pytest.mark.django_db


@pytest.fixture
def free_plan():
    plan, _ = Plan.objects.get_or_create(name='Free', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    return plan


@pytest.fixture
def user_with_tenant(django_user_model, free_plan):
    u = django_user_model.objects.create_user(
        email='delete@test.com', username='delete@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=u.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=free_plan)
    u.tenant = tenant
    u.save(update_fields=['tenant'])
    return u


def test_deactivate_account_sets_inactive(user_with_tenant):
    c = Client()
    c.force_login(user_with_tenant)
    response = c.post('/dashboard/delete-account/', {'confirmation': 'ELIMINAR'})
    assert response.status_code == 302
    assert '/auth/login/' in response.url

    user_with_tenant.refresh_from_db()
    assert user_with_tenant.is_active is False
    assert user_with_tenant.deactivated_at is not None
    assert user_with_tenant.tenant.status == 'deactivated'
    assert user_with_tenant.tenant.subscription.status == 'canceled'


def test_deactivate_without_confirmation_rejected(user_with_tenant):
    c = Client()
    c.force_login(user_with_tenant)
    response = c.post('/dashboard/delete-account/', {'confirmation': 'wrong'})
    assert response.status_code == 302
    assert '/dashboard/' in response.url

    user_with_tenant.refresh_from_db()
    assert user_with_tenant.is_active is True


def test_deactivate_requires_post(user_with_tenant):
    c = Client()
    c.force_login(user_with_tenant)
    response = c.get('/dashboard/delete-account/')
    assert response.status_code == 302


def test_login_blocked_after_deactivation(user_with_tenant):
    c = Client()
    c.force_login(user_with_tenant)
    c.post('/dashboard/delete-account/', {'confirmation': 'ELIMINAR'})

    c2 = Client()
    response = c2.post('/auth/login/', {
        'email': 'delete@test.com',
        'password': 'pass1234',
    })
    assert response.status_code == 200
    assert b'error' in response.content.lower() or b'incorrecto' in response.content.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_account_deactivation.py -v`
Expected: FAIL — 404 on `/dashboard/delete-account/`

- [ ] **Step 3: Implement deactivate_account_view**

Add to `core/brand_dna/auth_views.py`, after `apply_code_view`:

```python
@login_required
def deactivate_account_view(request):
    if request.method != 'POST':
        return redirect('dashboard')

    if request.POST.get('confirmation', '') != 'ELIMINAR':
        return redirect('dashboard')

    user = request.user
    user.is_active = False
    user.deactivated_at = timezone.now()
    user.save(update_fields=['is_active', 'deactivated_at'])

    if user.tenant:
        user.tenant.status = 'deactivated'
        user.tenant.save(update_fields=['status'])
        try:
            sub = user.tenant.subscription
            sub.status = 'canceled'
            sub.save(update_fields=['status'])
        except Exception:
            pass

    logout(request)
    return redirect('/auth/login/?reason=deactivated')
```

Add the `timezone` import if not present (check — it may already be imported indirectly).

- [ ] **Step 4: Add URL**

In `core/brand_dna/urls.py`, add after the `apply_code` line:

```python
    path('dashboard/delete-account/', auth_views.deactivate_account_view, name='deactivate_account'),
```

- [ ] **Step 5: Add Danger Zone to dashboard template**

In `core/brand_dna/templates/brand_dna/dashboard.html`, add before the closing `</body>` tag:

```html
  <div style="max-width:700px;margin:48px auto 32px;border:1px solid #c0392b;border-radius:12px;padding:28px;background:#1a1215;">
    <h3 style="color:#e74c3c;margin-bottom:12px;font-size:1.1rem;">Zona de peligro</h3>
    <p style="color:#aaa;font-size:0.9rem;margin-bottom:16px;">
      Tu cuenta sera desactivada. Podras reactivarla si te registras de nuevo con el mismo email.
      Despues de 30 dias, las imagenes generadas seran eliminadas permanentemente.
    </p>
    <button type="button" id="dangerBtn" onclick="document.getElementById('dangerConfirm').style.display='block';this.style.display='none';"
      style="background:#c0392b;color:#fff;border:none;padding:10px 24px;border-radius:8px;cursor:pointer;font-weight:600;">
      Eliminar mi cuenta
    </button>
    <div id="dangerConfirm" style="display:none;margin-top:16px;">
      <form method="POST" action="{% url 'deactivate_account' %}">
        {% csrf_token %}
        <label style="color:#e74c3c;font-size:0.85rem;display:block;margin-bottom:8px;">Escribe ELIMINAR para confirmar:</label>
        <input type="text" name="confirmation" autocomplete="off"
          style="padding:10px;border-radius:8px;border:1px solid #c0392b;background:#0d0d1a;color:#f0f0f0;width:200px;margin-right:12px;">
        <button type="submit"
          style="background:#e74c3c;color:#fff;border:none;padding:10px 20px;border-radius:8px;cursor:pointer;font-weight:600;">
          Confirmar eliminacion
        </button>
      </form>
    </div>
  </div>
```

- [ ] **Step 6: Add deactivated message to login template**

In `core/brand_dna/templates/brand_dna/auth/login.html`, after the existing error box block (`{% if error %}`...`{% endif %}`), add:

```html
    {% if request.GET.reason == 'deactivated' %}
    <div style="background:#1a2a1a;border:1px solid #27ae60;color:#6ddf8e;padding:12px;border-radius:8px;font-size:0.9rem;margin-bottom:20px;">
      Tu cuenta fue desactivada exitosamente.
    </div>
    {% endif %}
```

- [ ] **Step 7: Run tests**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_account_deactivation.py -v`
Expected: 4 PASSED

- [ ] **Step 8: Run full suite**

Run: `docker compose exec backend pytest --tb=no -q`
Expected: 241+ passed, 0 failed.

- [ ] **Step 9: Commit**

```bash
git add core/brand_dna/auth_views.py core/brand_dna/urls.py core/brand_dna/templates/brand_dna/dashboard.html core/brand_dna/templates/brand_dna/auth/login.html core/brand_dna/tests/test_account_deactivation.py
GIT_EDITOR=true git commit -m "feat: account deactivation with danger zone in dashboard"
```

---

### Task 3: Reactivation flow — detect deactivated user on registration

**Files:**
- Modify: `core/brand_dna/auth_views.py:185-229` (verify_email_view) and `core/brand_dna/auth_views.py:421-443` (google_callback_view)
- Create: `core/brand_dna/templates/brand_dna/auth/reactivate.html`
- Modify: `core/brand_dna/urls.py` (add reactivate URL)
- Test: `core/brand_dna/tests/test_account_deactivation.py` (add reactivation tests)

**Interfaces:**
- Consumes: `User.is_active`, `User.deactivated_at`, `TenantModel.status`, `Subscription.status`
- Produces: `reactivate_account_view(request, token) -> HttpResponse` — reactivates user, logs in, redirects to dashboard

- [ ] **Step 1: Write failing tests**

Add to `core/brand_dna/tests/test_account_deactivation.py`:

```python
def test_register_with_deactivated_email_shows_reactivation(user_with_tenant, free_plan):
    from core.tenant_management.models import EmailVerificationToken
    from django.contrib.auth.hashers import make_password

    user_with_tenant.is_active = False
    user_with_tenant.deactivated_at = timezone.now()
    user_with_tenant.save(update_fields=['is_active', 'deactivated_at'])

    token = EmailVerificationToken.objects.create(
        email='delete@test.com',
        tenant_name='',
        user_data={'password': make_password('NewPass123!'), 'invitation_code': ''},
    )
    c = Client()
    response = c.get(f'/auth/verify/{token.token}/')
    assert response.status_code == 200
    assert b'reactivar' in response.content.lower() or b'reactivate' in response.content.lower()


def test_reactivation_restores_account(user_with_tenant, free_plan):
    from core.tenant_management.models import EmailVerificationToken
    from django.contrib.auth.hashers import make_password

    user_with_tenant.is_active = False
    user_with_tenant.deactivated_at = timezone.now()
    user_with_tenant.save(update_fields=['is_active', 'deactivated_at'])
    user_with_tenant.tenant.status = 'deactivated'
    user_with_tenant.tenant.save(update_fields=['status'])
    user_with_tenant.tenant.subscription.status = 'canceled'
    user_with_tenant.tenant.subscription.save(update_fields=['status'])

    token = EmailVerificationToken.objects.create(
        email='delete@test.com',
        tenant_name='',
        user_data={'password': make_password('NewPass123!'), 'invitation_code': ''},
    )
    c = Client()
    response = c.post(f'/auth/reactivate/{token.token}/')
    assert response.status_code == 302

    user_with_tenant.refresh_from_db()
    assert user_with_tenant.is_active is True
    assert user_with_tenant.deactivated_at is None
    assert user_with_tenant.tenant.status == 'active'
    assert user_with_tenant.tenant.subscription.status == 'active'


def test_reactivation_preserves_usage(user_with_tenant, free_plan):
    from core.brand_dna.models import AnalysisJob
    from core.tenant_management.models import EmailVerificationToken
    from django.contrib.auth.hashers import make_password

    AnalysisJob.objects.create(
        email=user_with_tenant.email, business_url='https://test.com',
        user=user_with_tenant, status='done',
    )
    original_count = AnalysisJob.objects.filter(user=user_with_tenant).count()

    user_with_tenant.is_active = False
    user_with_tenant.deactivated_at = timezone.now()
    user_with_tenant.save(update_fields=['is_active', 'deactivated_at'])

    token = EmailVerificationToken.objects.create(
        email='delete@test.com',
        tenant_name='',
        user_data={'password': make_password('x'), 'invitation_code': ''},
    )
    c = Client()
    c.post(f'/auth/reactivate/{token.token}/')

    assert AnalysisJob.objects.filter(user=user_with_tenant).count() == original_count
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_account_deactivation.py::test_register_with_deactivated_email_shows_reactivation -v`
Expected: FAIL — verify_email_view creates a new user instead of showing reactivation

- [ ] **Step 3: Create reactivation template**

Create `core/brand_dna/templates/brand_dna/auth/reactivate.html`:

```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Reactivar cuenta — Agente Cosmic</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', sans-serif; background: #0d0d1a; color: #f0f0f0; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
    .card { background: #1a1a2e; max-width: 480px; padding: 40px; border-radius: 16px; text-align: center; }
    h2 { color: #e94560; margin-bottom: 16px; }
    p { color: #aaa; margin-bottom: 24px; line-height: 1.5; }
    .btn { display: inline-block; padding: 14px 32px; border-radius: 8px; font-size: 1rem; font-weight: 600; cursor: pointer; border: none; text-decoration: none; }
    .btn-primary { background: #e94560; color: #fff; margin-right: 12px; }
    .btn-ghost { background: transparent; color: #aaa; border: 1px solid #444; }
  </style>
</head>
<body>
  <div class="card">
    <h2>Ya tenias una cuenta</h2>
    <p>Encontramos una cuenta desactivada con el email <strong>{{ email }}</strong>. Tus datos anteriores (analisis, calendarios) siguen guardados.</p>
    <form method="POST" action="{% url 'reactivate_account' token=token %}" style="display:inline;">
      {% csrf_token %}
      <button type="submit" class="btn btn-primary">Reactivar mi cuenta</button>
    </form>
    <a href="{% url 'login' %}" class="btn btn-ghost">Cancelar</a>
  </div>
</body>
</html>
```

- [ ] **Step 4: Modify verify_email_view to detect deactivated users**

In `core/brand_dna/auth_views.py`, in `verify_email_view`, after retrieving the email (line 198 `email = verification.email`), add before user creation:

```python
    deactivated_user = User.objects.filter(email=email, is_active=False).first()
    if deactivated_user:
        return render(request, 'brand_dna/auth/reactivate.html', {
            'email': email,
            'token': token,
        })
```

- [ ] **Step 5: Implement reactivate_account_view**

Add to `core/brand_dna/auth_views.py`:

```python
def reactivate_account_view(request, token):
    from core.tenant_management.models import EmailVerificationToken

    try:
        verification = EmailVerificationToken.objects.get(token=token)
    except EmailVerificationToken.DoesNotExist:
        return redirect('login')

    if not verification.is_valid():
        return redirect('login')

    email = verification.email
    user = User.objects.filter(email=email, is_active=False).first()
    if not user:
        return redirect('login')

    user.is_active = True
    user.deactivated_at = None
    user.save(update_fields=['is_active', 'deactivated_at'])

    if user.tenant:
        user.tenant.status = 'active'
        user.tenant.save(update_fields=['status'])
        try:
            sub = user.tenant.subscription
            sub.status = 'active'
            sub.save(update_fields=['status'])
        except Exception:
            pass

    verification.is_used = True
    verification.save(update_fields=['is_used'])

    login(request, user)
    return redirect('dashboard')
```

- [ ] **Step 6: Modify google_callback_view to handle deactivated users**

In `core/brand_dna/auth_views.py`, in `google_callback_view`, after `user, created = User.objects.get_or_create(...)` block, before `login(request, user)`, add:

```python
    if not created and not user.is_active:
        user.is_active = True
        user.deactivated_at = None
        user.save(update_fields=['is_active', 'deactivated_at'])
        if user.tenant:
            user.tenant.status = 'active'
            user.tenant.save(update_fields=['status'])
            try:
                sub = user.tenant.subscription
                sub.status = 'active'
                sub.save(update_fields=['status'])
            except Exception:
                pass
```

- [ ] **Step 7: Add reactivation URL**

In `core/brand_dna/urls.py`, add:

```python
    path('auth/reactivate/<str:token>/', auth_views.reactivate_account_view, name='reactivate_account'),
```

- [ ] **Step 8: Run tests**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_account_deactivation.py -v`
Expected: 7 PASSED (4 from Task 2 + 3 new)

- [ ] **Step 9: Run full suite**

Run: `docker compose exec backend pytest --tb=no -q`
Expected: ALL pass.

- [ ] **Step 10: Commit**

```bash
git add core/brand_dna/auth_views.py core/brand_dna/urls.py core/brand_dna/templates/brand_dna/auth/reactivate.html core/brand_dna/tests/test_account_deactivation.py
GIT_EDITOR=true git commit -m "feat: reactivation flow — detect deactivated users on registration, restore account"
```

---

### Task 4: GCS image cleanup management command

**Files:**
- Create: `core/tenant_management/management/commands/cleanup_deactivated_images.py`
- Create: `core/tenant_management/tests/test_cleanup_command.py`

**Interfaces:**
- Consumes: `User.deactivated_at`, `AnalysisJob` (via `user`), `settings.GOOGLE_CLOUD_STORAGE_BUCKET`, `google.cloud.storage.Client`
- Produces: Management command `cleanup_deactivated_images` — deletes GCS blobs and clears file paths for users deactivated >30 days

- [ ] **Step 1: Write failing tests**

Create `core/tenant_management/tests/test_cleanup_command.py`:

```python
import pytest
from unittest.mock import patch, MagicMock
from datetime import timedelta
from django.utils import timezone
from django.core.management import call_command
from core.tenant_management.models import TenantModel, Plan, Subscription
from core.brand_dna.models import AnalysisJob

pytestmark = pytest.mark.django_db


@pytest.fixture
def free_plan():
    plan, _ = Plan.objects.get_or_create(name='Free', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    return plan


@pytest.fixture
def deactivated_user_old(django_user_model, free_plan):
    u = django_user_model.objects.create_user(
        email='old@test.com', username='old@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=u.email, status='deactivated')
    Subscription.objects.create(tenant=tenant, plan=free_plan, status='canceled')
    u.tenant = tenant
    u.is_active = False
    u.deactivated_at = timezone.now() - timedelta(days=31)
    u.save(update_fields=['tenant', 'is_active', 'deactivated_at'])
    return u


@pytest.fixture
def deactivated_user_recent(django_user_model, free_plan):
    u = django_user_model.objects.create_user(
        email='recent@test.com', username='recent@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=u.email, status='deactivated')
    Subscription.objects.create(tenant=tenant, plan=free_plan, status='canceled')
    u.tenant = tenant
    u.is_active = False
    u.deactivated_at = timezone.now() - timedelta(days=10)
    u.save(update_fields=['tenant', 'is_active', 'deactivated_at'])
    return u


def test_cleanup_deletes_old_user_images(deactivated_user_old):
    job = AnalysisJob.objects.create(
        email=deactivated_user_old.email, business_url='https://test.com',
        user=deactivated_user_old, status='done',
        logo_file_path='uploads/logo_test.jpg',
        product_image_paths=['uploads/p1.jpg', 'uploads/p2.jpg'],
    )

    mock_bucket = MagicMock()
    mock_blob = MagicMock()
    mock_bucket.list_blobs.return_value = [mock_blob]

    with patch('core.tenant_management.management.commands.cleanup_deactivated_images.storage.Client') as mock_client:
        mock_client.return_value.bucket.return_value = mock_bucket
        call_command('cleanup_deactivated_images')

    job.refresh_from_db()
    assert job.logo_file_path == ''
    assert job.product_image_paths == []


def test_cleanup_skips_recent_deactivation(deactivated_user_recent):
    job = AnalysisJob.objects.create(
        email=deactivated_user_recent.email, business_url='https://test.com',
        user=deactivated_user_recent, status='done',
        logo_file_path='uploads/logo_test.jpg',
        product_image_paths=['uploads/p1.jpg'],
    )

    with patch('core.tenant_management.management.commands.cleanup_deactivated_images.storage.Client'):
        call_command('cleanup_deactivated_images')

    job.refresh_from_db()
    assert job.logo_file_path == 'uploads/logo_test.jpg'
    assert job.product_image_paths == ['uploads/p1.jpg']
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec backend pytest core/tenant_management/tests/test_cleanup_command.py -v`
Expected: FAIL — command does not exist

- [ ] **Step 3: Implement the management command**

Create `core/tenant_management/management/commands/cleanup_deactivated_images.py`:

```python
import logging
import os
from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from google.cloud import storage
from core.tenant_management.models import User
from core.brand_dna.models import AnalysisJob

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Delete GCS images and local files for users deactivated more than 30 days ago'

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=30)
        users = User.objects.filter(
            is_active=False,
            deactivated_at__isnull=False,
            deactivated_at__lt=cutoff,
        )

        if not users.exists():
            self.stdout.write('No users to clean up.')
            return

        bucket_name = settings.GOOGLE_CLOUD_STORAGE_BUCKET
        gcs_client = storage.Client(project=settings.GOOGLE_CLOUD_PROJECT)
        bucket = gcs_client.bucket(bucket_name)

        total_blobs = 0
        total_jobs = 0

        for user in users:
            jobs = AnalysisJob.objects.filter(user=user)
            for job in jobs:
                blobs = list(bucket.list_blobs(prefix=f'posts/{job.id}-'))
                for blob in blobs:
                    blob.delete()
                    total_blobs += 1

                for path_field in ('logo_file_path', 'product_image_path'):
                    path = getattr(job, path_field)
                    if path:
                        full = os.path.join(settings.MEDIA_ROOT, path)
                        if os.path.exists(full):
                            os.remove(full)

                for path in (job.post_images_paths or []):
                    full = os.path.join(settings.MEDIA_ROOT, path)
                    if os.path.exists(full):
                        os.remove(full)

                for path in (job.product_image_paths or []):
                    full = os.path.join(settings.MEDIA_ROOT, path)
                    if os.path.exists(full):
                        os.remove(full)

                job.logo_file_path = ''
                job.product_image_path = ''
                job.post_images_paths = []
                job.product_image_paths = []
                job.save(update_fields=[
                    'logo_file_path', 'product_image_path',
                    'post_images_paths', 'product_image_paths',
                ])
                total_jobs += 1

            logger.info(f'Cleaned images for user {user.email}')

        self.stdout.write(
            f'Cleanup complete: {users.count()} users, {total_jobs} jobs, {total_blobs} GCS blobs deleted.'
        )
```

- [ ] **Step 4: Run tests**

Run: `docker compose exec backend pytest core/tenant_management/tests/test_cleanup_command.py -v`
Expected: 2 PASSED

- [ ] **Step 5: Run full suite**

Run: `docker compose exec backend pytest --tb=no -q`
Expected: ALL pass.

- [ ] **Step 6: Commit**

```bash
git add core/tenant_management/management/commands/cleanup_deactivated_images.py core/tenant_management/tests/test_cleanup_command.py
GIT_EDITOR=true git commit -m "feat: management command cleanup_deactivated_images — delete GCS blobs after 30 days"
```
