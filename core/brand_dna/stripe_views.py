import logging
import stripe
import django_rq
from datetime import timedelta
from django.utils import timezone
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from core.tenant_management.models import Subscription
from core.brand_dna.models import AnalysisJob
from core.content_pipeline.email_sender import EmailSender

logger = logging.getLogger(__name__)

stripe.api_key = settings.STRIPE_SECRET_KEY


def _subscription_for_customer(customer_id):
    if not customer_id:
        return None
    return Subscription.objects.filter(stripe_customer_id=customer_id).first()


def _job_for_tenant(tenant_id):
    return AnalysisJob.objects.filter(
        user__tenant_id=tenant_id, generation_mode=AnalysisJob.MODE_FULL,
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
            paid_until=timezone.now() + timedelta(days=28),
            stripe_customer_id=getattr(session, 'customer', '') or '',
            stripe_subscription_id=getattr(session, 'subscription', '') or '',
        )
        if not updated:
            logger.error(f"Webhook de Stripe: no se encontro tenant {tenant_id} para el evento {event['id']}")
        else:
            logger.info(f"Pago confirmado para tenant {tenant_id} via Stripe")
            from core.content_pipeline.tasks import generate_next_month
            job = _job_for_tenant(tenant_id)
            if job and hasattr(job, 'brand_dna') and hasattr(job.brand_dna, 'calendar'):
                calendar = job.brand_dna.calendar
                calendar.next_week_generating = True
                calendar.save(update_fields=['next_week_generating'])
                django_rq.enqueue(generate_next_month, str(calendar.id), job_timeout=2400)
            else:
                logger.warning(f"No se encontro calendario para tenant {tenant_id} — pago confirmado sin generar mes")

    elif event_type == 'customer.subscription.updated':
        subscription_obj = event['data']['object']
        customer_id = getattr(subscription_obj, 'customer', None)
        sub = _subscription_for_customer(customer_id)
        if sub:
            # Stripe representa "va a cancelar" de 2 formas segun el flujo: el booleano
            # cancel_at_period_end, o un timestamp en cancel_at (confirmado en vivo via
            # Customer Portal real — cancel_at_period_end se quedo en False mientras
            # cancel_at traia el timestamp real de fin de periodo). Cubrimos ambas.
            cancel_scheduled = bool(getattr(subscription_obj, 'cancel_at_period_end', False)) or bool(getattr(subscription_obj, 'cancel_at', None))
            sub.cancel_at_period_end = cancel_scheduled
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
            job = _job_for_tenant(sub.tenant_id)
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


@login_required
@require_POST
def manage_subscription_view(request):
    subscription = getattr(getattr(request.user, 'tenant', None), 'subscription', None)
    if not subscription or not subscription.stripe_customer_id:
        return redirect('dashboard')
    portal_session = stripe.billing_portal.Session.create(
        customer=subscription.stripe_customer_id,
        return_url=settings.COSMIC_BASE_URL + reverse('dashboard'),
    )
    return redirect(portal_session.url)

