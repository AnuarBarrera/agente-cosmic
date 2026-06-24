# Tenant Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-provision a tenant + subscription when users register, activate TenantIsolation and SessionTimeout middlewares, and backfill existing users with tenants.

**Architecture:** On registration (email or Google OAuth), create a `TenantModel` + `Subscription(plan=Free)` and assign it to the user. Activate two middlewares in the Django MIDDLEWARE list: `TenantIsolationMiddleware` (with a public-path whitelist) and a rewritten `SessionTimeoutMiddleware` (inactivity-based, using Django sessions instead of JWT). A data migration backfills tenants for existing users.

**Tech Stack:** Django 5.2, PostgreSQL, pytest, Docker Compose

## Global Constraints

- Git commits MUST use `GIT_EDITOR=true git commit -m "msg"` — never heredoc (hangs in this environment).
- Container reload: `docker compose up --force-recreate --no-deps backend rqworker nginx`.
- Tests run with: `docker compose exec backend pytest <path> -v`.
- `AUTH_USER_MODEL = 'tenant_management.User'` — custom User with UUID pk, email as USERNAME_FIELD.
- Plans seeded by migrations: `Free` (calendars=2, regen=2, edits=2), `Tester` (5/5/5), `Admin` (99999/99999/99999).
- `get_user_plan(user)` in `core/brand_dna/rate_limits.py` already falls back from `user.tenant.subscription.plan` → group-based plan lookup → Free defaults. This function does NOT change.
- Public routes (no auth required): `/`, `/auth/login/`, `/auth/register/`, `/auth/verify/<token>/`, `/auth/forgot-password/`, `/auth/reset-password/<token>/`, `/auth/google/`, `/auth/google/callback/`, `/health/`, `/admin/`, `/static/`, `/media/`, `/metrics`, `/favicon.svg`.

---

### Task 1: Tenant auto-provisioning on registration

**Files:**
- Modify: `core/brand_dna/auth_views.py:169-213` (verify_email_view) and `core/brand_dna/auth_views.py:409-421` (google_callback_view)
- Create: `core/brand_dna/tests/test_tenant_provisioning.py`

**Interfaces:**
- Consumes: `TenantModel`, `Subscription`, `Plan` from `core.tenant_management.models`
- Produces: `provision_tenant(user: User) -> TenantModel` — a helper function in `auth_views.py` that creates tenant + subscription and assigns them to the user. Called from `verify_email_view` and `google_callback_view`.

- [ ] **Step 1: Write failing tests**

Create `core/brand_dna/tests/test_tenant_provisioning.py`:

