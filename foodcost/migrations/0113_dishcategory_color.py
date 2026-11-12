from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("foodcost", "0112_dishcategory_show_in_pos"),
    ]

    operations = [
        migrations.AddField(
            model_name="dishcategory",
            name="color",
            field=models.CharField(blank=True, default="", max_length=7),
        ),
    ]
