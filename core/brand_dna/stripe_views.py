import logging
import stripe
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from core.tenant_management.models import Subscription

logger = logging.getLogger(__name__)


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

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        tenant_id = session.get('client_reference_id')
        updated = Subscription.objects.filter(tenant_id=tenant_id).update(
            status='active', trial_ends_at=None,
        )
        if not updated:
            logger.error(f"Webhook de Stripe: no se encontro tenant {tenant_id} para el evento {event['id']}")
        else:
            logger.info(f"Suscripcion activada para tenant {tenant_id} via Stripe")

    return HttpResponse(status=200)