```python
import pytest
from unittest.mock import patch
from django.test import Client
from core.tenant_management.models import TenantModel, Subscription, Plan

pytestmark = pytest.mark.django_db


@pytest.fixture
def free_plan():
    plan, _ = Plan.objects.get_or_create(
        name='Free',
        defaults={
            'max_calendars_per_week': 2,
            'max_post_regenerations': 2,
            'max_post_edits': 2,
            'price': 0,
        },
    )
    return plan


def test_provision_tenant_creates_tenant_and_subscription(free_plan, django_user_model):
    from core.brand_dna.auth_views import provision_tenant

    user = django_user_model.objects.create_user(
        email='new@test.com', username='new@test.com', password='pass1234'
    )
    tenant = provision_tenant(user)

    assert tenant is not None
    assert tenant.name == 'new@test.com'
    assert tenant.status == 'active'
    user.refresh_from_db()
    assert user.tenant == tenant
    assert Subscription.objects.filter(tenant=tenant, plan=free_plan).exists()


def test_provision_tenant_is_idempotent(free_plan, django_user_model):
    from core.brand_dna.auth_views import provision_tenant

    user = django_user_model.objects.create_user(
        email='existing@test.com', username='existing@test.com', password='pass1234'
    )
    tenant1 = provision_tenant(user)
    tenant2 = provision_tenant(user)

    assert tenant1.pk == tenant2.pk
    assert TenantModel.objects.filter(name='existing@test.com').count() == 1


def test_verify_email_creates_tenant(free_plan, django_user_model):
    from core.tenant_management.models import EmailVerificationToken
    from django.contrib.auth.hashers import make_password

    token = EmailVerificationToken.objects.create(
        email='verify@test.com',
        tenant_name='',
        user_data={'password': make_password('TestPass123!'), 'invitation_code': ''},
    )
    c = Client()
    response = c.get(f'/auth/verify/{token.token}/')

    user = django_user_model.objects.get(email='verify@test.com')
    assert user.tenant is not None
    assert Subscription.objects.filter(tenant=user.tenant).exists()


def test_google_callback_creates_tenant(free_plan, django_user_model):
    c = Client()
    session = c.session
    session['google_oauth_state'] = 'test-state'
    session['google_oauth_code_verifier'] = 'test-verifier'
    session.save()

    mock_id_info = {
        'email': 'google@test.com',
        'name': 'Google User',
    }

    with patch('core.brand_dna.auth_views.Flow') as MockFlow, \
         patch('core.brand_dna.auth_views.id_token') as mock_id_token, \
         patch('core.brand_dna.auth_views.google_requests'):
        mock_flow = MockFlow.from_client_config.return_value
        mock_flow.credentials.id_token = 'fake-token'
        mock_id_token.verify_oauth2_token.return_value = mock_id_info

        response = c.get('/auth/google/callback/?state=test-state&code=test-code')

    user = django_user_model.objects.get(email='google@test.com')
    assert user.tenant is not None
    assert user.tenant.name == 'google@test.com'
    assert Subscription.objects.filter(tenant=user.tenant).exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_tenant_provisioning.py -v`
Expected: FAIL — `ImportError: cannot import name 'provision_tenant'`

- [ ] **Step 3: Implement provision_tenant helper**

In `core/brand_dna/auth_views.py`, add after the existing imports (around line 18):

```python
def provision_tenant(user):
    """Create a TenantModel + Subscription(Free) for the user if they don't have one."""
    from core.tenant_management.models import TenantModel, Plan, Subscription

    if user.tenant is not None:
        return user.tenant

    tenant = TenantModel.objects.create(name=user.email, status='active')
    free_plan = Plan.objects.filter(name='Free').first()
    if free_plan:
        Subscription.objects.create(tenant=tenant, plan=free_plan)
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    return tenant
```

- [ ] **Step 4: Wire provision_tenant into verify_email_view**

In `core/brand_dna/auth_views.py`, in `verify_email_view` (around line 192, after `user.save(update_fields=['password', 'email_verified'])`), add:

```python
    provision_tenant(user)
```

Place it before the invitation code block (before line 194 `invitation_code_str = user_data.get(...)`).

- [ ] **Step 5: Wire provision_tenant into google_callback_view**

In `core/brand_dna/auth_views.py`, in `google_callback_view` (around line 416, inside the `if created:` block, after `user.save(update_fields=['password', 'email_verified'])`), add:

```python
        provision_tenant(user)
```

Place it before the Group assignment (before line 417 `from django.contrib.auth.models import Group`).

- [ ] **Step 6: Run tests to verify they pass**

Run: `docker compose exec backend pytest core/brand_dna/tests/test_tenant_provisioning.py -v`
Expected: 4 PASSED

- [ ] **Step 7: Run existing tests to check for regressions**

Run: `docker compose exec backend pytest core/brand_dna/tests/ -v`
Expected: All existing tests still pass.

- [ ] **Step 8: Commit**

```bash
git add core/brand_dna/auth_views.py core/brand_dna/tests/test_tenant_provisioning.py
GIT_EDITOR=true git commit -m "feat: auto-provision tenant on user registration (email + Google OAuth)"
```

---

### Task 2: Data migration — backfill tenants for existing users

**Files:**
- Create: `core/tenant_management/migrations/0015_backfill_user_tenants.py`

