# Roles, Onboarding y Notificaciones — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a role system (admin/tester/user), invitation codes for testers, email-verified registration, admin notifications on new user, and Django Admin with 2FA.

**Architecture:** Django Groups for roles mapped to Plans via `get_user_plan()`. `InvitationCode` model for tester upgrades. `EmailVerificationToken` (existing) for magic-link email verification. `django-otp` + TOTP for Django Admin 2FA. Mailgun (already configured) for all emails.

**Tech Stack:** Django 5.2, django-otp, qrcode, Mailgun/anymail, Django Admin, PostgreSQL, Redis.

## Global Constraints

- All tests run inside Docker: `docker compose exec -T backend python -m pytest <path> -v`
- Commits use: `GIT_EDITOR=true git commit -m "msg"` — NEVER heredoc
- `AUTH_USER_MODEL = 'tenant_management.User'`
- UI text in Spanish, code identifiers in English
- `MAX_REGISTERED_USERS=30` — unchanged
- Existing test suite: 5 pre-existing failures are expected and not caused by this work

## File Structure

| File | Responsibility |
|------|---------------|
| `core/tenant_management/models.py` | Add `InvitationCode` model with `generate_code()` and `redeem(user)` |
| `core/tenant_management/migrations/0013_invitationcode_groups_tester_plan.py` | Schema migration for InvitationCode |
| `core/tenant_management/migrations/0014_seed_groups_and_plans.py` | Data migration: groups, Tester plan, assign existing users |
| `core/tenant_management/admin.py` | All ModelAdmin registrations + OTPAdminSite |
| `core/brand_dna/rate_limits.py` | Update `get_user_plan()` to resolve by group |
| `core/brand_dna/auth_forms.py` | Add `invitation_code` field to RegisterForm |
| `core/brand_dna/auth_views.py` | Refactor register, add verify_email, apply_code, notify_admin |
| `core/brand_dna/urls.py` | Add verify and apply-code routes |
| `core/brand_dna/templates/brand_dna/auth/register.html` | Add invitation code field |
| `core/brand_dna/templates/brand_dna/auth/verify_pending.html` | New "check your email" page |
| `core/brand_dna/templates/brand_dna/dashboard.html` | Add invitation code banner |
| `saas_chatbot/settings.py` | Add django-otp to INSTALLED_APPS/MIDDLEWARE, ADMIN_NOTIFICATION_EMAIL |
| `saas_chatbot/urls.py` | Replace admin.site with OTPAdminSite |
| `requirements.txt` | Add django-otp, qrcode |
| `core/tenant_management/tests/test_invitation_code.py` | Tests for InvitationCode model |
| `core/brand_dna/tests/test_rate_limits.py` | Tests for get_user_plan with groups |
| `core/brand_dna/tests/test_auth_views.py` | Tests for register/verify/apply-code/notify flows |
| `core/tenant_management/tests/test_admin.py` | Tests for admin access and 2FA |

---

### Task 1: InvitationCode model + data migration

**Files:**
- Modify: `core/tenant_management/models.py` (add InvitationCode after SecurityEvent)
- Create: `core/tenant_management/tests/test_invitation_code.py`
- Auto-generated: `core/tenant_management/migrations/0013_invitationcode.py`
- Create: `core/tenant_management/migrations/0014_seed_groups_and_plans.py`

**Interfaces:**
- Consumes: `User` model (for `created_by` FK and `redeem()` group assignment)
- Produces: `InvitationCode` model with `generate_code() -> str`, `is_valid() -> bool`, `redeem(user: User) -> bool`

- [ ] **Step 1: Write tests for InvitationCode**

Create `core/tenant_management/tests/test_invitation_code.py`:

```python
import pytest
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.utils import timezone
from core.tenant_management.models import InvitationCode

User = get_user_model()


@pytest.fixture
def admin_user(db):
    return User.objects.create_user(
        email='admin@test.com', password='testpass123!', username='admin@test.com'
    )


@pytest.fixture
def regular_user(db):
    return User.objects.create_user(
        email='user@test.com', password='testpass123!', username='user@test.com'
    )


@pytest.fixture
def tester_group(db):
    return Group.objects.create(name='tester')


@pytest.fixture
def user_group(db):
    return Group.objects.create(name='user')


class TestInvitationCodeGeneration:
    def test_generate_code_format(self):
        code = InvitationCode.generate_code()
        assert code.startswith('COSMIC-')
        assert len(code) == 13  # COSMIC- (7) + 6 chars
        suffix = code[7:]
        allowed = set('ABCDEFGHJKMNPQRSTUVWXYZ23456789')
        assert all(c in allowed for c in suffix)

    def test_generate_code_unique(self):
        codes = {InvitationCode.generate_code() for _ in range(50)}
        assert len(codes) == 50


class TestInvitationCodeModel:
    def test_create_code(self, admin_user):
        code = InvitationCode.objects.create(created_by=admin_user)
        assert code.code.startswith('COSMIC-')
        assert code.target_group == 'tester'
        assert code.max_uses == 1
        assert code.times_used == 0
        assert code.is_active is True

    def test_is_valid_active_code(self, admin_user):
        code = InvitationCode.objects.create(created_by=admin_user)
        assert code.is_valid() is True

    def test_is_valid_inactive_code(self, admin_user):
        code = InvitationCode.objects.create(created_by=admin_user, is_active=False)
        assert code.is_valid() is False

    def test_is_valid_expired_code(self, admin_user):
        code = InvitationCode.objects.create(
            created_by=admin_user,
            expires_at=timezone.now() - timedelta(hours=1),
        )
        assert code.is_valid() is False

    def test_is_valid_exhausted_code(self, admin_user):
        code = InvitationCode.objects.create(
            created_by=admin_user, max_uses=1, times_used=1
        )
        assert code.is_valid() is False

    def test_is_valid_unlimited_uses(self, admin_user):
        code = InvitationCode.objects.create(
            created_by=admin_user, max_uses=0, times_used=999
        )
        assert code.is_valid() is True


class TestInvitationCodeRedeem:
    def test_redeem_assigns_group(self, admin_user, regular_user, tester_group, user_group):
        regular_user.groups.add(user_group)
        code = InvitationCode.objects.create(created_by=admin_user)
        result = code.redeem(regular_user)
        assert result is True
        assert regular_user.groups.filter(name='tester').exists()
        assert not regular_user.groups.filter(name='user').exists()
        assert code.times_used == 1

    def test_redeem_invalid_code_returns_false(self, admin_user, regular_user):
        code = InvitationCode.objects.create(
            created_by=admin_user, is_active=False
        )
        result = code.redeem(regular_user)
        assert result is False
        assert code.times_used == 0

    def test_redeem_custom_target_group(self, admin_user, regular_user, db):
        Group.objects.create(name='admin')
        code = InvitationCode.objects.create(
            created_by=admin_user, target_group='admin'
        )
        result = code.redeem(regular_user)
        assert result is True
        assert regular_user.groups.filter(name='admin').exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec -T backend python -m pytest core/tenant_management/tests/test_invitation_code.py -v`
