import uuid
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('content_pipeline', '0003_contentpost_counts'),
    ]

    operations = [
        migrations.AddField(
            model_name='contentcalendar',
            name='active_product_images',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.CreateModel(
            name='WeeklyFeedback',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('week_number', models.IntegerField()),
                ('rating', models.IntegerField(blank=True, null=True)),
                ('comment', models.TextField(blank=True, default='')),
                ('continue_decision', models.CharField(choices=[('pending', 'Pendiente'), ('yes', 'Sí'), ('no', 'No')], default='pending', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('responded_at', models.DateTimeField(blank=True, null=True)),
                ('calendar', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='feedback_entries', to='content_pipeline.contentcalendar')),
            ],
            options={
                'db_table': 'content_pipeline_weekly_feedback',
                'ordering': ['week_number'],
            },
        ),
        migrations.AlterUniqueTogether(
            name='weeklyfeedback',
            unique_together={('calendar', 'week_number')},
        ),
    ]
