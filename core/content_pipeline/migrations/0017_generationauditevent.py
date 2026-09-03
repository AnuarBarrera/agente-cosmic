from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ('brand_dna', '0015_productreferenceasset'),
        ('content_pipeline', '0016_calendar_first_download_at'),
    ]

    operations = [
        migrations.CreateModel(
            name='GenerationAuditEvent',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('stage', models.CharField(max_length=100)),
                ('attempt', models.PositiveIntegerField(default=1)),
                ('decision', models.CharField(choices=[('started', 'Iniciado'), ('accepted', 'Aceptado'), ('rejected', 'Rechazado'), ('fallback', 'Fallback'), ('error', 'Error'), ('skipped', 'Omitido')], max_length=20)),
                ('flags', models.JSONField(blank=True, default=dict)),
                ('prompt_hash', models.CharField(blank=True, default='', max_length=64)),
                ('response_hash', models.CharField(blank=True, default='', max_length=64)),
                ('prompt_preview', models.TextField(blank=True, default='')),
                ('response_preview', models.TextField(blank=True, default='')),
                ('duration_ms', models.PositiveIntegerField(blank=True, null=True)),
                ('provider', models.CharField(blank=True, default='', max_length=50)),
                ('model', models.CharField(blank=True, default='', max_length=150)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='generation_audit_events', to='brand_dna.analysisjob')),
                ('post', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='generation_audit_events', to='content_pipeline.contentpost')),
                ('reference_asset', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='generation_audit_events', to='brand_dna.productreferenceasset')),
            ],
            options={'db_table': 'content_pipeline_generation_audit_event', 'ordering': ['created_at']},
        ),
        migrations.AddIndex(model_name='generationauditevent', index=models.Index(fields=['job', 'stage', 'created_at'], name='audit_job_stage_idx')),
        migrations.AddIndex(model_name='generationauditevent', index=models.Index(fields=['decision', 'created_at'], name='audit_decision_idx')),
    ]
