import pytest
from io import StringIO
from datetime import timedelta
from django.core.management import call_command
from django.contrib.auth import get_user_model
from django.utils import timezone
from core.tenant_management.models import TenantModel, Subscription, Plan
from core.brand_dna.models import AnalysisJob, BrandDNA

pytestmark = pytest.mark.django_db

UserModel = get_user_model()


def _plan(name, **overrides):
    defaults = dict(
        max_calendars_per_week=2, max_post_regenerations=2, max_post_edits=2,
        max_photo_prechecks_per_day=10, max_product_reference_photos=7,
        allows_sample_generation=False, price=0,
    )
    defaults.update(overrides)
    return Plan.objects.create(name=name, **defaults)


def _tester_with_jobs(email, job_count, plan):
    user = UserModel.objects.create_user(username=email, email=email, password='pass1234')
    tenant = TenantModel.objects.create(name=email, status='active')
    sub = Subscription.objects.create(tenant=tenant, plan=plan, status='active')
    user.tenant = tenant
    user.save(update_fields=['tenant'])
    jobs = []
    for i in range(job_count):
        job = AnalysisJob.objects.create(
            email=email, business_url='https://tuwebmx.com', user=user,
            status=AnalysisJob.STATUS_DONE, stage=AnalysisJob.STAGE_COMPLETE, progress=100,
        )
        # created_at tiene auto_now_add=True -- se fuerza el orden con update()
        # para no depender de sleeps entre creaciones.
        AnalysisJob.objects.filter(id=job.id).update(created_at=timezone.now() - timedelta(days=job_count - i))
        job.refresh_from_db()
        jobs.append(job)
    return user, sub, jobs


def test_dry_run_creates_no_founder_plan_and_changes_nothing():
    tester_plan = _plan('Tester')
    user, sub, jobs = _tester_with_jobs('t1@test.com', 2, tester_plan)

    out = StringIO()
    call_command(
        'migrate_testers_to_founder', '--payment-link-url=https://buy.stripe.com/founder123',
        stdout=out,
    )

    assert not Plan.objects.filter(name='Fundador').exists()
    sub.refresh_from_db()
    assert sub.plan == tester_plan
    assert sub.status == 'active'
    assert AnalysisJob.objects.filter(user=user).count() == 2
    assert '[dry-run]' in out.getvalue()


def test_apply_creates_founder_plan_copying_user_plan_limits():
    _plan('User', max_calendars_per_week=4, max_post_regenerations=5, max_post_edits=6,
          max_photo_prechecks_per_day=20, max_product_reference_photos=14,
          allows_sample_generation=True)
    tester_plan = _plan('Tester')
    _tester_with_jobs('t2@test.com', 1, tester_plan)

    out = StringIO()
    call_command(
        'migrate_testers_to_founder', '--payment-link-url=https://buy.stripe.com/founder123',
        '--apply', stdout=out,
    )

    founder = Plan.objects.get(name='Fundador')
    assert founder.max_calendars_per_week == 4
    assert founder.max_post_regenerations == 5
    assert founder.max_post_edits == 6
    assert founder.max_photo_prechecks_per_day == 20
    assert founder.max_product_reference_photos == 14
    assert founder.allows_sample_generation is True
    assert founder.stripe_payment_link_url == 'https://buy.stripe.com/founder123'


def test_apply_second_run_does_not_duplicate_founder_plan():
    _plan('User')
    tester_plan = _plan('Tester')
    _tester_with_jobs('t3@test.com', 1, tester_plan)

    call_command('migrate_testers_to_founder', '--payment-link-url=https://buy.stripe.com/v1', '--apply')
    call_command('migrate_testers_to_founder', '--payment-link-url=https://buy.stripe.com/v2', '--apply')

    assert Plan.objects.filter(name='Fundador').count() == 1
    assert Plan.objects.get(name='Fundador').stripe_payment_link_url == 'https://buy.stripe.com/v2'


