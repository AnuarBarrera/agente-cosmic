from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('brand_dna', '0002_add_user_to_analysisjob'),
    ]

    operations = [
        migrations.AddField(
            model_name='analysisjob',
            name='product_image_path',
            field=models.CharField(blank=True, default='', max_length=500),
        ),
    ]
