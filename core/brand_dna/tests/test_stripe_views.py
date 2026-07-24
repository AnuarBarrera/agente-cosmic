import uuid
import pytest
import stripe
from types import SimpleNamespace
from unittest.mock import patch
from django.test import Client, override_settings
from datetime import timedelta
from django.utils import timezone
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


def _fake_event(event_id, tenant_id, event_type='checkout.session.completed', customer='cus_test1', subscription='sub_test1'):
    return {
        'id': event_id,
        'type': event_type,
        'data': {'object': SimpleNamespace(
            client_reference_id=str(tenant_id), customer=customer, subscription=subscription,
        )},
    }


def _fake_subscription_event(event_id, customer_id, cancel_at_period_end=False, cancel_at=None):
    return {
        'id': event_id,
        'type': 'customer.subscription.updated',
        'data': {'object': SimpleNamespace(
            customer=customer_id, cancel_at_period_end=cancel_at_period_end, cancel_at=cancel_at,
        )},
    }


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_valid_signature_activates_subscription(tenant_with_subscription):
    from core.brand_dna.models import AnalysisJob, BrandDNA
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    user = UserModel.objects.create_user(username='t1@t.com', email='t1@t.com', password='pass1234')
    user.tenant = tenant_with_subscription
    user.save(update_fields=['tenant'])
    job = AnalysisJob.objects.create(
        email=user.email, business_url='https://tuwebmx.com', user=user,
        status=AnalysisJob.STATUS_DONE, generation_mode=AnalysisJob.MODE_FULL,
    )
    BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event',
               return_value=_fake_event('evt_1', tenant_with_subscription.id)), \
         patch('core.brand_dna.stripe_views.django_rq') as mock_rq:
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
        )
    assert response.status_code == 200
    sub = Subscription.objects.get(tenant=tenant_with_subscription)
    assert sub.status == 'active'
    assert sub.trial_ends_at is None
    assert sub.paid_until is not None
    assert sub.paid_until > timezone.now() + timedelta(days=27)
    assert sub.stripe_customer_id == 'cus_test1'
    mock_rq.enqueue.assert_not_called()


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


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_subscription_updated_syncs_cancel_at_period_end(tenant_with_subscription):
    tenant_with_subscription.subscription.stripe_customer_id = 'cus_test1'
    tenant_with_subscription.subscription.status = 'active'
    tenant_with_subscription.subscription.save(update_fields=['stripe_customer_id', 'status'])
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event',
               return_value=_fake_subscription_event('evt_4', 'cus_test1', cancel_at_period_end=True)):
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
        )
    assert response.status_code == 200
    sub = Subscription.objects.get(tenant=tenant_with_subscription)
    assert sub.cancel_at_period_end is True
    assert sub.status == 'active'


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_subscription_updated_syncs_via_cancel_at_timestamp(tenant_with_subscription):
    # Confirmado en vivo contra el Customer Portal real: al cancelar ahi, Stripe deja
    # cancel_at_period_end en False pero pone un timestamp real en cancel_at. Sin este
    # fallback la cancelacion real nunca se hubiera sincronizado.
    tenant_with_subscription.subscription.stripe_customer_id = 'cus_test1'
    tenant_with_subscription.subscription.status = 'active'
    tenant_with_subscription.subscription.save(update_fields=['stripe_customer_id', 'status'])
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event',
               return_value=_fake_subscription_event('evt_4b', 'cus_test1', cancel_at_period_end=False, cancel_at=1787549561)):
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
        )
    assert response.status_code == 200
    sub = Subscription.objects.get(tenant=tenant_with_subscription)
    assert sub.cancel_at_period_end is True
    assert sub.status == 'active'


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_subscription_deleted_cancels(tenant_with_subscription):
    tenant_with_subscription.subscription.stripe_customer_id = 'cus_test1'
    tenant_with_subscription.subscription.status = 'active'
    tenant_with_subscription.subscription.cancel_at_period_end = True
    tenant_with_subscription.subscription.save(update_fields=['stripe_customer_id', 'status', 'cancel_at_period_end'])
    fake_event = {
        'id': 'evt_5',
        'type': 'customer.subscription.deleted',
        'data': {'object': SimpleNamespace(customer='cus_test1')},
    }
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event', return_value=fake_event):
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
        )
    assert response.status_code == 200
    sub = Subscription.objects.get(tenant=tenant_with_subscription)
    assert sub.status == 'canceled'
    assert sub.cancel_at_period_end is False


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_subscription_updated_unknown_customer_returns_200_and_logs(tenant_with_subscription):
    fake_event = {
        'id': 'evt_6',
        'type': 'customer.subscription.updated',
        'data': {'object': SimpleNamespace(customer='cus_unknown', cancel_at_period_end=True)},
    }
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event', return_value=fake_event):
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
        )
    assert response.status_code == 200