Expected: FAIL with `ImportError: cannot import name 'InvitationCode'`

- [ ] **Step 3: Add InvitationCode model to models.py**

Add at the end of `core/tenant_management/models.py` (after `SecurityEvent`):

```python
_CODE_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789'


class InvitationCode(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code = models.CharField(max_length=13, unique=True)
    target_group = models.CharField(max_length=20, default='tester')
    max_uses = models.PositiveIntegerField(default=1)
    times_used = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_codes')
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'invitation_codes'
        verbose_name = 'Invitation Code'
        verbose_name_plural = 'Invitation Codes'

    def __str__(self):
        return f"{self.code} ({self.target_group})"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self.generate_code()
        super().save(*args, **kwargs)

    @staticmethod
    def generate_code() -> str:
        suffix = ''.join(secrets.choice(_CODE_ALPHABET) for _ in range(6))
        return f'COSMIC-{suffix}'

    def is_valid(self) -> bool:
        if not self.is_active:
            return False
        if self.expires_at and timezone.now() > self.expires_at:
            return False
        if self.max_uses > 0 and self.times_used >= self.max_uses:
            return False
        return True

    def redeem(self, user) -> bool:
        if not self.is_valid():
            return False
        from django.contrib.auth.models import Group
        target, _ = Group.objects.get_or_create(name=self.target_group)
        user.groups.clear()
        user.groups.add(target)
        self.times_used += 1
        self.save(update_fields=['times_used'])
        return True
```

- [ ] **Step 4: Generate and run schema migration**

Run: `docker compose exec -T backend python manage.py makemigrations tenant_management --name invitationcode`
Run: `docker compose exec -T backend python manage.py migrate`

- [ ] **Step 5: Create data migration for groups and Tester plan**

Create `core/tenant_management/migrations/0014_seed_groups_and_plans.py`:

```python
from django.db import migrations


def seed_groups_and_plans(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Plan = apps.get_model('tenant_management', 'Plan')
    User = apps.get_model('tenant_management', 'User')

    # Create groups
    admin_group, _ = Group.objects.get_or_create(name='admin')
    Group.objects.get_or_create(name='tester')
    user_group, _ = Group.objects.get_or_create(name='user')

    # Create Tester plan
    Plan.objects.get_or_create(
        name='Tester',
        defaults={
            'max_calendars_per_week': 5,
            'max_post_regenerations': 5,
            'max_post_edits': 5,
            'price': 0,
        },
    )

    # Assign superuser to admin group
    for u in User.objects.filter(is_superuser=True):
        u.groups.add(admin_group)

    # Assign non-superusers without groups to user group
    for u in User.objects.filter(is_superuser=False):
        if not u.groups.exists():
            u.groups.add(user_group)


def reverse_seed(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('tenant_management', '0013_invitationcode'),
    ]

    operations = [
        migrations.RunPython(seed_groups_and_plans, reverse_seed),
    ]
```

Run: `docker compose exec -T backend python manage.py migrate`

- [ ] **Step 6: Run tests to verify they pass**

Run: `docker compose exec -T backend python -m pytest core/tenant_management/tests/test_invitation_code.py -v`
Expected: All 10 tests PASS

- [ ] **Step 7: Commit**

```bash
git add core/tenant_management/models.py core/tenant_management/tests/test_invitation_code.py core/tenant_management/migrations/
GIT_EDITOR=true git commit -m "feat: add InvitationCode model + data migration for groups and Tester plan"
```

---

### Task 2: Update get_user_plan() to resolve by group

**Files:**
- Modify: `core/brand_dna/rate_limits.py:6-15` (rewrite `get_user_plan`)
- Create: `core/brand_dna/tests/test_rate_limits.py`

**Interfaces:**
- Consumes: `Plan` model, Django `Group` model, `User.groups` relation
- Produces: `get_user_plan(user) -> Plan` — now resolves via group when no tenant subscription

- [ ] **Step 1: Write tests for group-based plan resolution**

Create `core/brand_dna/tests/test_rate_limits.py`:

