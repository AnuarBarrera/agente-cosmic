import logging
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentPost
from core.shared.metrics import EMAILS_SENT

logger = logging.getLogger(__name__)


_MESES_ES = [
    'enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio',
    'julio', 'agosto', 'septiembre', 'octubre', 'noviembre', 'diciembre',
]


def _fecha_es(dt) -> str:
    return f"{dt.day} de {_MESES_ES[dt.month - 1]}"


class EmailSender:
    def send_initial(self, job: AnalysisJob, brand_dna: BrandDNA) -> None:
        calendar_url = settings.COSMIC_BASE_URL + reverse('calendar_review', args=[job.id])
        html = render_to_string('content_pipeline/email_initial.html', {
            'brand_dna': brand_dna,
            'calendar_url': calendar_url,
        })
        name = brand_dna.business_name.strip() if brand_dna.business_name else ''
        subject = f'✅ Tu plan de contenido está listo — {name}' if name else '✅ Tu plan de contenido está listo — Agente Cosmic'
        plain = f'Tu plan de contenido de {name} está listo.' if name else 'Tu plan de contenido está listo.'
        send_mail(
            subject,
            plain,
            settings.DEFAULT_FROM_EMAIL,
            recipient_list=[job.email],
            html_message=html,
            fail_silently=False,
        )
        EMAILS_SENT.labels(type='initial_calendar').inc()
        logger.info(f"Email inicial enviado a {job.email} para job {job.id}")

    def send_month_ready(self, job: AnalysisJob, brand_dna: BrandDNA) -> None:
        calendar_url = settings.COSMIC_BASE_URL + reverse('calendar_review', args=[job.id])
        html = render_to_string('content_pipeline/email_initial.html', {
            'brand_dna': brand_dna,
            'calendar_url': calendar_url,
        })
        name = brand_dna.business_name.strip() if brand_dna.business_name else ''
        subject = f'✅ Tu mes de contenido está listo — {name}' if name else '✅ Tu mes de contenido está listo — Agente Cosmic'
        plain = f'Tu mes de contenido de {name} está listo.' if name else 'Tu mes de contenido está listo.'
        send_mail(
            subject,
            plain,
            settings.DEFAULT_FROM_EMAIL,
            recipient_list=[job.email],
            html_message=html,
            fail_silently=False,
        )
        EMAILS_SENT.labels(type='month_ready').inc()
        logger.info(f"Email de mes listo enviado a {job.email} para job {job.id}")

    def send_week_ready(self, job: AnalysisJob, brand_dna: BrandDNA) -> None:
        calendar_url = settings.COSMIC_BASE_URL + reverse('calendar_review', args=[job.id])
        html = render_to_string('content_pipeline/email_week_ready.html', {
            'brand_dna': brand_dna,
            'calendar_url': calendar_url,
        })
        name = brand_dna.business_name.strip() if brand_dna.business_name else ''
        subject = f'🎉 Tu primera semana de contenido ya está lista — {name}' if name else '🎉 Tu primera semana de contenido ya está lista — Agente Cosmic'
        plain = (
            f'Tu primera semana de contenido de {name} ya está lista. Seguimos generando el resto del mes en segundo plano.'
            if name else
            'Tu primera semana de contenido ya está lista. Seguimos generando el resto del mes en segundo plano.'
        )
        send_mail(
            subject, plain, settings.DEFAULT_FROM_EMAIL,
            recipient_list=[job.email], html_message=html, fail_silently=False,
        )
        EMAILS_SENT.labels(type='week_ready').inc()
        logger.info(f"Email de semana 1 lista enviado a {job.email} para job {job.id}")


    def send_daily(self, post: ContentPost) -> None:
        calendar_review_url = settings.COSMIC_BASE_URL + reverse(
            'calendar_review', args=[post.calendar.brand_dna.job.id]
        )
        fecha = _fecha_es(post.scheduled_at)
        html = render_to_string('content_pipeline/email_daily.html', {
            'post': post,
            'calendar_review_url': calendar_review_url,
            'fecha': fecha,
        })
        business_name = (post.calendar.brand_dna.business_name or '').strip()
        email = post.calendar.brand_dna.job.email
        subject = f'🔔 No se te olvide publicar hoy ({fecha}) — {business_name}' if business_name else f'🔔 No se te olvide publicar hoy ({fecha}) — Agente Cosmic'
        send_mail(
            subject,
            f'No se te olvide publicar el día de hoy ({fecha}).',
            settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            html_message=html,
            fail_silently=False,
        )
        post.status = ContentPost.STATUS_SENT
        post.sent_at = timezone.now()
        post.save(update_fields=['status', 'sent_at'])
        EMAILS_SENT.labels(type='daily_post').inc()
        logger.info(f"Email dia {post.day_number} enviado a {email}")

    def send_trial_expired(self, job: AnalysisJob, brand_dna: BrandDNA) -> None:
        payment_url = f"{settings.STRIPE_PAYMENT_LINK_URL}?client_reference_id={job.user.tenant_id}"
        html = render_to_string('content_pipeline/email_trial_expired.html', {
            'brand_dna': brand_dna,
            'payment_url': payment_url,
        })
        name = brand_dna.business_name.strip() if brand_dna.business_name else ''
        subject = f'⏳ Tu semana gratis de {name} terminó' if name else '⏳ Tu semana gratis terminó'
        plain = (
            f'Tu semana gratis de contenido para {name} terminó. '
            f'Paga para seguir generando contenido: {payment_url}'
        ) if name else f'Tu semana gratis terminó. Paga para seguir generando contenido: {payment_url}'
        send_mail(
            subject,
            plain,
            settings.DEFAULT_FROM_EMAIL,
            recipient_list=[job.email],
            html_message=html,
            fail_silently=False,
        )
        EMAILS_SENT.labels(type='trial_expired').inc()
        logger.info(f"Email de trial expirado enviado a {job.email} para job {job.id}")

    def send_payment_failed(self, job: AnalysisJob, brand_dna: BrandDNA) -> None:
        dashboard_url = settings.COSMIC_BASE_URL + reverse('dashboard')
        html = render_to_string('content_pipeline/email_payment_failed.html', {
            'brand_dna': brand_dna,
            'dashboard_url': dashboard_url,
        })
        name = brand_dna.business_name.strip() if brand_dna.business_name else ''
        subject = f'⚠️ No pudimos cobrar tu suscripción — {name}' if name else '⚠️ No pudimos cobrar tu suscripción'
        plain = (
            f'No pudimos cobrar tu suscripción de {name}. Actualiza tu método de pago: {dashboard_url}'
        ) if name else f'No pudimos cobrar tu suscripción. Actualiza tu método de pago: {dashboard_url}'
        send_mail(
            subject,
            plain,
            settings.DEFAULT_FROM_EMAIL,
            recipient_list=[job.email],
            html_message=html,
            fail_silently=False,
        )
        EMAILS_SENT.labels(type='payment_failed').inc()
        logger.info(f"Email de cobro fallido enviado a {job.email} para job {job.id}")

    def send_month_expired(self, job: AnalysisJob, brand_dna: BrandDNA) -> None:
        payment_url = f"{settings.STRIPE_PAYMENT_LINK_URL}?client_reference_id={job.user.tenant_id}"
        html = render_to_string('content_pipeline/email_month_expired.html', {
            'brand_dna': brand_dna,
            'payment_url': payment_url,
        })
        name = brand_dna.business_name.strip() if brand_dna.business_name else ''
        subject = f'⏳ Ya pasó un mes — {name}' if name else '⏳ Ya pasó un mes desde tu última generación'
        plain = (
            f'Ya pasó un mes desde tu última generación de contenido para {name}. '
            f'Genera un mes nuevo ahora: {payment_url}'
        ) if name else f'Ya pasó un mes desde tu última generación de contenido. Genera un mes nuevo ahora: {payment_url}'
        send_mail(
            subject,
            plain,
            settings.DEFAULT_FROM_EMAIL,
            recipient_list=[job.email],
            html_message=html,
            fail_silently=False,
        )
        EMAILS_SENT.labels(type='month_expired').inc()
        logger.info(f"Email de mes vencido enviado a {job.email} para job {job.id}")



