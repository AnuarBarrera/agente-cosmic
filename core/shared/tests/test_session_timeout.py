import time
import pytest
from django.test import Client, modify_settings
from core.tenant_management.models import TenantModel, Plan, Subscription

pytestmark = [
    pytest.mark.django_db,
    pytest.mark.usefixtures('_activate_session_timeout'),
]


@pytest.fixture(autouse=False)
def _activate_session_timeout(settings):
    mw = 'core.shared.middleware.session_timeout.SessionTimeoutMiddleware'
    if mw not in settings.MIDDLEWARE:
        settings.MIDDLEWARE = list(settings.MIDDLEWARE) + [mw]


@pytest.fixture
def free_plan():
    plan, _ = Plan.objects.get_or_create(
        name='Free',
        defaults={
            'max_calendars_per_week': 2,
            'max_post_regenerations': 2,
            'max_post_edits': 2,
            'price': 0,
        },
    )
    return plan


@pytest.fixture
def user(django_user_model, free_plan):
    u = django_user_model.objects.create_user(
        email='timeout@test.com', username='timeout@test.com', password='pass1234'
    )
    tenant = TenantModel.objects.create(name=u.email, status='active')
    Subscription.objects.create(tenant=tenant, plan=free_plan)
    u.tenant = tenant
    u.save(update_fields=['tenant'])
    return u


def test_session_sets_last_activity(user):
    c = Client()
    c.force_login(user)
    c.get('/dashboard/')
    session = c.session
    assert 'last_activity' in session


def test_session_not_flushed_when_active(user):
    c = Client()
    c.force_login(user)
    c.get('/dashboard/')
    response = c.get('/dashboard/')
    assert response.status_code == 200


def test_session_flushed_on_inactivity(user):
    c = Client()
    c.force_login(user)
    c.get('/dashboard/')

    session = c.session
    session['last_activity'] = time.time() - 1900
    session.save()

    response = c.get('/dashboard/')
    assert response.status_code == 302
    assert '/auth/login/' in response.url
    assert 'reason=inactivity' in response.url


def test_unauthenticated_request_passes_through():
    c = Client()
    response = c.get('/')
    assert response.status_code == 302
