from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [('foodcost', '0107_subscriptioninvoice')]
    operations = [migrations.RenameModel('Country', 'Tenant')]
