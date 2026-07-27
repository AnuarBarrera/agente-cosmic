from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Envia correos de reactivacion a calendarios sin descargar y usuarios sin analizar su marca'

    def handle(self, *args, **options):
        from core.content_pipeline.tasks import send_reactivation_emails_task
        send_reactivation_emails_task()
        self.stdout.write(self.style.SUCCESS('Proceso de correos de reactivacion finalizado.'))
