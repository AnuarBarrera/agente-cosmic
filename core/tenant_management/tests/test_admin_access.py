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
