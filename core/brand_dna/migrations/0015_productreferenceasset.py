import hashlib

from django.db import migrations, models
import django.db.models.deletion
import uuid


def backfill_reference_assets(apps, schema_editor):
    AnalysisJob = apps.get_model('brand_dna', 'AnalysisJob')
    ProductReferenceAsset = apps.get_model('brand_dna', 'ProductReferenceAsset')
    assets = []
    for job in AnalysisJob.objects.iterator():
        seen = set()
        for position, path in enumerate(job.product_reference_image_paths or []):
            if not path:
                continue
            # Migrations must be deterministic and must never depend on GCS.
            # New uploads use the binary hash. This marker documents that the
            # historical value only deduplicates repeated legacy paths.
            fingerprint = hashlib.sha256(('legacy-path:' + path).encode()).hexdigest()
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            extension = path.rsplit('.', 1)[-1].lower() if '.' in path else ''
            mime_type = {
                'jpg': 'image/jpeg', 'jpeg': 'image/jpeg', 'png': 'image/png',
                'webp': 'image/webp', 'gif': 'image/gif',
            }.get(extension, '')
            assets.append(ProductReferenceAsset(
                job_id=job.pk,
                position=position,
                storage_path=path,
                sha256=fingerprint,
                mime_type=mime_type,
                usage_mode='preserve_only',
                triage_status='pending',
                risk_flags={
                    'legacy_backfill': True,
                    'sha256_source': 'storage_path',
                },
            ))
    ProductReferenceAsset.objects.bulk_create(assets, batch_size=500)


class Migration(migrations.Migration):
    dependencies = [('brand_dna', '0014_remove_analysisjob_product_reference_image_path_and_more')]

    operations = [
        migrations.CreateModel(
            name='ProductReferenceAsset',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('position', models.PositiveIntegerField()),
                ('storage_path', models.CharField(max_length=500)),
                ('sha256', models.CharField(max_length=64)),
                ('mime_type', models.CharField(blank=True, default='', max_length=100)),
                ('width', models.PositiveIntegerField(blank=True, null=True)),
                ('height', models.PositiveIntegerField(blank=True, null=True)),
                ('analysis_description', models.TextField(blank=True, default='')),
                ('product_category', models.CharField(blank=True, default='', max_length=100)),
                ('commercial_relationship', models.CharField(choices=[('maker', 'Fabricante'), ('reseller', 'Distribuidor'), ('service', 'Servicio'), ('unknown', 'Desconocida')], default='unknown', max_length=20)),
                ('usage_mode', models.CharField(choices=[('edit_allowed', 'Edicion creativa permitida'), ('preserve_only', 'Conservar pixeles originales'), ('context_only', 'Solo contexto')], default='preserve_only', max_length=20)),
                ('risk_flags', models.JSONField(blank=True, default=dict)),
                ('visible_brands', models.JSONField(blank=True, default=list)),
                ('visible_text_summary', models.TextField(blank=True, default='')),
                ('triage_status', models.CharField(choices=[('pending', 'Pendiente'), ('complete', 'Completo'), ('failed', 'Fallido')], default='pending', max_length=20)),
                ('triage_version', models.CharField(blank=True, default='', max_length=50)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('job', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='product_reference_assets', to='brand_dna.analysisjob')),
            ],
            options={'db_table': 'brand_dna_product_reference_asset', 'ordering': ['position', 'created_at']},
        ),
        migrations.AddConstraint(model_name='productreferenceasset', constraint=models.UniqueConstraint(fields=('job', 'position'), name='unique_job_asset_position')),
        migrations.AddConstraint(model_name='productreferenceasset', constraint=models.UniqueConstraint(fields=('job', 'sha256'), name='unique_job_asset_sha256')),
        migrations.AddIndex(model_name='productreferenceasset', index=models.Index(fields=['job', 'usage_mode'], name='asset_job_usage_idx')),
        migrations.AddIndex(model_name='productreferenceasset', index=models.Index(fields=['triage_status'], name='asset_triage_status_idx')),
        migrations.RunPython(backfill_reference_assets, migrations.RunPython.noop),
    ]
