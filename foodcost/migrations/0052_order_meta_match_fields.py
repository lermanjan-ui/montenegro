# Meta CAPI match-quality fields on Order (fbp / fbc / client_ip / user_agent).
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('foodcost', '0051_order_meta_capi_sent_order_meta_event_id_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='order',
            name='meta_fbp',
            field=models.CharField(blank=True, default='', max_length=255, help_text='Значение cookie _fbp от Meta Pixel (с фронта при оформлении).'),
        ),
        migrations.AddField(
            model_name='order',
            name='meta_fbc',
            field=models.CharField(blank=True, default='', max_length=255, help_text='Значение cookie _fbc / собранное из fbclid (с фронта при оформлении).'),
        ),
        migrations.AddField(
            model_name='order',
            name='meta_client_ip',
            field=models.CharField(blank=True, default='', max_length=64, help_text='IP клиента в момент оформления заказа (для user_data CAPI).'),
        ),
        migrations.AddField(
            model_name='order',
            name='meta_user_agent',
            field=models.CharField(blank=True, default='', max_length=512, help_text='User-Agent клиента в момент оформления заказа (для user_data CAPI).'),
        ),
    ]
