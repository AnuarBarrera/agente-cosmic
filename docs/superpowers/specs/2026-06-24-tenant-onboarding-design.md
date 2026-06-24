# Tenant Onboarding Design

## Goal

Connect every user to a tenant so that data is isolated per business and usage limits apply per subscription plan. Auto-provision tenant + subscription on registration; activate tenant isolation and session timeout middlewares.

## Context

The codebase has a full `tenant_management` app inherited from the miagent fork: `TenantModel`, `Plan`, `Subscription`, `UsageRecord`, plus three dormant middlewares (`TenantIsolation`, `SessionTimeout`, `TenantRateLimiting`) and a JWT-based REST API. None of this is wired up. Users register via `auth_views.py` (Django sessions), and `User.tenant` is always null.

This spec activates the tenant infrastructure for the current Django-session-based web app. The JWT REST API and TenantRateLimitingMiddleware remain dormant.

## Architecture

```
Registration flow (email or Google OAuth)
  └─ auth_views.py
       ├─ Create User (existing)
       ├─ NEW: Create TenantModel(name=user.email, status='active')
       ├─ NEW: Create Subscription(tenant=tenant, plan=free_plan)
       └─ NEW: user.tenant = tenant; user.save()

Request flow (after auth)
  └─ TenantIsolationMiddleware
       ├─ Skip public routes (/, /login/, /register/, /verify-email/, etc.)
       ├─ Skip superuser on /admin/
       ├─ Block authenticated users without tenant → 403
       └─ Set request.tenant_id for downstream use

  └─ SessionTimeoutMiddleware (rewritten for Django sessions)
       ├─ Track last_activity in request.session
       ├─ Inactivity timeout: flush session after 30 min idle
       └─ Absolute timeout: SESSION_COOKIE_AGE (1 hour)
```

## Components

### 1. Tenant auto-provisioning

**Where:** `core/brand_dna/auth_views.py`

**Registration via email** (`verify_email_view`): After creating the user and assigning groups, create tenant + subscription:

```python
from core.tenant_management.models import TenantModel, Plan, Subscription

tenant = TenantModel.objects.create(name=user.email, status='active')
free_plan = Plan.objects.get(name='free')
Subscription.objects.create(tenant=tenant, plan=free_plan)
user.tenant = tenant
user.save(update_fields=['tenant'])
```

**Registration via Google OAuth** (`google_callback`): Same logic, inside the `if created:` block.

**Data migration:** Create tenant + subscription for all existing users without one:

```python
for user in User.objects.filter(tenant__isnull=True):
    tenant = TenantModel.objects.create(name=user.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=free_plan)
    user.tenant = tenant
    user.save(update_fields=['tenant'])
```

### 2. TenantIsolationMiddleware activation

**Where:** `saas_chatbot/settings.py` MIDDLEWARE list

**Position:** After `AuthenticationMiddleware`, before `MessageMiddleware`.

**Adaptations needed in `core/shared/middleware/tenant_isolation.py`:**

- Add a whitelist of public paths that skip tenant check:
  ```python
  PUBLIC_PATHS = [
      '/', '/login/', '/register/', '/verify-email/',
      '/forgot-password/', '/reset-password/',
      '/google/login/', '/google/callback/',
      '/health/', '/admin/', '/static/', '/media/',
      '/metrics',
  ]
  ```
- For paths not in the whitelist, authenticated users without tenant get 403.
- The existing logic for `request.tenant_id` and manipulation detection stays.
- Remove excessive `logger.info` calls (debug noise from development).

### 3. SessionTimeoutMiddleware rewrite

**Where:** `core/shared/middleware/session_timeout.py`

**Current state:** Parses JWT Bearer tokens, looks up `UserSession` model, blacklists JTI on timeout. None of this applies to Django sessions.

**Rewrite to:**

```python
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
                    from django.shortcuts import redirect
                    return redirect('/login/?reason=inactivity')
            request.session['last_activity'] = time.time()
        return self.get_response(request)
```

**Also configure in settings.py:**
```python
SESSION_COOKIE_AGE = 3600  # 1 hour absolute timeout
SESSION_SAVE_EVERY_REQUEST = True  # Refresh cookie on each request
```

**SessionCleanupMiddleware:** Remove. Django's `clearsessions` management command handles expired session cleanup. If periodic cleanup is needed, use a cron/management command, not middleware.

### 4. What does NOT change

- **JWT REST API** (`interfaces/urls.py`, `interfaces/views.py`): Not activated, not modified. Stays dormant.
- **TenantRateLimitingMiddleware** and **APIThrottlingMiddleware**: Stay dormant. Rate limiting already exists per-IP in `auth_views.py`.
- **Frontend templates/JS**: No changes.
- **brand_dna models**: No `tenant` FK added. Isolation via `user` FK → `user.tenant`.
- **Plan enforcement**: Already implemented in views (calendars/week, regenerations, edits). No new enforcement logic.

## Data model changes

No schema changes. All models already exist:
- `TenantModel` — `name`, `status`, `created_at`
- `Subscription` — `tenant` (OneToOne), `plan` (FK), `status`
- `User.tenant` — FK to TenantModel, nullable

Only change: a data migration populates tenant for existing users.

## Middleware order (final)

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
    'core.shared.middleware.tenant_isolation.TenantIsolationMiddleware',  # NEW
    'core.shared.middleware.session_timeout.SessionTimeoutMiddleware',    # NEW
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_prometheus.middleware.PrometheusAfterMiddleware',
]
```

## Testing

- **Unit tests for auto-provisioning:** Verify registration creates tenant + subscription, user.tenant is set.
- **Unit tests for TenantIsolationMiddleware:** Authenticated user without tenant gets 403 on protected route. Public routes pass through. Superuser on /admin/ passes.
- **Unit tests for SessionTimeoutMiddleware:** Session flush on inactivity. Active sessions persist.
- **Data migration test:** Existing users get tenant + subscription.
- **Integration:** Register → analyze → verify data is associated with correct tenant.

## Out of scope

- JWT REST API activation
- TenantRateLimiting / APIThrottling middlewares
- Frontend changes
- New Plan enforcement logic
- Multi-user teams per tenant
- Flow without website URL (community validation item — separate spec)
