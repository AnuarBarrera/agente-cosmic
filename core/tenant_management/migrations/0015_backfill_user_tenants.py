from django.db import migrations


def backfill_tenants(apps, schema_editor):
    User = apps.get_model('tenant_management', 'User')
    TenantModel = apps.get_model('tenant_management', 'TenantModel')
    Subscription = apps.get_model('tenant_management', 'Subscription')
    Plan = apps.get_model('tenant_management', 'Plan')

    free_plan = Plan.objects.filter(name='Free').first()
    if not free_plan:
        return

    for user in User.objects.filter(tenant__isnull=True):
        tenant = TenantModel.objects.create(name=user.email, status='active')
        Subscription.objects.create(tenant=tenant, plan=free_plan)
        user.tenant = tenant
        user.save(update_fields=['tenant'])


def reverse_backfill(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('tenant_management', '0014_seed_groups_and_plans'),
    ]

    operations = [
        migrations.RunPython(backfill_tenants, reverse_backfill),
    ]