def _fake_invoice_event(event_id, event_type, customer_id):
    return {
        'id': event_id,
        'type': event_type,
        'data': {'object': SimpleNamespace(customer=customer_id)},
    }


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_payment_failed_marks_past_due_and_sends_email(tenant_with_subscription):
    from core.brand_dna.models import AnalysisJob, BrandDNA
    from django.contrib.auth import get_user_model
    tenant_with_subscription.subscription.stripe_customer_id = 'cus_test1'
    tenant_with_subscription.subscription.status = 'active'
    tenant_with_subscription.subscription.save(update_fields=['stripe_customer_id', 'status'])
    UserModel = get_user_model()
    user = UserModel.objects.create_user(username='t@t.com', email='t@t.com', password='pass1234')
    user.tenant = tenant_with_subscription
    user.save(update_fields=['tenant'])
    job = AnalysisJob.objects.create(
        email=user.email, business_url='https://tuwebmx.com', user=user,
        status=AnalysisJob.STATUS_DONE, generation_mode=AnalysisJob.MODE_FULL,
    )
    BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event',
               return_value=_fake_invoice_event('evt_7', 'invoice.payment_failed', 'cus_test1')), \
         patch('core.brand_dna.stripe_views.EmailSender') as MockEmail:
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
        )
    assert response.status_code == 200
    sub = Subscription.objects.get(tenant=tenant_with_subscription)
    assert sub.status == 'past_due'
    MockEmail.return_value.send_payment_failed.assert_called_once()


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_payment_succeeded_restores_active(tenant_with_subscription):
    tenant_with_subscription.subscription.stripe_customer_id = 'cus_test1'
    tenant_with_subscription.subscription.status = 'past_due'
    tenant_with_subscription.subscription.save(update_fields=['stripe_customer_id', 'status'])
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event',
               return_value=_fake_invoice_event('evt_8', 'invoice.payment_succeeded', 'cus_test1')):
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
        )
    assert response.status_code == 200
    sub = Subscription.objects.get(tenant=tenant_with_subscription)
    assert sub.status == 'active'


@pytest.fixture
def user_with_customer_id(django_user_model, tenant_with_subscription):
    tenant_with_subscription.subscription.stripe_customer_id = 'cus_test1'
    tenant_with_subscription.subscription.save(update_fields=['stripe_customer_id'])
    user = django_user_model.objects.create_user(
        username='portal@test.com', email='portal@test.com', password='pass1234'
    )
    user.tenant = tenant_with_subscription
    user.save(update_fields=['tenant'])
    return user


def test_manage_subscription_redirects_to_portal_session(user_with_customer_id):
    c = Client()
    c.force_login(user_with_customer_id)
    fake_session = SimpleNamespace(url='https://billing.stripe.com/p/session/test_abc')
    with patch('core.brand_dna.stripe_views.stripe.billing_portal.Session.create',
               return_value=fake_session) as mock_create:
        response = c.post('/dashboard/suscripcion/')
    assert response.status_code == 302
    assert response.url == 'https://billing.stripe.com/p/session/test_abc'
    mock_create.assert_called_once()
    assert mock_create.call_args[1]['customer'] == 'cus_test1'


def test_manage_subscription_without_customer_id_redirects_to_dashboard(django_user_model, tenant_with_subscription):
    user = django_user_model.objects.create_user(
        username='noportal@test.com', email='noportal@test.com', password='pass1234'
    )
    user.tenant = tenant_with_subscription
    user.save(update_fields=['tenant'])
    c = Client()
    c.force_login(user)
    with patch('core.brand_dna.stripe_views.stripe.billing_portal.Session.create') as mock_create:
        response = c.post('/dashboard/suscripcion/')
    assert response.status_code == 302
    assert response.url == '/dashboard/'
    mock_create.assert_not_called()


def test_manage_subscription_requires_login():
    c = Client()
    response = c.post('/dashboard/suscripcion/')
    assert response.status_code == 302
    assert '/auth/login/' in response.url


@override_settings(STRIPE_WEBHOOK_SECRET='whsec_test123')
def test_webhook_payment_enqueues_generate_next_month(tenant_with_subscription):
    from core.brand_dna.models import AnalysisJob, BrandDNA
    from core.content_pipeline.models import ContentCalendar
    from django.contrib.auth import get_user_model
    UserModel = get_user_model()
    user = UserModel.objects.create_user(username='t2@t.com', email='t2@t.com', password='pass1234')
    user.tenant = tenant_with_subscription
    user.save(update_fields=['tenant'])
    job = AnalysisJob.objects.create(
        email=user.email, business_url='https://tuwebmx.com', user=user,
        status=AnalysisJob.STATUS_DONE, generation_mode=AnalysisJob.MODE_FULL,
    )
    dna = BrandDNA.objects.create(
        job=job, business_name='Tu Web MX', business_url='https://tuwebmx.com',
        description='Agencia digital', keywords=['diseno'], audience='PYMEs',
        tone='profesional', primary_colors=['#1a1a2e'],
    )
    calendar = ContentCalendar.objects.create(brand_dna=dna)
    c = Client()
    with patch('core.brand_dna.stripe_views.stripe.Webhook.construct_event',
               return_value=_fake_event('evt_2', tenant_with_subscription.id)), \
         patch('core.brand_dna.stripe_views.django_rq') as mock_rq:
        response = c.post(
            '/stripe/webhook/', data=b'{}', content_type='application/json',
            HTTP_STRIPE_SIGNATURE='t=1,v1=fake',
        )
    assert response.status_code == 200
    mock_rq.enqueue.assert_called_once()
    enqueue_args = mock_rq.enqueue.call_args[0]
    assert enqueue_args[1] == str(calendar.id)
    calendar.refresh_from_db()
    assert calendar.next_week_generating is True


