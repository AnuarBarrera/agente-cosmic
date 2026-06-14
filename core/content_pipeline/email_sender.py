import logging
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from core.brand_dna.models import AnalysisJob, BrandDNA
from core.content_pipeline.models import ContentCalendar, ContentPost

logger = logging.getLogger(__name__)


class EmailSender:
    def send_initial(self, job: AnalysisJob, brand_dna: BrandDNA, calendar: ContentCalendar) -> None:
        posts = list(calendar.posts.order_by('day_number'))
        day1 = posts[0] if posts else None
        html = render_to_string('content_pipeline/email_initial.html', {
            'brand_dna': brand_dna,
            'posts': posts,
            'day1': day1,
        })
        send_mail(
            f'Tu ADN de Marca esta listo — {brand_dna.business_name}',
            f'Tu ADN de Marca de {brand_dna.business_name} esta listo.',
            settings.DEFAULT_FROM_EMAIL,
            recipient_list=[job.email],
            html_message=html,
            fail_silently=False,
        )
        logger.info(f"Email inicial enviado a {job.email} para job {job.id}")

    def send_daily(self, post: ContentPost) -> None:
        calendar_review_url = settings.COSMIC_BASE_URL + reverse(
            'calendar_review', args=[post.calendar.brand_dna.job.id]
        )
        html = render_to_string('content_pipeline/email_daily.html', {
            'post': post,
            'calendar_review_url': calendar_review_url,
        })
        business_name = post.calendar.brand_dna.business_name
        email = post.calendar.brand_dna.job.email
        send_mail(
            f'Dia {post.day_number} de tu calendario — {business_name}',
            f'Tu contenido del dia {post.day_number} esta listo.',
            settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            html_message=html,
            fail_silently=False,
        )
        post.status = ContentPost.STATUS_SENT
        post.sent_at = timezone.now()
        post.save(update_fields=['status', 'sent_at'])
        logger.info(f"Email dia {post.day_number} enviado a {email}")
