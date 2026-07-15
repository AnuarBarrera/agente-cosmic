from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('brand_dna', '0006_add_business_description'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='analysisjob',
            name='product_image_path',
        ),
        migrations.RemoveField(
            model_name='analysisjob',
            name='product_image_paths',
        ),
    ]