```python
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from core.tenant_management.models import Plan, TenantModel, Subscription
from core.brand_dna.rate_limits import get_user_plan

User = get_user_model()


@pytest.fixture
def plans(db):
    free = Plan.objects.get_or_create(name='Free', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2, 'max_post_edits': 2,
    })[0]
    tester = Plan.objects.get_or_create(name='Tester', defaults={
        'max_calendars_per_week': 5, 'max_post_regenerations': 5, 'max_post_edits': 5,
    })[0]
    admin = Plan.objects.get_or_create(name='Admin', defaults={
        'max_calendars_per_week': 99999, 'max_post_regenerations': 99999, 'max_post_edits': 99999,
    })[0]
    return {'free': free, 'tester': tester, 'admin': admin}


@pytest.fixture
def groups(db):
    a, _ = Group.objects.get_or_create(name='admin')
    t, _ = Group.objects.get_or_create(name='tester')
    u, _ = Group.objects.get_or_create(name='user')
    return {'admin': a, 'tester': t, 'user': u}


@pytest.mark.django_db
class TestGetUserPlan:
    def test_user_with_tenant_subscription(self, plans):
        tenant = TenantModel.objects.create(name='T1', status='active')
        Subscription.objects.create(tenant=tenant, plan=plans['admin'])
        user = User.objects.create_user(
            email='sub@test.com', password='test123!', username='sub@test.com', tenant=tenant
        )
        result = get_user_plan(user)
        assert result.name == 'Admin'

    def test_admin_group_gets_admin_plan(self, plans, groups):
        user = User.objects.create_user(
            email='a@test.com', password='test123!', username='a@test.com'
        )
        user.groups.add(groups['admin'])
        result = get_user_plan(user)
        assert result.name == 'Admin'

    def test_tester_group_gets_tester_plan(self, plans, groups):
        user = User.objects.create_user(
            email='t@test.com', password='test123!', username='t@test.com'
        )
        user.groups.add(groups['tester'])
        result = get_user_plan(user)
        assert result.name == 'Tester'

    def test_user_group_gets_free_plan(self, plans, groups):
        user = User.objects.create_user(
            email='u@test.com', password='test123!', username='u@test.com'
        )
        user.groups.add(groups['user'])
        result = get_user_plan(user)
        assert result.name == 'Free'

    def test_no_group_gets_free_plan(self, plans):
        user = User.objects.create_user(
            email='n@test.com', password='test123!', username='n@test.com'
        )
        result = get_user_plan(user)
        assert result.name == 'Free'

    def test_tenant_subscription_takes_priority_over_group(self, plans, groups):
        tenant = TenantModel.objects.create(name='T2', status='active')
        Subscription.objects.create(tenant=tenant, plan=plans['free'])
        user = User.objects.create_user(
            email='p@test.com', password='test123!', username='p@test.com', tenant=tenant
        )
        user.groups.add(groups['admin'])
        result = get_user_plan(user)
        assert result.name == 'Free'
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_rate_limits.py -v`
Expected: `test_admin_group_gets_admin_plan` and `test_tester_group_gets_tester_plan` FAIL (they get Free instead)

- [ ] **Step 3: Update get_user_plan()**

Replace `core/brand_dna/rate_limits.py` `get_user_plan` function (lines 6-15):

```python
def get_user_plan(user):
    from core.tenant_management.models import Plan
    try:
        return user.tenant.subscription.plan
    except Exception:
        pass
    _GROUP_TO_PLAN = {'admin': 'Admin', 'tester': 'Tester', 'user': 'Free'}
    group_names = set(user.groups.values_list('name', flat=True))
    for group_name, plan_name in _GROUP_TO_PLAN.items():
        if group_name in group_names:
            plan = Plan.objects.filter(name=plan_name).first()
            if plan:
                return plan
    return Plan.objects.filter(name='Free').first() or Plan(
        max_calendars_per_week=2,
        max_post_regenerations=2,
        max_post_edits=2,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_rate_limits.py -v`
Expected: All 6 tests PASS

- [ ] **Step 5: Commit**

```bash
git add core/brand_dna/rate_limits.py core/brand_dna/tests/test_rate_limits.py
GIT_EDITOR=true git commit -m "feat: resolve user plan by Django Group when no tenant subscription"
```

---

### Task 3: Email verification flow + register refactor

**Files:**
- Modify: `core/brand_dna/auth_forms.py` (add invitation_code field)
- Modify: `core/brand_dna/auth_views.py` (refactor register_view, add verify_email_view)
- Modify: `core/brand_dna/urls.py` (add verify route)
- Create: `core/brand_dna/templates/brand_dna/auth/verify_pending.html`
- Modify: `core/brand_dna/templates/brand_dna/auth/register.html` (add invitation code field)
- Create: `core/brand_dna/tests/test_auth_views.py`

**Interfaces:**
- Consumes: `InvitationCode.redeem(user)` from Task 1, `EmailVerificationToken` model, `RegisterForm`, `django.core.mail.send_mail`
- Produces: `register_view` (now creates token instead of user), `verify_email_view(request, token)` (creates user + assigns group), `_assign_user_group(user, invitation_code_str)` helper

- [ ] **Step 1: Write tests for email verification flow**

Create `core/brand_dna/tests/test_auth_views.py`:

```python
import pytest
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client
from core.tenant_management.models import EmailVerificationToken, InvitationCode, Plan

User = get_user_model()


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def setup_plans_and_groups(db):
    Plan.objects.get_or_create(name='Free', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2, 'max_post_edits': 2,
    })
    Plan.objects.get_or_create(name='Tester', defaults={
        'max_calendars_per_week': 5, 'max_post_regenerations': 5, 'max_post_edits': 5,
    })
    Group.objects.get_or_create(name='user')
    Group.objects.get_or_create(name='tester')


@pytest.mark.django_db
class TestRegisterView:
    @patch('core.brand_dna.auth_views.send_mail')
    def test_register_creates_token_not_user(self, mock_send, client, setup_plans_and_groups):
        resp = client.post('/auth/register/', {
            'email': 'new@test.com',
            'password1': 'SecurePass123!x',
            'password2': 'SecurePass123!x',
        })
        assert resp.status_code == 200
        assert b'Revisa tu correo' in resp.content
        assert User.objects.filter(email='new@test.com').count() == 0
        assert EmailVerificationToken.objects.filter(email='new@test.com').count() == 1
        mock_send.assert_called_once()

    @patch('core.brand_dna.auth_views.send_mail')
    def test_register_with_invitation_code_stores_in_token(self, mock_send, client, setup_plans_and_groups):
        admin = User.objects.create_user(
            email='adm@test.com', password='test123!', username='adm@test.com'
        )
        code = InvitationCode.objects.create(created_by=admin)
        resp = client.post('/auth/register/', {
            'email': 'invited@test.com',
            'password1': 'SecurePass123!x',
            'password2': 'SecurePass123!x',
            'invitation_code': code.code,
        })
        assert resp.status_code == 200
        token = EmailVerificationToken.objects.get(email='invited@test.com')
        assert token.user_data['invitation_code'] == code.code


@pytest.mark.django_db
class TestVerifyEmailView:
    def test_verify_valid_token_creates_user(self, client, setup_plans_and_groups):
        from django.contrib.auth.hashers import make_password
        token = EmailVerificationToken.objects.create(
            email='verify@test.com',
            tenant_name='',
            user_data={'password': make_password('SecurePass123!x'), 'invitation_code': ''},
        )
        resp = client.get(f'/auth/verify/{token.token}/')
        assert resp.status_code == 302
        user = User.objects.get(email='verify@test.com')
        assert user.groups.filter(name='user').exists()
        token.refresh_from_db()
        assert token.is_used is True

    def test_verify_with_invitation_code_assigns_tester(self, client, setup_plans_and_groups):
        from django.contrib.auth.hashers import make_password
        admin = User.objects.create_user(
            email='adm2@test.com', password='test123!', username='adm2@test.com'
        )
        code = InvitationCode.objects.create(created_by=admin)
        token = EmailVerificationToken.objects.create(
            email='tester@test.com',
            tenant_name='',
            user_data={'password': make_password('SecurePass123!x'), 'invitation_code': code.code},
        )
        resp = client.get(f'/auth/verify/{token.token}/')
        assert resp.status_code == 302
        user = User.objects.get(email='tester@test.com')
        assert user.groups.filter(name='tester').exists()

    def test_verify_used_token_redirects_to_login(self, client, setup_plans_and_groups):
        from django.contrib.auth.hashers import make_password
        token = EmailVerificationToken.objects.create(
            email='used@test.com',
            tenant_name='',
            user_data={'password': make_password('x'), 'invitation_code': ''},
            is_used=True,
        )
        resp = client.get(f'/auth/verify/{token.token}/')
        assert resp.status_code == 302
        assert '/auth/login/' in resp.url
        assert User.objects.filter(email='used@test.com').count() == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_auth_views.py -v`
Expected: FAIL — register still creates user directly, verify_email_view doesn't exist

- [ ] **Step 3: Add invitation_code to RegisterForm**

Replace `core/brand_dna/auth_forms.py` completely:

```python
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

User = get_user_model()


class RegisterForm(forms.Form):
    email = forms.EmailField(label='Correo electrónico')
    password1 = forms.CharField(
        label='Contraseña',
        widget=forms.PasswordInput,
        min_length=8,
        help_text='Mínimo 8 caracteres',
    )
    password2 = forms.CharField(
        label='Confirmar contraseña',
        widget=forms.PasswordInput,
    )
    invitation_code = forms.CharField(
        label='Código de invitación',
        required=False,
        max_length=13,
    )

    def clean_email(self):
        email = self.cleaned_data['email'].lower().strip()
        if User.objects.filter(email=email).exists():
            raise ValidationError('Este correo ya está registrado. ¿Quieres iniciar sesión?')
        return email

    def clean_password1(self):
        password = self.cleaned_data.get('password1')
        if password:
            validate_password(password)
        return password

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get('password1')
        p2 = cleaned.get('password2')
        if p1 and p2 and p1 != p2:
            self.add_error('password2', 'Las contraseñas no coinciden.')
        return cleaned


class LoginForm(forms.Form):
    email = forms.EmailField(label='Correo electrónico')
    password = forms.CharField(label='Contraseña', widget=forms.PasswordInput)
```

- [ ] **Step 4: Refactor register_view and add verify_email_view**

Replace the `register_view` function in `core/brand_dna/auth_views.py` (lines 48-78):

```python
def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    if not _is_registration_open():
        return render(request, 'brand_dna/auth/register.html', {
            'form': None,
            'registration_closed': True,
        })

    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            if not _is_registration_open():
                return render(request, 'brand_dna/auth/register.html', {
                    'form': None,
                    'registration_closed': True,
                })
            email = form.cleaned_data['email']
            password = form.cleaned_data['password1']
            invitation_code = form.cleaned_data.get('invitation_code', '').strip()

            from django.contrib.auth.hashers import make_password
            from core.tenant_management.models import EmailVerificationToken
            token = EmailVerificationToken.objects.create(
                email=email,
                tenant_name='',
                user_data={
                    'password': make_password(password),
                    'invitation_code': invitation_code,
                },
            )

            verify_url = f"{settings.COSMIC_BASE_URL}/auth/verify/{token.token}/"
            from django.core.mail import send_mail
            send_mail(
                'Verifica tu correo — Agente Cosmic',
                f'Haz clic en este enlace para verificar tu correo: {verify_url}',
                settings.DEFAULT_FROM_EMAIL,
                [email],
                html_message=(
                    f'<div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">'
                    f'<h2 style="color:#e94560;">Agente Cosmic</h2>'
                    f'<p>Haz clic en el botón para verificar tu correo y activar tu cuenta:</p>'
                    f'<a href="{verify_url}" style="display:inline-block;padding:14px 28px;'
                    f'background:#e94560;color:#fff;text-decoration:none;border-radius:8px;'
                    f'font-weight:600;">Verificar mi correo</a>'
                    f'<p style="color:#888;font-size:0.85rem;margin-top:24px;">'
                    f'Este enlace expira en 24 horas.</p></div>'
                ),
                fail_silently=False,
            )

            return render(request, 'brand_dna/auth/verify_pending.html', {'email': email})
    else:
        form = RegisterForm()

    return render(request, 'brand_dna/auth/register.html', {'form': form})
```