**Interfaces:**
- Consumes: `TenantModel`, `Subscription`, `Plan`, `User` models via `apps.get_model()`
- Produces: All existing users without a tenant get one assigned with a Free subscription.

- [ ] **Step 1: Write the data migration**

Create `core/tenant_management/migrations/0015_backfill_user_tenants.py`:

```python
from django.db import migrations


def backfill_tenants(apps, schema_editor):
    User = apps.get_model('tenant_management', 'User')
    TenantModel = apps.get_model('tenant_management', 'TenantModel')
    Subscription = apps.get_model('tenant_management', 'Subscription')
    Plan = apps.get_model('tenant_management', 'Plan')

    free_plan = Plan.objects.filter(name='Free').first()
    if not free_plan:
        return

    for user in User.objects.filter(tenant__isnull=True):
        tenant = TenantModel.objects.create(name=user.email, status='active')
        Subscription.objects.create(tenant=tenant, plan=free_plan)
        user.tenant = tenant
        user.save(update_fields=['tenant'])


def reverse_backfill(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('tenant_management', '0014_seed_groups_and_plans'),
    ]

    operations = [
        migrations.RunPython(backfill_tenants, reverse_backfill),
    ]
```

- [ ] **Step 2: Run the migration**

Run: `docker compose exec backend python manage.py migrate tenant_management`
Expected: `Applying tenant_management.0015_backfill_user_tenants... OK`

- [ ] **Step 3: Verify migration worked**

Run: `docker compose exec backend python manage.py shell -c "from core.tenant_management.models import User; print('Users without tenant:', User.objects.filter(tenant__isnull=True).count())"`
Expected: `Users without tenant: 0`

- [ ] **Step 4: Commit**

```bash
git add core/tenant_management/migrations/0015_backfill_user_tenants.py
GIT_EDITOR=true git commit -m "feat: data migration to backfill tenants for existing users"
```

---

### Task 3: Rewrite SessionTimeoutMiddleware for Django sessions

**Files:**
- Modify: `core/shared/middleware/session_timeout.py` (full rewrite)
- Create: `core/shared/tests/test_session_timeout.py`

**Interfaces:**
- Consumes: `request.user.is_authenticated`, `request.session`
- Produces: Middleware class `SessionTimeoutMiddleware` that flushes session after 1800s of inactivity and redirects to `/auth/login/?reason=inactivity`. Sets `request.session['last_activity']` on every authenticated request.

- [ ] **Step 1: Write failing tests**

Create `core/shared/tests/test_session_timeout.py`:

```python
import time
import pytest
from unittest.mock import patch
from django.test import Client, override_settings

pytestmark = pytest.mark.django_db


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(
        email='timeout@test.com', username='timeout@test.com', password='pass1234'
    )


def test_session_sets_last_activity(user):
    c = Client()
    c.force_login(user)
    c.get('/dashboard/')
    session = c.session
    assert 'last_activity' in session


def test_session_not_flushed_when_active(user):
    c = Client()
    c.force_login(user)
    c.get('/dashboard/')
    response = c.get('/dashboard/')
    assert response.status_code == 200


def test_session_flushed_on_inactivity(user):
    c = Client()
    c.force_login(user)
    c.get('/dashboard/')

    session = c.session
    session['last_activity'] = time.time() - 1900
    session.save()

    response = c.get('/dashboard/')
    assert response.status_code == 302
    assert '/auth/login/' in response.url
    assert 'reason=inactivity' in response.url


def test_unauthenticated_request_passes_through():
    c = Client()
    response = c.get('/')
    assert response.status_code == 302
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec backend pytest core/shared/tests/test_session_timeout.py -v`
Expected: FAIL — the current middleware looks for JWT Bearer tokens and doesn't set `last_activity` in session.

- [ ] **Step 3: Rewrite SessionTimeoutMiddleware**

Replace the entire contents of `core/shared/middleware/session_timeout.py` with:

