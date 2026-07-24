import uuid
import pytest
import stripe
from unittest.mock import patch
from django.test import Client, override_settings
from core.tenant_management.models import TenantModel, Subscription, Plan

pytestmark = pytest.mark.django_db


@pytest.fixture
def tenant_with_subscription():
    plan, _ = Plan.objects.get_or_create(name='User', defaults={
        'max_calendars_per_week': 2, 'max_post_regenerations': 2,
        'max_post_edits': 2, 'price': 0,
    })
    tenant = TenantModel.objects.create(name='Tenant Test', status='active')
    Subscription.objects.create(tenant=tenant, plan=plan, status='trialing')
    return tenant


def _fake_event(event_id, tenant_id):
    return {
        'id': event_id,
        'type': 'checkout.session.completed',
        'data': {'object': {'client_reference_id': str(tenant_id)}},
    }


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_valid_signature_activates_subscription(tenant_with_subscription):
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event',
               return_value=_fake_event('evt_1', tenant_with_subscription.id)):
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
        )
    assert response.status_code == 200
    sub = Subscription.objects.get(tenant=tenant_with_subscription)
    assert sub.status == 'active'
    assert sub.trial_ends_at is None


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_invalid_signature_returns_400_without_changes(tenant_with_subscription):
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event',
               side_effect=stripe.error.SignatureVerificationError('bad sig', 'sig_header')):
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='bad',
        )
    assert response.status_code == 400
    sub = Subscription.objects.get(tenant=tenant_with_subscription)
    assert sub.status == 'trialing'


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_unknown_tenant_returns_200_and_logs(tenant_with_subscription):
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event',
               return_value=_fake_event('evt_2', uuid.uuid4())):
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
        )
    assert response.status_code == 200
    sub = Subscription.objects.get(tenant=tenant_with_subscription)
    assert sub.status == 'trialing'


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_repeated_event_is_idempotent(tenant_with_subscription):
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event',
               return_value=_fake_event('evt_3', tenant_with_subscription.id)):
        c.post('/stripe/webhook/', data=b'{}', content_type='application/json', HTTP_STRIPE_SIGNATURE='t=1,v1=fake')
        response = c.post('/stripe/webhook/', data=b'{}', content_type='application/json', HTTP_STRIPE_SIGNATURE='t=1,v1=fake')
    assert response.status_code == 200
    sub = Subscription.objects.get(tenant=tenant_with_subscription)
    assert sub.status == 'active'
