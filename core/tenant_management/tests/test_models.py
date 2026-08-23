import pytest
from django.utils import timezone
from core.tenant_management.models import TenantModel, Plan, Subscription

import secrets
from django.contrib.auth import get_user_model

User = get_user_model()
# Contraseña generada dinámicamente — el repo no hardcodea contraseñas de prueba
_TEST_PWD = f"T3st-{secrets.token_urlsafe(10)}!"

pytestmark = pytest.mark.django_db


def test_subscription_trial_ends_at_defaults_to_none():
    plan = Plan.objects.create(name='Plan Test Trial')
    tenant = TenantModel.objects.create(name='Tenant Test', status='active')
    sub = Subscription.objects.create(tenant=tenant, plan=plan)
    assert sub.trial_ends_at is None


def test_subscription_trial_ends_at_accepts_datetime():
    plan = Plan.objects.create(name='Plan Test Trial 2')
    tenant = TenantModel.objects.create(name='Tenant Test 2', status='active')
    ends_at = timezone.now() + timezone.timedelta(days=7)
    sub = Subscription.objects.create(
        tenant=tenant, plan=plan, status='trialing', trial_ends_at=ends_at,
    )
    sub.refresh_from_db()
    assert sub.status == 'trialing'
    assert sub.trial_ends_at == ends_at


def test_subscription_stripe_fields_default_empty():
    plan = Plan.objects.create(name='Plan Test Stripe Fields')
    tenant = TenantModel.objects.create(name='Tenant Stripe Fields', status='active')
    sub = Subscription.objects.create(tenant=tenant, plan=plan)
    assert sub.stripe_customer_id == ''
    assert sub.stripe_subscription_id == ''
    assert sub.cancel_at_period_end is False


def test_subscription_stripe_fields_accept_values():
    plan = Plan.objects.create(name='Plan Test Stripe Fields 2')
    tenant = TenantModel.objects.create(name='Tenant Stripe Fields 2', status='active')
    sub = Subscription.objects.create(
        tenant=tenant, plan=plan,
        stripe_customer_id='cus_123', stripe_subscription_id='sub_123',
        cancel_at_period_end=True,
    )
    sub.refresh_from_db()
    assert sub.stripe_customer_id == 'cus_123'
    assert sub.stripe_subscription_id == 'sub_123'
    assert sub.cancel_at_period_end is True


def test_subscription_paid_until_defaults_to_none():
    plan = Plan.objects.create(name='Plan Test Paid Until')
    tenant = TenantModel.objects.create(name='Tenant Paid Until', status='active')
    sub = Subscription.objects.create(tenant=tenant, plan=plan)
    assert sub.paid_until is None


def test_subscription_paid_until_accepts_datetime():
    plan = Plan.objects.create(name='Plan Test Paid Until 2')
    tenant = TenantModel.objects.create(name='Tenant Paid Until 2', status='active')
    paid_until = timezone.now() + timezone.timedelta(days=28)
    sub = Subscription.objects.create(tenant=tenant, plan=plan, status='active', paid_until=paid_until)
    sub.refresh_from_db()
    assert sub.status == 'active'
    assert sub.paid_until == paid_until


def test_user_last_reactivation_email_at_defaults_to_none():
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    user = UserModel.objects.create_user(username='reactivacion@test.com', email='reactivacion@test.com', password='pass1234')
    assert user.last_reactivation_email_at is None


def test_plan_stripe_payment_link_url_defaults_empty():
    plan = Plan.objects.create(name='Plan Test Payment Link')
    assert plan.stripe_payment_link_url == ''


def test_plan_stripe_payment_link_url_accepts_value():
    plan = Plan.objects.create(
        name='Plan Test Payment Link 2',
        stripe_payment_link_url='https://buy.stripe.com/founder123',
    )
    plan.refresh_from_db()
    assert plan.stripe_payment_link_url == 'https://buy.stripe.com/founder123'


def test_login_token_expira_a_las_72_horas():
    """La ventana de 72h es la decisión central del spec — si alguien la cambia
    por accidente, este test lo atrapa."""
    from django.utils import timezone
    from core.tenant_management.models import LoginToken
    user = User.objects.create_user(email='magic@ejemplo.com', password=_TEST_PWD)

    antes = timezone.now()
    tok = LoginToken.objects.create(user=user, redirect_to='/dashboard/')
    despues = timezone.now()

    esperado_min = antes + timezone.timedelta(hours=72)
    esperado_max = despues + timezone.timedelta(hours=72)
    assert esperado_min <= tok.expires_at <= esperado_max


def test_login_token_es_valido_antes_de_expirar():
    from django.utils import timezone
    from core.tenant_management.models import LoginToken
    user = User.objects.create_user(email='magic2@ejemplo.com', password=_TEST_PWD)

    tok = LoginToken.objects.create(
        user=user, redirect_to='/dashboard/',
        expires_at=timezone.now() + timezone.timedelta(minutes=1),
    )

    assert tok.is_expired() is False
    assert tok.is_valid() is True


def test_login_token_expirado_no_es_valido():
    from django.utils import timezone
    from core.tenant_management.models import LoginToken
    user = User.objects.create_user(email='magic3@ejemplo.com', password=_TEST_PWD)

    tok = LoginToken.objects.create(
        user=user, redirect_to='/dashboard/',
        expires_at=timezone.now() - timezone.timedelta(seconds=1),
    )

    assert tok.is_expired() is True
    assert tok.is_valid() is False


def test_login_token_no_tiene_is_used():
    """A diferencia de PasswordResetToken y EmailVerificationToken, este token
    es REUTILIZABLE dentro de su ventana (decisión del spec: el prefetch de
    Gmail quemaría un token de un solo uso antes del clic del usuario).
    used_count reemplaza al booleano."""
    from core.tenant_management.models import LoginToken
    campos = {f.name for f in LoginToken._meta.get_fields()}
    assert 'is_used' not in campos
    assert 'used_count' in campos


def test_login_token_arranca_con_used_count_en_cero():
    from core.tenant_management.models import LoginToken
    user = User.objects.create_user(email='magic4@ejemplo.com', password=_TEST_PWD)

    tok = LoginToken.objects.create(user=user, redirect_to='/dashboard/')

    assert tok.used_count == 0
    assert tok.last_used_at is None
    assert tok.last_used_ip is None


def test_login_token_genera_tokens_unicos_y_largos():
    from core.tenant_management.models import LoginToken
    user = User.objects.create_user(email='magic5@ejemplo.com', password=_TEST_PWD)

    t1 = LoginToken.objects.create(user=user, redirect_to='/dashboard/')
    t2 = LoginToken.objects.create(user=user, redirect_to='/dashboard/')

    assert t1.token != t2.token
    # secrets.token_urlsafe(32) produce ~43 caracteres
    assert len(t1.token) >= 40



