import logging
import stripe
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from core.tenant_management.models import Subscription

logger = logging.getLogger(__name__)


def _subscription_for_customer(customer_id):
    if not customer_id:
        return None
    return Subscription.objects.filter(stripe_customer_id=customer_id).first()


@csrf_exempt
@require_POST
def stripe_webhook_view(request):
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, settings.STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        logger.warning(f"Webhook de Stripe con firma invalida: {e}")
        return HttpResponseBadRequest('Invalid signature')

    event_type = event['type']

    if event_type == 'checkout.session.completed':
        session = event['data']['object']
        tenant_id = getattr(session, 'client_reference_id', None)
        updated = Subscription.objects.filter(tenant_id=tenant_id).update(
            status='active',
            trial_ends_at=None,
            stripe_customer_id=getattr(session, 'customer', '') or '',
            stripe_subscription_id=getattr(session, 'subscription', '') or '',
        )
        if not updated:
            logger.error(f"Webhook de Stripe: no se encontro tenant {tenant_id} para el evento {event['id']}")
        else:
            logger.info(f"Suscripcion activada para tenant {tenant_id} via Stripe")

    elif event_type == 'customer.subscription.updated':
        subscription_obj = event['data']['object']
        customer_id = getattr(subscription_obj, 'customer', None)
        sub = _subscription_for_customer(customer_id)
        if sub:
            sub.cancel_at_period_end = bool(getattr(subscription_obj, 'cancel_at_period_end', False))
            sub.save(update_fields=['cancel_at_period_end'])
        else:
            logger.error(f"Webhook de Stripe: no se encontro suscripcion para customer {customer_id} en evento {event['id']}")

    elif event_type == 'customer.subscription.deleted':
        subscription_obj = event['data']['object']
        customer_id = getattr(subscription_obj, 'customer', None)
        sub = _subscription_for_customer(customer_id)
        if sub:
            sub.status = 'canceled'
            sub.cancel_at_period_end = False
            sub.save(update_fields=['status', 'cancel_at_period_end'])
        else:
            logger.error(f"Webhook de Stripe: no se encontro suscripcion para customer {customer_id} en evento {event['id']}")

    return HttpResponse(status=200)
