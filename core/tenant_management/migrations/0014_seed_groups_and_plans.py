from django.db import migrations


def seed_groups_and_plans(apps, schema_editor):
    Group = apps.get_model('auth', 'Group')
    Plan = apps.get_model('tenant_management', 'Plan')
    User = apps.get_model('tenant_management', 'User')

    admin_group, _ = Group.objects.get_or_create(name='admin')
    Group.objects.get_or_create(name='tester')
    user_group, _ = Group.objects.get_or_create(name='user')

    Plan.objects.get_or_create(
        name='Tester',
        defaults={
            'max_calendars_per_week': 5,
            'max_post_regenerations': 5,
            'max_post_edits': 5,
            'price': 0,
        },
    )

    for u in User.objects.filter(is_superuser=True):
        u.groups.add(admin_group)

    for u in User.objects.filter(is_superuser=False):
        if not u.groups.exists():
            u.groups.add(user_group)


def reverse_seed(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ('tenant_management', '0013_invitationcode'),
    ]

    operations = [
        migrations.RunPython(seed_groups_and_plans, reverse_seed),
    ]
