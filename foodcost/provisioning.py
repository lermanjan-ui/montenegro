# -*- coding: utf-8 -*-
"""
Провижининг дефолтных справочников нового арендатора — ПО ТИПУ БИЗНЕСА.

Набор способов оплаты, источников заказа и причин отмены зависит от
business_type арендатора (retail / restaurant / services), чтобы клиент сразу
получил осмысленную под свой профиль преднастройку. Идемпотентно (get_or_create
по tenant+name) — повторный вызов не дублирует. Безопасно звать из самрегистрации
и из консоли платформы.

Обратная совместимость: business_type можно не передавать — берётся из
tenant.business_type (по умолчанию «ресторан»), так что старые вызовы
provision_tenant_defaults(tenant) работают как раньше.
"""

# Источники заказа по типу бизнеса
_ORDER_SOURCES = {
    "restaurant": ("Зал", "Телефон", "Доставка", "Самовывоз"),
    "retail": ("Касса",),
    "services": ("Запись", "На месте"),
}
# Причины отмены по типу бизнеса
_CANCEL_REASONS = {
    "restaurant": ("Клиент отменил", "Ошибка оператора", "Нет продуктов"),
    "retail": ("Клиент отменил", "Ошибка кассира", "Возврат"),
    "services": ("Клиент не пришёл", "Отмена записи", "Ошибка оператора"),
}


def provision_tenant_defaults(tenant, business_type=None):
    """Создаёт дефолтные справочники под тип бизнеса. Возвращает сводку dict."""
    from .models import PaymentMethod, OrderSource

    bt = business_type or getattr(tenant, "business_type", None) or "restaurant"
    created = {"payment_methods": 0, "order_sources": 0, "cancel_reasons": 0}

    # --- Способы оплаты (общие для всех) ---
    cash, c1 = PaymentMethod.objects.get_or_create(
        country=tenant, name="Наличные",
        defaults={"is_cash": True, "is_active": True})
    created["payment_methods"] += int(c1)
    _card, c2 = PaymentMethod.objects.get_or_create(
        country=tenant, name="Карта / терминал",
        defaults={"is_cash": False, "is_active": True})
    created["payment_methods"] += int(c2)
    # Услугам и опту часто нужен безналичный перевод
    if bt in ("services",):
        _tr, c3 = PaymentMethod.objects.get_or_create(
            country=tenant, name="Перевод",
            defaults={"is_cash": False, "is_active": True})
        created["payment_methods"] += int(c3)

    # --- Источники заказа (с дефолтным способом оплаты — наличные) ---
    for src_name in _ORDER_SOURCES.get(bt, _ORDER_SOURCES["retail"]):
        _src, c = OrderSource.objects.get_or_create(
            country=tenant, name=src_name,
            defaults={"is_active": True, "default_payment_method": cash})
        created["order_sources"] += int(c)

    # --- Причины отмены (необязательная модель — тихо пропускаем, если нет) ---
    try:
        from .models import OrderCancelReason
        for reason in _CANCEL_REASONS.get(bt, _CANCEL_REASONS["retail"]):
            _r, c = OrderCancelReason.objects.get_or_create(
                country=tenant, name=reason, defaults={"is_active": True})
            created["cancel_reasons"] += int(c)
    except Exception:
        pass

    return created