Add `verify_email_view` function after `register_view` in `core/brand_dna/auth_views.py`:

```python
def verify_email_view(request, token):
    from core.tenant_management.models import EmailVerificationToken, InvitationCode
    from django.contrib.auth.models import Group

    try:
        verification = EmailVerificationToken.objects.get(token=token)
    except EmailVerificationToken.DoesNotExist:
        return redirect('login')

    if not verification.is_valid():
        return redirect('login')

    email = verification.email
    user_data = verification.user_data

    user = User.objects.create_user(
        email=email,
        password=None,
        username=email,
    )
    user.password = user_data['password']
    user.email_verified = True
    user.save(update_fields=['password', 'email_verified'])

    invitation_code_str = user_data.get('invitation_code', '')
    redeemed = False
    if invitation_code_str:
        try:
            code_obj = InvitationCode.objects.get(code=invitation_code_str)
            redeemed = code_obj.redeem(user)
        except InvitationCode.DoesNotExist:
            pass

    if not redeemed:
        user_group, _ = Group.objects.get_or_create(name='user')
        user.groups.add(user_group)

    verification.is_used = True
    verification.save(update_fields=['is_used'])

    return redirect('login')
```

- [ ] **Step 5: Add verify URL to urls.py**

Add to `core/brand_dna/urls.py` in the Auth section:

```python
    path('auth/verify/<str:token>/', auth_views.verify_email_view, name='verify_email'),
```

- [ ] **Step 6: Create verify_pending template**

Create `core/brand_dna/templates/brand_dna/auth/verify_pending.html`:

```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link rel="icon" type="image/svg+xml" href="{% url 'favicon' %}">
  <title>Revisa tu correo — Agente Cosmic</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body { font-family: 'Segoe UI', sans-serif; background: #0d0d1a; color: #f0f0f0; min-height: 100vh; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 20px; }
    .logo { font-size: 1.8rem; font-weight: 700; color: #e94560; margin-bottom: 8px; }
    .card { background: #1a1a2e; width: 100%; max-width: 420px; padding: 40px; border-radius: 16px; text-align: center; }
    .icon { font-size: 3rem; margin-bottom: 16px; }
    h2 { margin-bottom: 12px; font-size: 1.2rem; }
    p { color: #aaa; font-size: 0.95rem; line-height: 1.5; margin-bottom: 8px; }
    .email { color: #e94560; font-weight: 600; }
    .footer-link { text-align: center; margin-top: 24px; font-size: 0.9rem; color: #aaa; }
    .footer-link a { color: #e94560; text-decoration: none; }
  </style>
</head>
<body>
  <div class="logo">Agente Cosmic</div>
  <div class="card">
    <div class="icon">📬</div>
    <h2>Revisa tu correo</h2>
    <p>Enviamos un enlace de verificación a <span class="email">{{ email }}</span></p>
    <p>Haz clic en el enlace para activar tu cuenta. El enlace expira en 24 horas.</p>
    <div class="footer-link">
      <a href="{% url 'login' %}">Volver al inicio de sesión</a>
    </div>
  </div>
</body>
</html>
```

- [ ] **Step 7: Update register.html template with invitation code field**

Add the invitation code field to `core/brand_dna/templates/brand_dna/auth/register.html`. Insert this block after the password2 form-group (after line 86 `{% endif %}` for password2 errors) and before the submit button (line 88):

```html
      <div class="form-group">
        <label for="invitation_code">Código de invitación <span style="font-size:0.75rem;background:#333;color:#aaa;padding:2px 8px;border-radius:10px;margin-left:6px;">opcional</span></label>
        <input type="text" id="invitation_code" name="invitation_code" placeholder="COSMIC-XXXXXX"
               value="{{ form.invitation_code.value|default:'' }}" style="text-transform:uppercase;">
        <div class="hint">Si tienes un código de invitación, ingrésalo para obtener acceso ampliado.</div>
      </div>
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_auth_views.py -v`
Expected: All 5 tests PASS

- [ ] **Step 9: Commit**

```bash
git add core/brand_dna/auth_forms.py core/brand_dna/auth_views.py core/brand_dna/urls.py core/brand_dna/templates/ core/brand_dna/tests/test_auth_views.py
GIT_EDITOR=true git commit -m "feat: email verification flow with magic link + invitation code in register"
```

---

### Task 4: Google OAuth group assignment + dashboard apply-code

**Files:**
- Modify: `core/brand_dna/auth_views.py` (update google_callback_view, add apply_code_view, update dashboard_view)
- Modify: `core/brand_dna/urls.py` (add apply-code route)
- Modify: `core/brand_dna/templates/brand_dna/dashboard.html` (add invitation code banner)
- Modify: `core/brand_dna/tests/test_auth_views.py` (add tests)

**Interfaces:**
- Consumes: `InvitationCode.redeem(user)` from Task 1, Django `Group`
- Produces: `apply_code_view(request)` — POST endpoint for code redemption from dashboard

- [ ] **Step 1: Write tests for apply_code_view**

Add to `core/brand_dna/tests/test_auth_views.py`:

```python
@pytest.mark.django_db
class TestApplyCodeView:
    def test_apply_valid_code_upgrades_to_tester(self, client, setup_plans_and_groups):
        user = User.objects.create_user(
            email='apply@test.com', password='SecurePass123!x', username='apply@test.com'
        )
        Group.objects.get(name='user')
        user.groups.add(Group.objects.get(name='user'))
        admin = User.objects.create_user(
            email='adm3@test.com', password='test123!', username='adm3@test.com'
        )
        code = InvitationCode.objects.create(created_by=admin)

        client.force_login(user)
        resp = client.post('/dashboard/apply-code/', {'code': code.code})
        assert resp.status_code == 302
        user.refresh_from_db()
        assert user.groups.filter(name='tester').exists()

    def test_apply_invalid_code_stays_user(self, client, setup_plans_and_groups):
        user = User.objects.create_user(
            email='bad@test.com', password='SecurePass123!x', username='bad@test.com'
        )
        user.groups.add(Group.objects.get(name='user'))
        client.force_login(user)
        resp = client.post('/dashboard/apply-code/', {'code': 'COSMIC-INVALID'})
        assert resp.status_code == 302
        assert user.groups.filter(name='user').exists()

    def test_apply_code_requires_login(self, client, setup_plans_and_groups):
        resp = client.post('/dashboard/apply-code/', {'code': 'COSMIC-AAAAAA'})
        assert resp.status_code == 302
        assert '/auth/login/' in resp.url
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_auth_views.py::TestApplyCodeView -v`
Expected: FAIL — apply_code_view doesn't exist, URL not found

- [ ] **Step 3: Add apply_code_view and update google_callback_view**

Add `apply_code_view` to `core/brand_dna/auth_views.py` after `dashboard_view`:

```python
@login_required
def apply_code_view(request):
    if request.method != 'POST':
        return redirect('dashboard')
    from core.tenant_management.models import InvitationCode
    code_str = request.POST.get('code', '').strip().upper()
    try:
        code_obj = InvitationCode.objects.get(code=code_str)
        if code_obj.redeem(request.user):
            logger.info(f"Código {code_str} aplicado por {request.user.email}")
        else:
            logger.warning(f"Código inválido {code_str} intentado por {request.user.email}")
    except InvitationCode.DoesNotExist:
        logger.warning(f"Código inexistente {code_str} intentado por {request.user.email}")
    return redirect('dashboard')
```

Update `google_callback_view` — add group assignment after user creation. Replace the block at lines 186-192:

```python
    user, created = User.objects.get_or_create(
        email=email,
        defaults={'username': email, 'display_name': name},
    )
    if created:
        user.set_unusable_password()
        user.email_verified = True
        user.save(update_fields=['password', 'email_verified'])
        from django.contrib.auth.models import Group
        user_group, _ = Group.objects.get_or_create(name='user')
        user.groups.add(user_group)
```

- [ ] **Step 4: Add apply-code URL**

Add to `core/brand_dna/urls.py` in the Auth section:

```python
    path('dashboard/apply-code/', auth_views.apply_code_view, name='apply_code'),
```

- [ ] **Step 5: Update dashboard template with invitation code banner**

Add invitation code banner to `core/brand_dna/templates/brand_dna/dashboard.html`. Insert this block after the calendars counter div (after line 68, before `{% if jobs %}`):

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
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_auth_views.py -v`
Expected: All 8 tests PASS

- [ ] **Step 7: Commit**

```bash
git add core/brand_dna/auth_views.py core/brand_dna/urls.py core/brand_dna/templates/brand_dna/dashboard.html core/brand_dna/tests/test_auth_views.py
GIT_EDITOR=true git commit -m "feat: Google OAuth group assignment + dashboard apply-code for invitation codes"
```

---

### Task 5: Admin notifications on new user registration

**Files:**
- Modify: `core/brand_dna/auth_views.py` (add notify_admin_new_user, call from verify and OAuth)
- Modify: `saas_chatbot/settings.py` (add ADMIN_NOTIFICATION_EMAIL)
- Modify: `core/brand_dna/tests/test_auth_views.py` (add notification tests)

**Interfaces:**
- Consumes: `django.core.mail.send_mail`, `settings.ADMIN_NOTIFICATION_EMAIL`, `settings.COSMIC_BASE_URL`
- Produces: `notify_admin_new_user(user: User, invitation_code: str | None) -> None`

- [ ] **Step 1: Write tests for admin notifications**

Add to `core/brand_dna/tests/test_auth_views.py`:

```python
@pytest.mark.django_db
class TestNotifyAdmin:
    @patch('core.brand_dna.auth_views.send_mail')
    def test_verify_email_sends_admin_notification(self, mock_send, client, setup_plans_and_groups):
        from django.contrib.auth.hashers import make_password
        token = EmailVerificationToken.objects.create(
            email='notify@test.com',
            tenant_name='',
            user_data={'password': make_password('SecurePass123!x'), 'invitation_code': ''},
        )
        client.get(f'/auth/verify/{token.token}/')
        assert mock_send.call_count == 1
        call_args = mock_send.call_args
        assert 'notify@test.com' in call_args[0][0]  # subject contains email

    @patch('core.brand_dna.auth_views.send_mail')
    def test_notify_admin_includes_invitation_code(self, mock_send, client, setup_plans_and_groups):
        from django.contrib.auth.hashers import make_password
        admin = User.objects.create_user(
            email='adm4@test.com', password='test123!', username='adm4@test.com'
        )
        code = InvitationCode.objects.create(created_by=admin)
        token = EmailVerificationToken.objects.create(
            email='codenotify@test.com',
            tenant_name='',
            user_data={'password': make_password('SecurePass123!x'), 'invitation_code': code.code},
        )
        client.get(f'/auth/verify/{token.token}/')
        call_kwargs = mock_send.call_args
        html = call_kwargs[1].get('html_message', '') if call_kwargs[1] else ''
        assert code.code in html
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_auth_views.py::TestNotifyAdmin -v`
Expected: FAIL — `send_mail` not called from verify_email_view (notification not implemented yet)

- [ ] **Step 3: Add notify_admin_new_user and ADMIN_NOTIFICATION_EMAIL setting**

Add `ADMIN_NOTIFICATION_EMAIL` to `saas_chatbot/settings.py` after the `COSMIC_BASE_URL` line (line 519):

```python
ADMIN_NOTIFICATION_EMAIL = get_env('ADMIN_NOTIFICATION_EMAIL', default='contacto.neia@gmail.com')
```

Add `notify_admin_new_user` function to `core/brand_dna/auth_views.py` (after the imports, before `_GOOGLE_SCOPES`):

```python
from django.core.mail import send_mail


