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
        assert len(code) == 13
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
