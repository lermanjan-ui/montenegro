from django.apps import AppConfig


class PosConfig(AppConfig):
    """Отдельное приложение под кассу (POS).

    Держит ТОЛЬКО новые таблицы настроек кассы (кухонные экраны и пороги,
    обслуживание/чаевые, лояльность, зоны/столы, фискализация). Существующее
    приложение foodcost не трогаем — связь только через FK по строковым
    ссылкам ("foodcost.Tenant" и т.д.), поэтому порядок загрузки приложений
    и миграции foodcost остаются как есть.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "pos"
    verbose_name = "Касса (POS)"