def notify_admin_new_user(user, invitation_code=None):
    try:
        admin_email = settings.ADMIN_NOTIFICATION_EMAIL
        group_name = user.groups.first().name if user.groups.exists() else 'user'
        code_info = f'<p><strong>Código usado:</strong> {invitation_code}</p>' if invitation_code else ''
        admin_url = f'{settings.COSMIC_BASE_URL}/admin/tenant_management/user/{user.pk}/change/'
        send_mail(
            f'[Agente Cosmic] Nuevo usuario verificado — {user.email}',
            f'Nuevo usuario: {user.email} (rol: {group_name})',
            settings.DEFAULT_FROM_EMAIL,
            [admin_email],
            html_message=(
                f'<div style="font-family:sans-serif;max-width:480px;margin:0 auto;padding:32px;">'
                f'<h2 style="color:#e94560;">Nuevo usuario en Agente Cosmic</h2>'
                f'<p><strong>Email:</strong> {user.email}</p>'
                f'<p><strong>Rol:</strong> {group_name}</p>'
                f'{code_info}'
                f'<p><strong>Fecha:</strong> {user.date_joined.strftime("%Y-%m-%d %H:%M")}</p>'
                f'<a href="{admin_url}" style="display:inline-block;padding:12px 24px;'
                f'background:#e94560;color:#fff;text-decoration:none;border-radius:8px;'
                f'font-weight:600;margin-top:12px;">Ver en Admin</a></div>'
            ),
            fail_silently=True,
        )
    except Exception as e:
        logger.error(f'Admin notification failed: {e}')
```

- [ ] **Step 4: Call notify_admin from verify_email_view and google_callback_view**

In `verify_email_view`, add this call right before the final `return redirect('login')`:

```python
    notify_admin_new_user(user, invitation_code=invitation_code_str or None)
```

In `google_callback_view`, add this call inside the `if created:` block (after assigning the group):

```python
        notify_admin_new_user(user)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/test_auth_views.py -v`
Expected: All 10 tests PASS

- [ ] **Step 6: Commit**

```bash
git add core/brand_dna/auth_views.py saas_chatbot/settings.py core/brand_dna/tests/test_auth_views.py
GIT_EDITOR=true git commit -m "feat: notify admin by email on new user registration"
```

---

### Task 6: Django Admin with 2FA (django-otp)

**Files:**
- Modify: `requirements.txt` (add django-otp, qrcode)
- Modify: `saas_chatbot/settings.py` (INSTALLED_APPS, MIDDLEWARE)
- Modify: `saas_chatbot/urls.py` (replace admin.site with OTPAdminSite)
- Create: `core/tenant_management/admin.py`
- Create: `core/tenant_management/tests/test_admin_access.py`

**Interfaces:**
- Consumes: All models (User, InvitationCode, Plan, AnalysisJob, SecurityEvent), `django_otp.admin.OTPAdminSite`
- Produces: Custom `CosmicAdminSite` with 2FA enforcement and 404 for non-staff, all ModelAdmin registrations

- [ ] **Step 1: Write tests for admin access control**

Create `core/tenant_management/tests/test_admin_access.py`:

```python
import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client

User = get_user_model()


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def groups(db):
    Group.objects.get_or_create(name='admin')
    Group.objects.get_or_create(name='tester')
    Group.objects.get_or_create(name='user')


@pytest.mark.django_db
class TestAdminAccess:
    def test_non_staff_gets_404(self, client, groups):
        user = User.objects.create_user(
            email='regular@test.com', password='TestPass123!x', username='regular@test.com'
        )
        user.groups.add(Group.objects.get(name='user'))
        client.force_login(user)
        resp = client.get('/admin/')
        assert resp.status_code == 404

    def test_staff_can_reach_admin_login(self, client, groups):
        resp = client.get('/admin/login/')
        assert resp.status_code == 200

    def test_anonymous_gets_redirect_to_login(self, client):
        resp = client.get('/admin/')
        assert resp.status_code in (302, 404)
```

- [ ] **Step 2: Run tests to verify baseline**

Run: `docker compose exec -T backend python -m pytest core/tenant_management/tests/test_admin_access.py -v`
Expected: `test_non_staff_gets_404` FAILS (currently returns 302 redirect instead of 404)

- [ ] **Step 3: Add dependencies to requirements.txt**

Add to `requirements.txt` (after the `qrcode` or at the end):

```
django-otp>=1.5.0
qrcode>=7.4
```

- [ ] **Step 4: Update settings.py**

Add to `INSTALLED_APPS` in `saas_chatbot/settings.py` (after `'django.contrib.staticfiles'`, before `'rest_framework'`):

```python
    'django_otp',
    'django_otp.plugins.otp_totp',
```

Add to `MIDDLEWARE` in `saas_chatbot/settings.py` (after `'django.contrib.auth.middleware.AuthenticationMiddleware'`):

```python
    'django_otp.middleware.OTPMiddleware',