def test_apply_prunes_all_but_most_recent_calendar():
    _plan('User')
    tester_plan = _plan('Tester')
    user, sub, jobs = _tester_with_jobs('t4@test.com', 3, tester_plan)
    most_recent = jobs[-1]

    call_command('migrate_testers_to_founder', '--payment-link-url=https://buy.stripe.com/founder123', '--apply')

    remaining = list(AnalysisJob.objects.filter(user=user))
    assert len(remaining) == 1
    assert remaining[0].id == most_recent.id


def test_apply_changes_plan_and_forces_trial_expired_status():
    _plan('User')
    tester_plan = _plan('Tester')
    user, sub, jobs = _tester_with_jobs('t5@test.com', 1, tester_plan)

    call_command('migrate_testers_to_founder', '--payment-link-url=https://buy.stripe.com/founder123', '--apply')

    sub.refresh_from_db()
    assert sub.plan.name == 'Fundador'
    assert sub.status == 'trial_expired'


def test_apply_ignores_non_tester_subscriptions():
    _plan('User')
    user_plan = Plan.objects.get(name='User')
    other_user, other_sub, _ = _tester_with_jobs('other@test.com', 1, user_plan)

    call_command('migrate_testers_to_founder', '--payment-link-url=https://buy.stripe.com/founder123', '--apply')

    other_sub.refresh_from_db()
    assert other_sub.plan.name == 'User'
    assert other_sub.status == 'active'


def test_apply_prunes_via_cascade_deleting_brand_dna_of_pruned_jobs():
    _plan('User')
    tester_plan = _plan('Tester')
    user, sub, jobs = _tester_with_jobs('t6@test.com', 2, tester_plan)
    older_job = jobs[0]
    BrandDNA.objects.create(
        job=older_job, business_name='Negocio viejo', business_url='https://tuwebmx.com',
        description='desc', keywords=['k'], audience='a', tone='profesional', primary_colors=['#000'],
    )

    call_command('migrate_testers_to_founder', '--payment-link-url=https://buy.stripe.com/founder123', '--apply')

    assert not BrandDNA.objects.filter(job=older_job).exists()


def test_apply_ignores_already_deleted_jobs_when_choosing_which_to_keep():
    # HALLAZGO 2026-08-21: el usuario borro (soft-delete) su calendario mas
    # reciente antes de la migracion, dejando uno activo mas viejo. Elegir
    # "el mas reciente" sin filtrar deleted_at habria borrado de verdad el
    # unico calendario activo y conservado uno ya invisible para el usuario.
    _plan('User')
    tester_plan = _plan('Tester')
    user, sub, jobs = _tester_with_jobs('t7@test.com', 2, tester_plan)
    most_recent, active_job = jobs[-1], jobs[0]
    most_recent.deleted_at = timezone.now()
    most_recent.save(update_fields=['deleted_at'])

    call_command('migrate_testers_to_founder', '--payment-link-url=https://buy.stripe.com/founder123', '--apply')

    remaining_ids = set(AnalysisJob.objects.filter(user=user).values_list('id', flat=True))
    assert active_job.id in remaining_ids
    assert AnalysisJob.objects.filter(id=active_job.id, deleted_at__isnull=True).exists()


def test_apply_leaves_all_already_deleted_jobs_untouched_when_no_active_ones_exist():
    # Mismo hallazgo: si TODOS los calendarios del tester ya estaban borrados
    # antes de migrar (caso real visto en vivo), el comando no debe tocar
    # ninguno -- no hay "el mas reciente" real que conservar ni que podar.
    _plan('User')
    tester_plan = _plan('Tester')
    user, sub, jobs = _tester_with_jobs('t8@test.com', 2, tester_plan)
    for job in jobs:
        job.deleted_at = timezone.now()
        job.save(update_fields=['deleted_at'])

    call_command('migrate_testers_to_founder', '--payment-link-url=https://buy.stripe.com/founder123', '--apply')

    assert AnalysisJob.objects.filter(user=user).count() == 2
    sub.refresh_from_db()
    assert sub.plan.name == 'Fundador'
    assert sub.status == 'trial_expired'