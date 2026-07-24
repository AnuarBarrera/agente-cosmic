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

    def test_subscription_registered_read_only_in_admin(self, client, groups):
        from core.tenant_management.models import TenantModel, Subscription, Plan
        from django_otp import DEVICE_ID_SESSION_KEY
        from django_otp.plugins.otp_totp.models import TOTPDevice

        staff = User.objects.create_user(
            email='staff@test.com', password='TestPass123!x', username='staff@test.com',
            is_staff=True, is_superuser=True,
        )
        client.force_login(staff)

        device = TOTPDevice.objects.create(user=staff, confirmed=True)
        session = client.session
        session[DEVICE_ID_SESSION_KEY] = device.persistent_id
        session.save()

        plan = Plan.objects.create(name='Plan Admin Test')
        tenant = TenantModel.objects.create(name='Tenant Admin Test', status='active')
        sub = Subscription.objects.create(tenant=tenant, plan=plan, stripe_customer_id='cus_admin_test')

        response = client.get('/admin/tenant_management/subscription/')
        assert response.status_code == 200
        assert b'cus_admin_test' in response.content

        add_response = client.get('/admin/tenant_management/subscription/add/')
        assert add_response.status_code == 403

        change_response = client.get(f'/admin/tenant_management/subscription/{sub.id}/change/')
        assert change_response.status_code == 200

