import logging
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost
from core.shared.metrics import EMAILS_SENT

logger = logging.getLogger(__name__)


class EmailSender:
    def send_initial(self, job: AnalysisJob, brand_dna: BrandDNA, calendar: ContentCalendar) -> None:
        posts = list(calendar.posts.order_by('day_number'))
        day1 = posts[0] if posts else None
        calendar_url = settings.COSMIC_BASE_URL + reverse('calendar_review', args=[job.id])
        html = render_to_string('content_pipeline/email_initial.html', {
            'brand_dna': brand_dna,
            'day1': day1,
            'calendar_url': calendar_url,
        })
        name = brand_dna.business_name.strip() if brand_dna.business_name else ''
        subject = f'Tu plan de contenido está listo — {name}' if name else 'Tu plan de contenido está listo — Agente Cosmic'
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

    def send_daily(self, post: ContentPost) -> None:
        calendar_review_url = settings.COSMIC_BASE_URL + reverse(
            'calendar_review', args=[post.calendar.brand_dna.job.id]
        )
        html = render_to_string('content_pipeline/email_daily.html', {
            'post': post,
            'calendar_review_url': calendar_review_url,
        })
        business_name = (post.calendar.brand_dna.business_name or '').strip()
        email = post.calendar.brand_dna.job.email
        subject = f'Día {post.day_number} de tu calendario — {business_name}' if business_name else f'Día {post.day_number} de tu calendario — Agente Cosmic'
        send_mail(
            subject,
            f'Tu contenido del día {post.day_number} está listo.',
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
