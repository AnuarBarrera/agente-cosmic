from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tenant_management', '0017_alter_invitationcode_created_by'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='reels_enabled',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='user',
            name='carousel_enabled',
            field=models.BooleanField(default=True),
        ),
    ]
