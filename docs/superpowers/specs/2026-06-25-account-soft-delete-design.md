# Account Soft Delete Design

## Goal

Allow users to deactivate their account from a "Danger Zone" section in the dashboard. The account is soft-deleted (not destroyed), and can be reactivated if the user re-registers with the same email. Images in GCS are cleaned up after 30 days of deactivation.

## Context

The system uses `AUTH_USER_MODEL = 'tenant_management.User'` (extends `AbstractUser`). Each user has a `TenantModel` with a `Subscription`. Django's built-in `is_active` field on `AbstractUser` already blocks login when `False` — `authenticate()` returns `None` automatically.

Registration happens via two paths: email verification (`verify_email_view`) and Google OAuth (`google_callback_view`), both in `core/brand_dna/auth_views.py`.

## Architecture

```
DEACTIVATION (dashboard → POST /dashboard/delete-account/)
  ├─ User.is_active = False
  ├─ User.deactivated_at = now()
  ├─ TenantModel.status = 'deactivated'
  ├─ Subscription.status = 'canceled'
  ├─ Session flush → redirect to login
  └─ (images remain in GCS for 30 days)

REACTIVATION (register with existing deactivated email)
  ├─ Detect existing User with is_active=False
  ├─ Show reactivation page (not new registration)
  ├─ User.is_active = True
  ├─ User.deactivated_at = None
  ├─ TenantModel.status = 'active'
  ├─ Subscription.status = 'active'
  └─ Login → dashboard (usage history preserved)

IMAGE CLEANUP (management command, daily cron)
  └─ Users with deactivated_at > 30 days ago
       ├─ Delete GCS blobs under posts/{job_uuid}-*
       ├─ Clear AnalysisJob.product_image_paths
       └─ Log cleanup count
```

## Components

### 1. Model change

Add `deactivated_at = models.DateTimeField(null=True, blank=True)` to `User` model. This tracks when the account was deactivated and serves as the 30-day cleanup trigger. Migration required.

No new models. The existing `is_active` (AbstractUser), `TenantModel.status`, and `Subscription.status` fields handle the rest.

### 2. Deactivation view

New view `deactivate_account_view` in `core/brand_dna/auth_views.py`:
- POST only, `@login_required`
- Requires confirmation: POST body must contain `confirmation=ELIMINAR`
- Sets `user.is_active = False`, `user.deactivated_at = now()`
- Sets `user.tenant.status = 'deactivated'`
- Sets `user.tenant.subscription.status = 'canceled'`
- Flushes session via `logout(request)`
- Redirects to `/auth/login/?reason=deactivated`

New URL: `path('dashboard/delete-account/', auth_views.deactivate_account_view, name='deactivate_account')`

### 3. Reactivation in registration flow

**Email registration** (`verify_email_view`): Before creating a new user, check if a deactivated user with that email exists:
```python
existing = User.objects.filter(email=email, is_active=False).first()
if existing:
    # redirect to reactivation page instead of creating new user
```

**Google OAuth** (`google_callback_view`): The existing `User.objects.get_or_create(email=email)` already finds the user. Add check: if found and `is_active=False`, redirect to reactivation page.

**Reactivation page** (`reactivate_account_view`): Shows message "Ya tenias una cuenta con este email. ¿Quieres reactivarla?" with a confirm button. On POST:
- `user.is_active = True`, `user.deactivated_at = None`
- `tenant.status = 'active'`, `subscription.status = 'active'`
- Login and redirect to dashboard

### 4. Dashboard Danger Zone UI

At the bottom of `core/brand_dna/templates/brand_dna/dashboard.html`, add a "Danger Zone" section:
- Red border, warning icon
- Text: "Eliminar mi cuenta — Tu cuenta sera desactivada. Podras reactivarla si te registras de nuevo con el mismo email. Despues de 30 dias, las imagenes generadas seran eliminadas."
- Button: "Eliminar mi cuenta" (red)
- On click: modal/inline confirmation asking to type "ELIMINAR"
- Form POSTs to `/dashboard/delete-account/` with `confirmation=ELIMINAR`

### 5. Login page message

When redirected with `?reason=deactivated`, show: "Tu cuenta fue desactivada exitosamente."

The login page already handles `?reason=inactivity` (from SessionTimeoutMiddleware). Add `deactivated` as another reason.

### 6. Image cleanup management command

New command: `core/tenant_management/management/commands/cleanup_deactivated_images.py`

Runs daily (cron). Logic:
1. Find users where `deactivated_at` is not null AND `deactivated_at < now() - 30 days`
2. For each user, find all `AnalysisJob` records
3. For each job, delete GCS blobs: `posts/{job.id}-day*.png`
4. Clear `job.product_image_paths`, `job.product_image_path`, `job.post_images_paths`, `job.logo_file_path`
5. Delete local media files if they exist
6. Log: "Cleaned up images for user {email}, {N} jobs, {M} blobs deleted"

Does NOT delete the User, Tenant, Subscription, AnalysisJob, BrandDNA, or calendar records.

### 7. Protection against abuse

- Reactivation preserves all usage history — no free tier reset
- `AnalysisJob` records remain, so `can_create_calendar()` still counts past usage
- `deactivated_at` timestamp provides audit trail
- The confirmation step ("type ELIMINAR") prevents accidental deletion

## What does NOT change

- Admin can still see deactivated users in Django Admin
- AnalysisJobs, BrandDNA, calendars, posts — preserved (only GCS images cleaned after 30 days)
- Rate limiting, plan enforcement — unchanged (reactivated users keep their plan)
- Password reset — Django already blocks password reset for `is_active=False` users

## Testing

- **Deactivation:** POST with correct confirmation → user.is_active=False, session ended, redirect
- **Deactivation without confirmation:** POST without "ELIMINAR" → rejected
- **Login blocked after deactivation:** authenticate() returns None
- **Reactivation via email:** register with deactivated email → reactivation page → account restored
- **Reactivation via Google OAuth:** same flow
- **Usage preserved:** reactivated user's AnalysisJob count unchanged
- **Cleanup command:** users deactivated >30 days → GCS blobs deleted, paths cleared
- **Cleanup respects grace period:** users deactivated <30 days → untouched

## Out of scope

- GDPR hard delete (complete data erasure) — future feature if needed for EU compliance
- Email notification on deactivation/reactivation
- Admin-initiated deactivation
- Data export before deletion
