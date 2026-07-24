import logging
import stripe
from django.conf import settings
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from core.tenant_management.models import Subscription
from core.brand_dna.models import AnalysisJob
from core.content_pipeline.email_sender import EmailSender

logger = logging.getLogger(__name__)


def _subscription_for_customer(customer_id):
    if not customer_id:
        return None
    return Subscription.objects.filter(stripe_customer_id=customer_id).first()


def _job_for_tenant(tenant):
    return AnalysisJob.objects.filter(
        user__tenant=tenant, generation_mode=AnalysisJob.MODE_FULL,
    ).order_by('-created_at').first()


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

    elif event_type == 'invoice.payment_failed':
        invoice = event['data']['object']
        customer_id = getattr(invoice, 'customer', None)
        sub = _subscription_for_customer(customer_id)
        if sub:
            sub.status = 'past_due'
            sub.save(update_fields=['status'])
            job = _job_for_tenant(sub.tenant)
            if job and hasattr(job, 'brand_dna'):
                try:
                    EmailSender().send_payment_failed(job=job, brand_dna=job.brand_dna)
                except Exception as email_err:
                    logger.error(f"Email de cobro fallido fallo para tenant {sub.tenant_id} (no fatal): {email_err}")
        else:
            logger.error(f"Webhook de Stripe: no se encontro suscripcion para customer {customer_id} en evento {event['id']}")

    elif event_type == 'invoice.payment_succeeded':
        invoice = event['data']['object']
        customer_id = getattr(invoice, 'customer', None)
        sub = _subscription_for_customer(customer_id)
        if sub:
            sub.status = 'active'
            sub.save(update_fields=['status'])
        else:
            logger.error(f"Webhook de Stripe: no se encontro suscripcion para customer {customer_id} en evento {event['id']}")

    return HttpResponse(status=200)

