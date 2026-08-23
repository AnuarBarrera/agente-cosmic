import logging
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.tenant_management.models import LoginToken

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Borra los LoginToken (magic links) ya expirados. Dry-run por default '
        '-- requiere --apply para borrar de verdad. Pensado para el mismo cron '
        'externo donde corre send_reactivation_emails. Si nunca se corre no se '
        'rompe nada: la tabla solo acumula filas muertas.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--apply', action='store_true',
            help='Borra los tokens de verdad. Sin este flag, solo se reporta.',
        )

    def handle(self, *args, **options):
        apply_changes = options['apply']
        expirados = LoginToken.objects.filter(expires_at__lt=timezone.now())
        total = expirados.count()

        if not apply_changes:
            self.stdout.write(
                f'[dry-run] Se borrarian {total} LoginToken expirados. '
                f'Corre con --apply para ejecutar.'
            )
            return

        expirados.delete()
        self.stdout.write(self.style.SUCCESS(f'{total} LoginToken expirados borrados.'))
        logger.info(f'purge_login_tokens: {total} tokens expirados borrados')