```

- [ ] **Step 5: Create admin.py with OTPAdminSite and ModelAdmin registrations**

Create `core/tenant_management/admin.py`:

```python
from django.contrib import admin
from django.contrib.auth.models import Group
from django.db.models import Count
from django.http import Http404
from django_otp.admin import OTPAdminSite

from core.brand_dna.models import AnalysisJob
from core.tenant_management.models import (
    InvitationCode, Plan, SecurityEvent, User,
)


class CosmicAdminSite(OTPAdminSite):
    site_header = 'Agente Cosmic Admin'
    site_title = 'Agente Cosmic'
    index_title = 'Panel de administración'

    def has_permission(self, request):
        if not request.user.is_active or not request.user.is_staff:
            return False
        return super().has_permission(request)

    def login(self, request, extra_context=None):
        if request.method == 'GET' and request.user.is_authenticated and not request.user.is_staff:
            raise Http404
        return super().login(request, extra_context)

    def admin_view(self, view, cacheable=False):
        inner = super().admin_view(view, cacheable)

        def wrapper(request, *args, **kwargs):
            if request.user.is_authenticated and not request.user.is_staff:
                raise Http404
            return inner(request, *args, **kwargs)

        wrapper.__name__ = view.__name__
        wrapper.__module__ = view.__module__
        return wrapper


cosmic_admin = CosmicAdminSite(name='cosmic_admin')


class UserAdmin(admin.ModelAdmin):
    list_display = ('email', 'get_groups', 'is_active', 'date_joined', 'get_calendars_count')
    list_filter = ('is_active', 'groups', 'date_joined')
    search_fields = ('email', 'display_name')
    readonly_fields = ('id', 'date_joined', 'last_login')
    filter_horizontal = ('groups',)
    ordering = ('-date_joined',)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(
            calendars_count=Count('analysis_jobs', distinct=True)
        )

    def get_groups(self, obj):
        return ', '.join(g.name for g in obj.groups.all())
    get_groups.short_description = 'Grupos'

    def get_calendars_count(self, obj):
        return obj.calendars_count
    get_calendars_count.short_description = 'Calendarios'
    get_calendars_count.admin_order_field = 'calendars_count'

    actions = ['generate_invitation_codes']

    def generate_invitation_codes(self, request, queryset):
        codes = []
        for user in queryset:
            code = InvitationCode.objects.create(created_by=request.user)
            codes.append(f'{user.email}: {code.code}')
        self.message_user(request, 'Códigos generados: ' + ', '.join(codes))
    generate_invitation_codes.short_description = 'Generar código de invitación para seleccionados'


class InvitationCodeAdmin(admin.ModelAdmin):
    list_display = ('code', 'target_group', 'max_uses', 'times_used', 'is_active', 'expires_at', 'created_by', 'created_at')
    list_filter = ('is_active', 'target_group')
    search_fields = ('code',)
    readonly_fields = ('code', 'times_used', 'created_at')


class PlanAdmin(admin.ModelAdmin):
    list_display = ('name', 'max_calendars_per_week', 'max_post_regenerations', 'max_post_edits', 'price')


class AnalysisJobAdmin(admin.ModelAdmin):
    list_display = ('user', 'business_url', 'status', 'stage', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('email', 'business_url')
    readonly_fields = [f.name for f in AnalysisJob._meta.get_fields() if hasattr(f, 'name')]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class SecurityEventAdmin(admin.ModelAdmin):
    list_display = ('event_type', 'user', 'ip_address', 'severity', 'created_at')
    list_filter = ('event_type', 'severity', 'created_at')
    search_fields = ('description', 'ip_address')
    readonly_fields = [f.name for f in SecurityEvent._meta.get_fields() if hasattr(f, 'name')]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


cosmic_admin.register(User, UserAdmin)
cosmic_admin.register(InvitationCode, InvitationCodeAdmin)
cosmic_admin.register(Plan, PlanAdmin)
cosmic_admin.register(AnalysisJob, AnalysisJobAdmin)
cosmic_admin.register(SecurityEvent, SecurityEventAdmin)
cosmic_admin.register(Group)
```

- [ ] **Step 6: Update urls.py to use CosmicAdminSite**

Replace `saas_chatbot/urls.py` completely:

```python
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from .views import health_check
from core.tenant_management.admin import cosmic_admin

handler400 = 'core.shared.error_handlers.handler400'
handler403 = 'core.shared.error_handlers.handler403'
handler404 = 'core.shared.error_handlers.handler404'
handler500 = 'core.shared.error_handlers.handler500'

urlpatterns = [
    path('health/', health_check, name='health'),
    path('admin/', cosmic_admin.urls),
    path('', include('core.brand_dna.urls')),
]

urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
```

- [ ] **Step 7: Rebuild containers and run migrations**

Run: `docker compose up --build --force-recreate --no-deps -d backend rqworker`
Run: `docker compose exec -T backend python manage.py migrate`
Run: `docker compose exec -T backend python manage.py collectstatic --noinput`

- [ ] **Step 8: Run admin access tests**

Run: `docker compose exec -T backend python -m pytest core/tenant_management/tests/test_admin_access.py -v`
Expected: All 3 tests PASS

- [ ] **Step 9: Run full test suite to verify no regressions**

Run: `docker compose exec -T backend python -m pytest core/brand_dna/tests/ core/content_pipeline/tests/ core/shared/tests/ core/tenant_management/tests/test_invitation_code.py core/tenant_management/tests/test_admin_access.py -q --tb=short`
Expected: Only the 5 pre-existing failures, all new tests pass

- [ ] **Step 10: Commit**

```bash
git add requirements.txt saas_chatbot/settings.py saas_chatbot/urls.py core/tenant_management/admin.py core/tenant_management/tests/test_admin_access.py
GIT_EDITOR=true git commit -m "feat: Django Admin with 2FA via django-otp + ModelAdmin for all entities"
```
