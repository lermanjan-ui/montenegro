from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("foodcost", "0111_tenant_onboarding_modules"),
    ]

    operations = [
        migrations.AddField(
            model_name="dishcategory",
            name="show_in_pos",
            field=models.BooleanField(default=True),
        ),
    ]
