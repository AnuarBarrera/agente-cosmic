from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('content_pipeline', '0010_contentpost_video_url_alter_contentpost_format'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='contentcalendar',
            name='active_product_images',
        ),
    ]