```python
import time
from django.shortcuts import redirect


class SessionTimeoutMiddleware:
    INACTIVITY_TIMEOUT = 1800  # 30 minutes

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, 'user') and request.user.is_authenticated:
            last = request.session.get('last_activity')
            if last is not None:
                idle = time.time() - last
                if idle > self.INACTIVITY_TIMEOUT:
                    request.session.flush()
                    return redirect('/auth/login/?reason=inactivity')
            request.session['last_activity'] = time.time()
        return self.get_response(request)
```

This removes `SessionCleanupMiddleware` (Django's `clearsessions` management command handles that).

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec backend pytest core/shared/tests/test_session_timeout.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Run existing tests for regressions**

Run: `docker compose exec backend pytest core/shared/tests/ -v`
Expected: All pass.

- [ ] **Step 6: Commit**

```bash
git add core/shared/middleware/session_timeout.py core/shared/tests/test_session_timeout.py
GIT_EDITOR=true git commit -m "refactor: rewrite SessionTimeoutMiddleware for Django sessions (drop JWT dependency)"
```

---

### Task 4: Adapt TenantIsolationMiddleware with public-path whitelist

**Files:**
- Modify: `core/shared/middleware/tenant_isolation.py` (modify `TenantIsolationMiddleware.__call__`)
- Create: `core/shared/tests/test_tenant_isolation.py`

**Interfaces:**
- Consumes: `request.user`, `request.path`
- Produces: Middleware class `TenantIsolationMiddleware` that:
  - Skips public paths (see Global Constraints for the list)
  - Skips superusers on `/admin/`
  - Returns 403 JSON for authenticated users without tenant on protected routes
  - Sets `request.tenant_id` for authenticated users with tenant

- [ ] **Step 1: Write failing tests**

Create `core/shared/tests/test_tenant_isolation.py`:

```python
import pytest
from django.test import Client
from core.tenant_management.models import TenantModel, Plan, Subscription

pytestmark = pytest.mark.django_db


@pytest.fixture
def free_plan():
    plan, _ = Plan.objects.get_or_create(
        name='Free',
        defaults={
            'max_calendars_per_week': 2,
            'max_post_regenerations': 2,
            'max_post_edits': 2,
            'price': 0,
        },
    )
    return plan


@pytest.fixture
def user_with_tenant(django_user_model, free_plan):
    user = django_user_model.objects.create_user(
        email='tenant@test.com', username='tenant@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=user.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=free_plan)
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    return user


@pytest.fixture
def user_without_tenant(django_user_model):
    return django_user_model.objects.create_user(
        email='notenant@test.com', username='notenant@test.com', password='pass1234'
    )


def test_public_path_passes_through():
    c = Client()
    response = c.get('/')
    assert response.status_code != 403


def test_auth_path_passes_through():
    c = Client()
    response = c.get('/auth/login/')
    assert response.status_code == 200


def test_authenticated_without_tenant_gets_403(user_without_tenant):
    c = Client()
    c.force_login(user_without_tenant)
    response = c.get('/dashboard/')
    assert response.status_code == 403


def test_authenticated_with_tenant_passes(user_with_tenant):
    c = Client()
    c.force_login(user_with_tenant)
    response = c.get('/dashboard/')
    assert response.status_code == 200


def test_superuser_admin_passes_without_tenant(django_user_model):
    admin = django_user_model.objects.create_superuser(
        email='admin@test.com', password='pass1234'
    )
    c = Client()
    c.force_login(admin)
    response = c.get('/admin/')
    assert response.status_code in (200, 301, 302)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec backend pytest core/shared/tests/test_tenant_isolation.py -v`
Expected: FAIL — middleware is not yet in MIDDLEWARE list so won't trigger the 403.

- [ ] **Step 3: Add public-path whitelist to TenantIsolationMiddleware**

In `core/shared/middleware/tenant_isolation.py`, modify the `TenantIsolationMiddleware` class. Replace the `__call__` method:

```python
class TenantIsolationMiddleware:
    PUBLIC_PATH_PREFIXES = (
        '/', '/auth/', '/health/', '/admin/', '/static/', '/media/',
        '/metrics', '/favicon',
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if self._is_public_path(request.path):
            return self.get_response(request)

        if hasattr(request, 'user') and request.user.is_authenticated:
            tenant_response = self._enforce_tenant_isolation(request)
            if tenant_response:
                return tenant_response

        return self.get_response(request)

    def _is_public_path(self, path):
        if path == '/':
            return True
        for prefix in self.PUBLIC_PATH_PREFIXES[1:]:
            if path.startswith(prefix):
                return True
        return False
```

Keep the existing `_enforce_tenant_isolation` method but clean up the excessive debug logging. Replace the method body:

```python
    def _enforce_tenant_isolation(self, request):
        user = request.user

        if not hasattr(user, 'tenant') or not user.tenant:
            if user.is_superuser:
                return None
            return JsonResponse({
                'error': 'Access forbidden',
                'message': 'User is not associated with any tenant',
            }, status=403)

        request.tenant_id = str(user.tenant.id)
        return None
```

Remove the `_record_security_event` method — not needed for this scope. Keep the `tenant_required` decorator, `enforce_tenant_isolation` decorator, and `TenantQuerySetMixin` class as-is (they're not activated yet but may be used later).

- [ ] **Step 4: Run tests to verify they still fail (middleware not in settings yet)**

Run: `docker compose exec backend pytest core/shared/tests/test_tenant_isolation.py::test_authenticated_without_tenant_gets_403 -v`
Expected: Still FAIL — middleware not in MIDDLEWARE list.

- [ ] **Step 5: Activate both middlewares in settings.py**

In `saas_chatbot/settings.py`, add the two middlewares after `OTPMiddleware` (line 260) and before `MessageMiddleware`:

```python
    'core.shared.middleware.tenant_isolation.TenantIsolationMiddleware',
    'core.shared.middleware.session_timeout.SessionTimeoutMiddleware',
```

The full MIDDLEWARE list should now be:
```python
MIDDLEWARE = [
    'django_prometheus.middleware.PrometheusBeforeMiddleware',
    'core.shared.middleware.security.HostHeaderValidationMiddleware',
    'core.shared.middleware.request_limits.RequestSizeLimitMiddleware',
    'core.shared.middleware.request_limits.RequestTimeoutMiddleware',
    'core.shared.middleware.request_limits.RequestBodyValidationMiddleware',
    'core.shared.middleware.security.HTTPSRedirectMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'core.shared.middleware.security.SecurityHeadersMiddleware',
    'core.shared.middleware.request_limits.SecurityHeadersEnforcementMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django_otp.middleware.OTPMiddleware',
    'core.shared.middleware.tenant_isolation.TenantIsolationMiddleware',
    'core.shared.middleware.session_timeout.SessionTimeoutMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_prometheus.middleware.PrometheusAfterMiddleware',
]
```

- [ ] **Step 6: Run tenant isolation tests**

Run: `docker compose exec backend pytest core/shared/tests/test_tenant_isolation.py -v`
Expected: 5 PASSED

- [ ] **Step 7: Run ALL tests for regressions**

Run: `docker compose exec backend pytest -v`
Expected: All pass. If some tests fail because users now need tenants, those tests need to be fixed — add tenant creation to their fixtures. Common pattern:

```python
@pytest.fixture
def user(django_user_model):
    from core.tenant_management.models import TenantModel, Plan, Subscription
    u = django_user_model.objects.create_user(
        email='test@test.com', username='test@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=u.email, status='active')
    plan, _ = Plan.objects.get_or_create(name='Free', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    Subscription.objects.create(tenant=tenant, plan=plan)
    u.tenant = tenant
    u.save(update_fields=['tenant'])
    return u
```

Fix any broken test fixtures to include tenant. This is expected — the middleware now enforces tenant on all protected routes.

- [ ] **Step 8: Rebuild and test containers**

Run: `docker compose up --force-recreate --no-deps backend rqworker nginx`

Verify in browser:
1. Visit `/` — landing page loads (public path)
2. Visit `/auth/login/` — login page loads (public path)
3. Log in with existing user — redirects to `/dashboard/` (user now has tenant from migration)
4. Visit `/dashboard/` — works normally

- [ ] **Step 9: Commit**

```bash
git add core/shared/middleware/tenant_isolation.py core/shared/tests/test_tenant_isolation.py saas_chatbot/settings.py
GIT_EDITOR=true git commit -m "feat: activate TenantIsolation + SessionTimeout middlewares with public-path whitelist"
```

---

### Task 5: Fix existing test fixtures for tenant requirement

**Files:**
- Modify: `core/brand_dna/tests/test_views.py` (update `user` fixture)
- Modify: Any other test files that break due to middleware enforcement

**Interfaces:**
- Consumes: `TenantModel`, `Subscription`, `Plan` from `core.tenant_management.models`
- Produces: All existing tests pass with the tenant middleware active.

- [ ] **Step 1: Run all tests and collect failures**

Run: `docker compose exec backend pytest -v 2>&1 | grep -E "FAILED|ERROR"`
Expected: List of tests failing due to missing tenant on user fixtures.

- [ ] **Step 2: Fix test_views.py user fixture**

In `core/brand_dna/tests/test_views.py`, update the `user` fixture (line 15):

```python
@pytest.fixture
def user(django_user_model):
    from core.tenant_management.models import TenantModel, Plan, Subscription
    u = django_user_model.objects.create_user(
        username='feedback@test.com', email='feedback@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=u.email, status='active')
    plan, _ = Plan.objects.get_or_create(name='Free', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    Subscription.objects.create(tenant=tenant, plan=plan)
    u.tenant = tenant
    u.save(update_fields=['tenant'])
    return u
```

Also update `test_status_api_blocks_other_user` — the `other` user fixture (line 104):

```python
def test_status_api_blocks_other_user(user, django_user_model):
    from core.tenant_management.models import TenantModel, Plan, Subscription
    other = django_user_model.objects.create_user(
        username='other@test.com', email='other@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=other.email, status='active')
    plan, _ = Plan.objects.get_or_create(name='Free', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    Subscription.objects.create(tenant=tenant, plan=plan)
    other.tenant = tenant
    other.save(update_fields=['tenant'])
    # ... rest of test stays the same
```

And `test_calendar_feedback_api_requires_ownership` — the `other_user` fixture (line 201):

```python
def test_calendar_feedback_api_requires_ownership(client, django_user_model, job_with_calendar):
    from core.tenant_management.models import TenantModel, Plan, Subscription
    other_user = django_user_model.objects.create_user(
        username='other@test.com', email='other@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=other_user.email, status='active')
    plan, _ = Plan.objects.get_or_create(name='Free', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    Subscription.objects.create(tenant=tenant, plan=plan)
    other_user.tenant = tenant
    other_user.save(update_fields=['tenant'])
    # ... rest of test stays the same
```

- [ ] **Step 3: Fix any other failing test files**

Look at the failure list from Step 1. For each failing test file, apply the same pattern: any test that creates a user and calls `force_login` or authenticates must also create a tenant for that user. Common files to check:
- `core/tenant_management/tests/test_auth_security.py`
- `core/tenant_management/tests/test_invitation_code.py`
- `core/shared/tests/test_security_middleware.py`

- [ ] **Step 4: Run ALL tests to verify everything passes**

Run: `docker compose exec backend pytest -v`
Expected: ALL tests pass.

- [ ] **Step 5: Commit**

```bash
git add -u
GIT_EDITOR=true git commit -m "fix: update test fixtures to include tenant for middleware enforcement"
```
