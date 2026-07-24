from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Notifica y degrada a trial_expired las suscripciones cuyo trial de 7 dias vencio sin pago'

    def handle(self, *args, **options):
        from core.content_pipeline.tasks import expire_stale_trials_task
        expire_stale_trials_task()
        self.stdout.write(self.style.SUCCESS('Proceso de expiracion de trials finalizado.'))
