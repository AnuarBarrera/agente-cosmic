import logging
import django_rq
from django.core.management.base import BaseCommand
from core.content_pipeline.models import ContentPost

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Encola la generacion de imagen para posts de calendarios activos que quedaron '
        'sin image_url (arquitectura previa a la generacion upfront de las 7 imagenes). '
        'No genera nada en el momento — solo encola, uno por uno, respetando el rate '
        'limit normal de Vertex AI via RQ.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Solo muestra cuantos posts se encolarian, sin encolar nada.',
        )

    def handle(self, *args, **options):
        from core.content_pipeline.tasks import backfill_image_task

        posts = (
            ContentPost.objects
            .filter(image_url='', calendar__brand_dna__job__deleted_at__isnull=True)
            .select_related('calendar__brand_dna__job')
        )
        count = posts.count()

        if count == 0:
            self.stdout.write('No hay posts pendientes de imagen. Nada que hacer.')
            return

        if options['dry_run']:
            self.stdout.write(f'[dry-run] Se encolarian {count} posts:')
            for post in posts:
                business = post.calendar.brand_dna.business_name
                self.stdout.write(f'  - dia {post.day_number} | {business} | post {post.id}')
            return

        queue = django_rq.get_queue('default')
        for post in posts:
            queue.enqueue(backfill_image_task, str(post.id), job_timeout=300)

        self.stdout.write(f'Encolados {count} jobs de backfill de imagen.')
