from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('content_pipeline', '0017_generationauditevent'),
    ]

    operations = [
        migrations.AddField(
            model_name='contentpost',
            name='commercial_line',
            field=models.CharField(blank=True, default='', max_length=255),
        ),
    ]
