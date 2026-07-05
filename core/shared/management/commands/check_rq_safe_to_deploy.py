import django_rq
from django.core.management.base import BaseCommand
from rq.registry import StartedJobRegistry
from rq.worker import Worker


class Command(BaseCommand):
    help = (
        'Verifica si es seguro recrear el contenedor rqworker ahora mismo: revisa si hay '
        'jobs activamente corriendo (no solo agendados). Reiniciar rqworker mata en seco '
        'cualquier job en ejecucion, sin darle chance de terminar limpio. '
        'Exit code 0 = seguro reiniciar. Exit code 1 = hay jobs corriendo, esperar.'
    )

    def handle(self, *args, **options):
        queue = django_rq.get_queue('default')
        connection = django_rq.get_connection('default')
        registry = StartedJobRegistry(queue=queue)
        job_ids = registry.get_job_ids()

        if not job_ids:
            self.stdout.write(self.style.SUCCESS(
                'Seguro reiniciar rqworker — no hay jobs corriendo ahora mismo.'
            ))
            return

        self.stdout.write(self.style.WARNING(
            f'NO reinicies rqworker todavia — {len(job_ids)} job(s) corriendo:'
        ))
        for jid in job_ids:
            job = queue.fetch_job(jid)
            if job:
                self.stdout.write(f'  - {job.func_name}({", ".join(str(a) for a in job.args)})')

        workers = Worker.all(connection=connection)
        busy = [w for w in workers if w.get_state() == 'busy']
        self.stdout.write(f'{len(busy)}/{len(workers)} workers ocupados.')
        raise SystemExit(1)
