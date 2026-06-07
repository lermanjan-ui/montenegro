"""Повторить заказ — подготовка корзины из прошлого заказа.

POST /api/app/orders/<public_order_number>/repeat   (нужен Bearer-токен)

Возвращает позиции исходного заказа, сопоставленные с ТЕКУЩИМ меню: для каждой
позиции — актуальная цена и признак доступности. Заказ при этом НЕ создаётся —
это только подготовка корзины. Доступно только владельцу заказа (иначе 404,
как у деталей заказа).

ВАЖНОЕ ОГРАНИЧЕНИЕ ПРО ДОБАВКИ.
Выбранные добавки/модификаторы в заказе НЕ сохраняются: при оформлении их
стоимость сворачивается в цену позиции, а сами id добавок нигде не фиксируются
(в OrderItem нет ни поля, ни связи с добавками; отдельных строк под добавки тоже
не создаётся). Добавки восстанавливаются из снимка (OrderItemAddon). Историческое примечание: чтобы «Повторить
заказ» восстанавливал и добавки, их нужно начать сохранять при оформлении
заказа (новая модель/поле + заполнение в order_create + миграция) — это отдельная
доработка, и она поможет только новым заказам (у старых данных о добавках нет).
"""

from decimal import Decimal

from django.views.decorators.csrf import csrf_exempt

from .models import Order
from .public_api import (
    api_success,
    api_error,
    is_dish_available,
    _is_addon_available,
    _display_name,
    _to_float,
)
from .app_auth import _authenticate


def _qty(value):
    """Количество как число: целое, если без дробной части, иначе float."""
    try:
        d = Decimal(str(value or 0))
    except Exception:
        return 0
    return int(d) if d == d.to_integral_value() else float(d)


@csrf_exempt
def repeat_order(request, public_order_number):
    customer, _row, err = _authenticate(request)
    if err:
        return err
    if request.method != "POST":
        return api_error("METHOD_NOT_ALLOWED", "Метод не поддерживается", status=405)

    number = (public_order_number or "").strip()
    order = Order.objects.filter(
        customer=customer, public_order_number=number
    ).first()
    if order is None:
        # Тот же ответ, что у деталей заказа: не палим существование чужих заказов.
        return api_error("NOT_FOUND", "Заказ не найден", status=404)

    items = []
    unavailable = 0

    for it in order.items.select_related("dish").prefetch_related(
        "addons__addon_dish"
    ):
        # Подарочные позиции акций (цена 0) не повторяем — промо пересчитается
        # в корзине при оформлении.
        if (it.price_snapshot or 0) == 0 and (it.total_price or 0) == 0:
            continue

        dish = it.dish
        available = (
            dish is not None
            and not getattr(dish, "is_archived", False)
            and is_dish_available(dish)
        )

        if dish is not None:
            name = _display_name(dish)
        else:
            # Блюдо удалили — показываем сохранённое в заказе имя как недоступное.
            name = getattr(it, "dish_name_snapshot", "") or ""

        # Добавки восстанавливаем из снимка заказа, сопоставляя с текущим меню.
        # Недоступную/снятую добавку молча опускаем; цену берём актуальную.
        addon_out = []
        for a in it.addons.all():
            ad = a.addon_dish
            if ad is None or not _is_addon_available(ad):
                continue
            addon_out.append({
                "id": ad.id,
                "name": _display_name(ad),
                "price": _to_float(ad.selling_price),
            })

        items.append({
            "dish_id": dish.id if dish is not None else None,
            "name": name,
            "quantity": _qty(it.quantity),
            "available": available,
            # Актуальная цена единицы для доступных; для недоступных — null.
            "unit_price": _to_float(dish.selling_price) if available else None,
            "addons": addon_out,
        })
        if not available:
            unavailable += 1

    return api_success({
        "items": items,
        "unavailable_count": unavailable,
        "currency": "UZS",
    })
