import pytest
import secrets
from io import StringIO
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from core.tenant_management.models import LoginToken

pytestmark = pytest.mark.django_db

User = get_user_model()
_TEST_PWD = f"T3st-{secrets.token_urlsafe(10)}!"


@pytest.fixture
def user():
    return User.objects.create_user(email='purga@ejemplo.com', password=_TEST_PWD)


def _token_expirado(user):
    return LoginToken.objects.create(
        user=user, redirect_to='/dashboard/',
        expires_at=timezone.now() - timezone.timedelta(hours=1),
    )


def _token_vivo(user):
    return LoginToken.objects.create(
        user=user, redirect_to='/dashboard/',
        expires_at=timezone.now() + timezone.timedelta(hours=1),
    )


def test_dry_run_por_default_no_borra_nada(user):
    """Mismo criterio que migrate_testers_to_founder: sin --apply, el comando
    solo reporta."""
    _token_expirado(user)
    _token_expirado(user)
    _token_vivo(user)

    out = StringIO()
    call_command('purge_login_tokens', stdout=out)

    assert LoginToken.objects.count() == 3
    assert '2' in out.getvalue()


def test_apply_borra_solo_los_expirados(user):
    _token_expirado(user)
    _token_expirado(user)
    vivo = _token_vivo(user)

    call_command('purge_login_tokens', '--apply', stdout=StringIO())

    assert LoginToken.objects.count() == 1
    assert LoginToken.objects.first().id == vivo.id


def test_apply_sin_expirados_no_falla(user):
    _token_vivo(user)

    call_command('purge_login_tokens', '--apply', stdout=StringIO())

    assert LoginToken.objects.count() == 1


def test_es_idempotente(user):
    _token_expirado(user)

    call_command('purge_login_tokens', '--apply', stdout=StringIO())
    call_command('purge_login_tokens', '--apply', stdout=StringIO())

    assert LoginToken.objects.count() == 0