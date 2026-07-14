from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('tenant_management', '0018_user_reels_carousel_toggles'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='user',
            name='reels_enabled',
        ),
        migrations.RemoveField(
            model_name='user',
            name='carousel_enabled',
        ),
    ]
