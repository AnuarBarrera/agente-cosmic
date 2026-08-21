import logging
from django.core.management.base import BaseCommand
from core.tenant_management.models import Plan, Subscription
from core.brand_dna.models import AnalysisJob

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        'Migra todas las Subscription con plan=Tester al plan Fundador (creado o '
        'actualizado con los limites del plan User + el Payment Link recibido), '
        'podando cada tenant a solo su AnalysisJob mas reciente y forzando '
        'status=trial_expired para que el boton de pago aparezca de inmediato. '
        'Dry-run por default -- requiere --apply para ejecutar de verdad.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--payment-link-url', required=True,
            help='Payment Link de Stripe del plan Fundador.',
        )
        parser.add_argument(
            '--apply', action='store_true',
            help='Ejecuta los cambios de verdad. Sin este flag, solo se imprime que se haria.',
        )

    def handle(self, *args, **options):
        payment_link_url = options['payment_link_url']
        apply_changes = options['apply']

        # Paso 1: crear/actualizar el plan Fundador (siempre, aunque no haya testers)
        # segun spec: "si ya existe (ej. segunda corrida), se actualiza solo
        # stripe_payment_link_url con el valor pasado — nunca se duplica"
        if apply_changes:
            user_plan = Plan.objects.get(name='User')
            founder_plan, created = Plan.objects.get_or_create(
                name='Fundador',
                defaults=dict(
                    max_daily_interactions=user_plan.max_daily_interactions,
                    max_monthly_interactions=user_plan.max_monthly_interactions,
                    max_calendars_per_week=user_plan.max_calendars_per_week,
                    max_post_regenerations=user_plan.max_post_regenerations,
                    max_post_edits=user_plan.max_post_edits,
                    max_photo_prechecks_per_day=user_plan.max_photo_prechecks_per_day,
                    max_product_reference_photos=user_plan.max_product_reference_photos,
                    allows_sample_generation=user_plan.allows_sample_generation,
                    price=user_plan.price,
                    stripe_payment_link_url=payment_link_url,
                ),
            )
            if not created:
                founder_plan.stripe_payment_link_url = payment_link_url
                founder_plan.save(update_fields=['stripe_payment_link_url'])
        else:
            self.stdout.write(
                f"[dry-run] Se crearia/actualizaria el plan 'Fundador' con "
                f"stripe_payment_link_url={payment_link_url!r}, copiando limites del plan 'User'."
            )

        # Paso 2: buscar suscripciones Tester
        subscriptions = (
            Subscription.objects
            .filter(plan__name='Tester')
            .select_related('tenant', 'plan')
        )
        if not subscriptions.exists():
            self.stdout.write('No hay suscripciones con plan Tester. Nada que hacer.')
            return

        for sub in subscriptions:
            tenant_jobs = list(
                AnalysisJob.objects.filter(user__tenant=sub.tenant).order_by('-created_at')
            )
            to_keep = tenant_jobs[0] if tenant_jobs else None
            to_prune = tenant_jobs[1:]

            if not apply_changes:
                self.stdout.write(
                    f"[dry-run] Tenant {sub.tenant.name}: plan Tester -> Fundador, "
                    f"status {sub.status!r} -> 'trial_expired', "
                    f"{len(to_prune)} calendario(s) a eliminar "
                    f"(se conserva {to_keep.id if to_keep else 'ninguno'})."
                )
                for job in to_prune:
                    self.stdout.write(f"    - borraria AnalysisJob {job.id} (creado {job.created_at})")
                continue

            # En modo apply, founder_plan ya existe (se creó arriba)
            for job in to_prune:
                job.delete()

            sub.plan = founder_plan
            sub.status = 'trial_expired'
            sub.save(update_fields=['plan', 'status'])
            logger.info(f"Tenant {sub.tenant.name} migrado a Fundador, {len(to_prune)} calendario(s) podado(s)")

        verb = 'Migrados' if apply_changes else '[dry-run] Se migrarian'
        self.stdout.write(self.style.SUCCESS(f'{verb} {subscriptions.count()} tester(s) al plan Fundador.'))