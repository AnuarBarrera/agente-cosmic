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


@pytest.mark.django_db
class TestApplyCodeView:
    def test_apply_valid_code_upgrades_to_tester(self, client, setup_plans_and_groups):
        user = User.objects.create_user(
            email='apply@test.com', password='SecurePass123!x', username='apply@test.com'
        )
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
