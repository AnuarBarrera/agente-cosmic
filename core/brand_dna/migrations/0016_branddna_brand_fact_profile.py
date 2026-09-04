# Generated migration for brand_fact_profile field
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('brand_dna', '0015_productreferenceasset')]

    operations = [
        migrations.AddField(
            model_name='branddna',
            name='brand_fact_profile',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
