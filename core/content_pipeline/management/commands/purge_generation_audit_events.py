from datetime import timedelta

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from core.content_pipeline.models import GenerationAuditEvent


class Command(BaseCommand):
    help = 'Elimina eventos de auditoria de generacion anteriores a la retencion.'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=90)
        parser.add_argument('--dry-run', action='store_true')

    def handle(self, *args, **options):
        days = options['days']
        if days < 1:
            raise CommandError('--days debe ser mayor que cero')
        cutoff = timezone.now() - timedelta(days=days)
        queryset = GenerationAuditEvent.objects.filter(created_at__lt=cutoff)
        count = queryset.count()
        if not options['dry_run']:
            queryset.delete()
        action = 'Se eliminarian' if options['dry_run'] else 'Eliminados'
        self.stdout.write(f'{action} {count} eventos anteriores a {cutoff.isoformat()}')
