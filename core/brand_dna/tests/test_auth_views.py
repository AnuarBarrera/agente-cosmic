import pytest
import secrets
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client
from core.tenant_management.models import (
    EmailVerificationToken, InvitationCode, Plan, TenantModel, Subscription,
)

# Contraseñas generadas dinámicamente — no hardcodeadas en el código fuente
_TEST_PWD = f"T3st-{secrets.token_urlsafe(10)}!"   # ~17 chars, cumple todos los validators
_ADMIN_PWD = f"Adm!{secrets.token_urlsafe(8)}"     # para usuarios admin creados directamente

User = get_user_model()


def _make_tenant(user):
    tenant = TenantModel.objects.create(name=user.email, status='active')
    free, _ = Plan.objects.get_or_create(name='User', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    Subscription.objects.create(tenant=tenant, plan=free)
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    return tenant


@pytest.fixture
def client():
    return Client()


@pytest.fixture
def setup_plans_and_groups(db):
    Plan.objects.get_or_create(name='User', defaults={
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
            'password1': _TEST_PWD,
            'password2': _TEST_PWD,
        })
        assert resp.status_code == 200
        assert b'Revisa tu correo' in resp.content
        assert User.objects.filter(email='new@test.com').count() == 0
        assert EmailVerificationToken.objects.filter(email='new@test.com').count() == 1
        mock_send.assert_called_once()

    @patch('core.brand_dna.auth_views.send_mail')
    def test_register_with_invitation_code_stores_in_token(self, mock_send, client, setup_plans_and_groups):
        admin = User.objects.create_user(
            email='adm@test.com', password=_ADMIN_PWD, username='adm@test.com'
        )
        code = InvitationCode.objects.create(created_by=admin)
        resp = client.post('/auth/register/', {
            'email': 'invited@test.com',
            'password1': _TEST_PWD,
            'password2': _TEST_PWD,
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
            user_data={'password': make_password(_TEST_PWD), 'invitation_code': ''},
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
            email='adm2@test.com', password=_ADMIN_PWD, username='adm2@test.com'
        )
        code = InvitationCode.objects.create(created_by=admin)
        token = EmailVerificationToken.objects.create(
            email='tester@test.com',
            tenant_name='',
            user_data={'password': make_password(_TEST_PWD), 'invitation_code': code.code},
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


@pytest.mark.django_db
class TestNotifyAdmin:
    @patch('core.brand_dna.auth_views.send_mail')
    def test_verify_email_sends_admin_notification(self, mock_send, client, setup_plans_and_groups):
        from django.contrib.auth.hashers import make_password
        token = EmailVerificationToken.objects.create(
            email='notify@test.com',
            tenant_name='',
            user_data={'password': make_password(_TEST_PWD), 'invitation_code': ''},
        )
        client.get(f'/auth/verify/{token.token}/')
        assert mock_send.call_count == 1
        call_args = mock_send.call_args
        assert 'notify@test.com' in call_args[0][0]

    @patch('core.brand_dna.auth_views.send_mail')
    def test_notify_admin_includes_invitation_code(self, mock_send, client, setup_plans_and_groups):
        from django.contrib.auth.hashers import make_password
        admin = User.objects.create_user(
            email='adm4@test.com', password=_ADMIN_PWD, username='adm4@test.com'
        )
        code = InvitationCode.objects.create(created_by=admin)
        token = EmailVerificationToken.objects.create(
            email='codenotify@test.com',
            tenant_name='',
            user_data={'password': make_password(_TEST_PWD), 'invitation_code': code.code},
        )
        client.get(f'/auth/verify/{token.token}/')
        call_kwargs = mock_send.call_args
        html = call_kwargs[1].get('html_message', '') if call_kwargs[1] else ''
        assert code.code in html


@pytest.mark.django_db
class TestApplyCodeView:
    def test_apply_valid_code_upgrades_to_tester(self, client, setup_plans_and_groups):
        user = User.objects.create_user(
            email='apply@test.com', password=_TEST_PWD, username='apply@test.com'
        )
        _make_tenant(user)
        user.groups.add(Group.objects.get(name='user'))
        admin = User.objects.create_user(
            email='adm3@test.com', password=_ADMIN_PWD, username='adm3@test.com'
        )
        _make_tenant(admin)
        code = InvitationCode.objects.create(created_by=admin)

        client.force_login(user)
        resp = client.post('/dashboard/apply-code/', {'code': code.code})
        assert resp.status_code == 302
        user.refresh_from_db()
        assert user.groups.filter(name='tester').exists()

    def test_apply_invalid_code_stays_user(self, client, setup_plans_and_groups):
        user = User.objects.create_user(
            email='bad@test.com', password=_TEST_PWD, username='bad@test.com'
        )
        _make_tenant(user)
        user.groups.add(Group.objects.get(name='user'))
        client.force_login(user)
        resp = client.post('/dashboard/apply-code/', {'code': 'COSMIC-INVALID'})
        assert resp.status_code == 302
        assert user.groups.filter(name='user').exists()

    def test_apply_code_requires_login(self, client, setup_plans_and_groups):
        resp = client.post('/dashboard/apply-code/', {'code': 'COSMIC-AAAAAA'})
        assert resp.status_code == 302
        assert '/auth/login/' in resp.url


# ── Magic link (auto-login desde correo) ──

@pytest.fixture
def magic_user(db):
    from core.tenant_management.models import LoginToken  # noqa: F401
    user = User.objects.create_user(email='magiclink@ejemplo.com', password=_TEST_PWD)
    _make_tenant(user)
    return user


def test_magic_link_valido_loguea_y_redirige_al_destino(client, magic_user):
    from core.tenant_management.models import LoginToken
    tok = LoginToken.objects.create(user=magic_user, redirect_to='/dashboard/')

    resp = client.get(f'/auth/entrar/{tok.token}/')

    assert resp.status_code == 302
    assert resp.url == '/dashboard/'
    assert client.session.get('_auth_user_id') == str(magic_user.id)


def test_magic_link_registra_el_uso(client, magic_user):
    """used_count/last_used_ip son para auditoría forense: permiten responder
    'desde qué IPs se usó este token' si el usuario reporta algo raro."""
    from core.tenant_management.models import LoginToken
    tok = LoginToken.objects.create(user=magic_user, redirect_to='/dashboard/')

    client.get(f'/auth/entrar/{tok.token}/', HTTP_X_REAL_IP='203.0.113.7')

    tok.refresh_from_db()
    assert tok.used_count == 1
    assert tok.last_used_at is not None
    assert tok.last_used_ip == '203.0.113.7'


def test_magic_link_es_reutilizable_dentro_de_la_ventana(client, magic_user):
    """Decisión del spec: reutilizable para sobrevivir al prefetch de Gmail y
    al uso en dos dispositivos."""
    from core.tenant_management.models import LoginToken
    tok = LoginToken.objects.create(user=magic_user, redirect_to='/dashboard/')

    primera = client.get(f'/auth/entrar/{tok.token}/')
    client.logout()
    segunda = client.get(f'/auth/entrar/{tok.token}/')

    assert primera.status_code == 302
    assert segunda.status_code == 302
    assert segunda.url == '/dashboard/'
    assert client.session.get('_auth_user_id') == str(magic_user.id)
    tok.refresh_from_db()
    assert tok.used_count == 2


def test_magic_link_expirado_manda_a_login_con_next(client, magic_user):
    """El ?next= es seguro porque sale de la BD, no de la URL: tras poner su
    contraseña el usuario cae donde iba, no en el dashboard genérico."""
    from django.utils import timezone
    from core.tenant_management.models import LoginToken
    tok = LoginToken.objects.create(
        user=magic_user, redirect_to='/calendar/abc/',
        expires_at=timezone.now() - timezone.timedelta(seconds=1),
    )

    resp = client.get(f'/auth/entrar/{tok.token}/')

    assert resp.status_code == 302
    assert resp.url.startswith('/auth/login/')
    assert 'next=%2Fcalendar%2Fabc%2F' in resp.url or 'next=/calendar/abc/' in resp.url
    assert client.session.get('_auth_user_id') is None


def test_magic_link_inexistente_manda_a_login_sin_next(client, db):
    resp = client.get('/auth/entrar/token-que-no-existe/')

    assert resp.status_code == 302
    assert resp.url.startswith('/auth/login/')
    assert 'next=' not in resp.url
    assert client.session.get('_auth_user_id') is None


def test_magic_link_no_revive_cuenta_desactivada(client, magic_user):
    """Una cuenta desactivada se reactiva por auth/reactivate/, nunca por
    magic link."""
    from core.tenant_management.models import LoginToken
    magic_user.is_active = False
    magic_user.save(update_fields=['is_active'])
    tok = LoginToken.objects.create(user=magic_user, redirect_to='/dashboard/')

    resp = client.get(f'/auth/entrar/{tok.token}/')

    assert resp.status_code == 302
    assert resp.url.startswith('/auth/login/')
    assert client.session.get('_auth_user_id') is None


def test_magic_link_reemplaza_sesion_de_otro_usuario(client, magic_user):
    """Computadora compartida: si había sesión de otro, el magic link la
    reemplaza correctamente (Django cicla la sesión en login())."""
    from core.tenant_management.models import LoginToken
    otro = User.objects.create_user(email='otro@ejemplo.com', password=_TEST_PWD, username='otro@ejemplo.com')
    _make_tenant(otro)
    client.force_login(otro)

    tok = LoginToken.objects.create(user=magic_user, redirect_to='/dashboard/')
    client.get(f'/auth/entrar/{tok.token}/')

    assert client.session.get('_auth_user_id') == str(magic_user.id)


def test_magic_link_no_lee_next_de_la_url(client, magic_user):
    """Cierra el open redirect: el destino sale SOLO de la fila en BD, así que
    un ?next= inyectado en la URL se ignora por completo."""
    from core.tenant_management.models import LoginToken
    tok = LoginToken.objects.create(user=magic_user, redirect_to='/dashboard/')

    resp = client.get(f'/auth/entrar/{tok.token}/?next=https://sitio-malicioso.com')

    assert resp.status_code == 302
    assert resp.url == '/dashboard/'


def test_magic_link_rate_limit_bloquea_al_intento_11(client, db):
    from django.core.cache import cache
    cache.clear()

    for _ in range(10):
        resp = client.get('/auth/entrar/token-invalido/', HTTP_X_REAL_IP='198.51.100.4')
        assert resp.url.startswith('/auth/login/')

    resp = client.get('/auth/entrar/token-invalido/', HTTP_X_REAL_IP='198.51.100.4')
    assert resp.status_code == 429


def test_magic_link_exitoso_no_cuenta_contra_el_rate_limit(client, magic_user):
    """Un usuario legítimo que abre su link muchas veces nunca debe toparse
    con el límite."""
    from django.core.cache import cache
    from core.tenant_management.models import LoginToken
    cache.clear()
    tok = LoginToken.objects.create(user=magic_user, redirect_to='/dashboard/')

    for _ in range(15):
        resp = client.get(f'/auth/entrar/{tok.token}/', HTTP_X_REAL_IP='198.51.100.9')
        assert resp.status_code == 302
        assert resp.url == '/dashboard/'
