# -*- coding: utf-8 -*-
"""
Провижининг дефолтных справочников нового арендатора.

Чтобы только что зарегистрированный клиент мог СРАЗУ оформить заказ, досеваем
минимальный, но осмысленный набор справочников. Идемпотентно (get_or_create по
country+name) — повторный вызов ничего не дублирует. Безопасно звать и из
самрегистрации (signup_verify), и из консоли платформы (создание клиента).
"""


def provision_tenant_defaults(country):
    """Создаёт дефолтные способы оплаты, источники заказа и причины отмены.

    Возвращает dict с краткой сводкой (сколько чего создано/было).
    Никогда не падает «наружу» на отсутствии необязательных моделей.
    """
    from .models import PaymentMethod, OrderSource

    created = {"payment_methods": 0, "order_sources": 0, "cancel_reasons": 0}

    # --- Способы оплаты ---
    cash, c1 = PaymentMethod.objects.get_or_create(
        country=country, name="Наличные",
        defaults={"is_cash": True, "is_active": True})
    created["payment_methods"] += int(c1)
    _card, c2 = PaymentMethod.objects.get_or_create(
        country=country, name="Карта / терминал",
        defaults={"is_cash": False, "is_active": True})
    created["payment_methods"] += int(c2)

    # --- Источники заказа (с дефолтным способом оплаты — наличные) ---
    for src_name in ("Телефон", "Зал"):
        _src, c = OrderSource.objects.get_or_create(
            country=country, name=src_name,
            defaults={"is_active": True, "default_payment_method": cash})
        created["order_sources"] += int(c)

    # --- Причины отмены (необязательная модель — тихо пропускаем, если нет) ---
    try:
        from .models import OrderCancelReason
        for reason in ("Клиент отменил", "Ошибка оператора", "Нет продуктов"):
            _r, c = OrderCancelReason.objects.get_or_create(
                country=country, name=reason, defaults={"is_active": True})
            created["cancel_reasons"] += int(c)
    except Exception:
        pass

    return created
