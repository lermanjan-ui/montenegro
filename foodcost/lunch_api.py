"""
🍽️ Публичные эндпоинты «Обед дня» (комплексные обеды) — сайт и приложение.

  GET /api/public/lunch/days            — активные дни (переключатель)
  GET /api/public/lunch?date=YYYY-MM-DD — меню обеда на день (без даты → сегодня)
  GET /api/public/lunch/<date>          — то же, дата в пути

Ответы в обёртке { success, data }. Деньги — целые суммы в UZS. Фото блюда —
URL или null (без заглушки, см. §5 ТЗ). Переиспользует helpers из public_api.
"""

import datetime
from decimal import Decimal

from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import LunchMenu, LunchCorporateTier
from .public_api import (
    api_success,
    api_error,
    get_public_country,
    _resolve_image,
    _display_name,
    _to_float,
    _coerce_int,
)

_WEEKDAYS_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
_DAYS_AHEAD = 14  # горизонт переключателя дней


def _today():
    return timezone.localdate()


def _weekday_ru(d):
    return _WEEKDAYS_RU[d.weekday()]


def _label_for(d, today):
    if d == today:
        return "Сегодня"
    if d == today + datetime.timedelta(days=1):
        return "Завтра"
    return _weekday_ru(d)


def _money(value):
    """Деньги — целым числом UZS (фронт не считает)."""
    try:
        return int(round(float(value or 0)))
    except (TypeError, ValueError):
        return 0


def _parse_date(raw):
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        return None


def _dish_photo(request, dish):
    return _resolve_image(request, dish.photo, getattr(dish, "photo_url", "") or "")


def _serialize_slot(request, menu, slot):
    """Слот состава: блюдо из системы (имя+фото) или просто текст (id/photo = null)."""
    dish = menu.slot_dish(slot)
    if dish is not None:
        return {
            "slot": slot,
            "category_label": menu.SLOT_LABELS[slot],
            "dish": {
                "id": dish.id,
                "name": _display_name(dish),
                "photo": _dish_photo(request, dish),
            },
        }
    return {
        "slot": slot,
        "category_label": menu.SLOT_LABELS[slot],
        "dish": {
            "id": None,
            "name": menu.slot_text(slot),
            "photo": None,
        },
    }


def _serialize_upsell(request, up):
    dish = up.dish
    return {
        "id": dish.id,  # = dish_id: кладётся в корзину как обычный товар
        "name": _display_name(dish),
        "price": _money(getattr(dish, "selling_price", 0)),
        "photo": _dish_photo(request, dish),
        "sort_order": up.sort_order,
    }


def _serialize_tiers(country):
    rows = LunchCorporateTier.objects.filter(country=country).order_by("min_qty")
    return [
        {"min_qty": t.min_qty, "discount_percent": _to_float(t.discount_percent)}
        for t in rows
    ]


@csrf_exempt
def lunch_days(request):
    """Список активных дней с меню (для переключателя «Сегодня | Завтра | …»)."""
    country, err = get_public_country(request)
    if err:
        return err

    today = _today()
    horizon = today + datetime.timedelta(days=_DAYS_AHEAD)
    menus = (
        LunchMenu.objects
        .filter(country=country, is_active=True, date__gte=today, date__lte=horizon)
        .order_by("date")
    )
    days = [
        {
            "date": m.date.isoformat(),
            "label": _label_for(m.date, today),
            "weekday": _weekday_ru(m.date),
            "is_active": True,
        }
        for m in menus
    ]
    return api_success({"days": days})


@csrf_exempt
def lunch_menu(request, date=None):
    """Меню обеда на конкретный день (или сегодняшний, если дата не указана)."""
    country, err = get_public_country(request)
    if err:
        return err

    raw = date if date is not None else request.GET.get("date")
    if raw:
        target = _parse_date(raw)
        if target is None:
            return api_error("INVALID_DATE", "Неверный формат даты (YYYY-MM-DD)", status=400)
    else:
        target = _today()

    menu = (
        LunchMenu.objects
        .filter(country=country, date=target, is_active=True)
        .select_related("soup_dish", "main_dish", "salad_dish", "drink_dish")
        .first()
    )
    if menu is None:
        return api_error("LUNCH_NOT_FOUND", "Меню на этот день не задано", status=404)

    today = _today()
    items = [_serialize_slot(request, menu, slot) for slot in menu.SLOTS]
    composition_summary = " • ".join(menu.SLOT_LABELS[slot] for slot in menu.SLOTS)
    upsell = [
        _serialize_upsell(request, up)
        for up in menu.upsells.select_related("dish").order_by("sort_order", "id")
    ]

    return api_success({
        "date": menu.date.isoformat(),
        "is_active": menu.is_active,
        "badge": _label_for(menu.date, today),
        "delivery_from": menu.delivery_from or "",
        "title": menu.title or "Обед дня",
        "composition_summary": composition_summary,
        "combo_id": menu.id,
        "combo_price": _money(menu.combo_price),
        "separate_price": _money(menu.separate_price),
        "savings": _money(menu.savings),
        "items": items,
        "upsell": upsell,
        "corporate": {"tiers": _serialize_tiers(country)},
    })


# ---------------------------------------------------------------------------
# Корзина/заказ: расчёт позиций-комбо и корпоративная скидка
# ---------------------------------------------------------------------------
def split_cart_items(items):
    """Разделить позиции корзины на блюда и комбо.

    Комбо — позиция с непустым lunch_combo_id; остальное считается блюдом и
    идёт прежним путём (_validate_and_price_cart) без изменений.
    """
    dish_items, combo_items = [], []
    for it in (items or []):
        if isinstance(it, dict) and it.get("lunch_combo_id"):
            combo_items.append(it)
        else:
            dish_items.append(it)
    return dish_items, combo_items


def price_combos(request, country, combo_items):
    """Оценить позиции-комбо.

    Возвращает (result, error). result:
      { "lines": [...], "subtotal": Decimal, "total_qty": int,
        "objects": [ {menu, quantity, unit_price, line_total}, ... ] }
    """
    lines = []
    objects = []
    subtotal = Decimal("0")
    total_qty = 0

    for index, raw in enumerate(combo_items or []):
        combo_id = _coerce_int(raw.get("lunch_combo_id"))
        if not combo_id:
            return None, api_error(
                "INVALID_REQUEST", "Позиция комбо без lunch_combo_id",
                details={"index": index}, status=400,
            )
        quantity = _coerce_int(raw.get("quantity"), default=1) or 1
        if quantity < 1:
            return None, api_error(
                "INVALID_QUANTITY", "Количество должно быть ≥ 1",
                details={"index": index, "lunch_combo_id": combo_id}, status=400,
            )
        menu = (
            LunchMenu.objects
            .filter(id=combo_id, country=country, is_active=True)
            .select_related("soup_dish", "main_dish", "salad_dish", "drink_dish")
            .first()
        )
        if menu is None:
            return None, api_error(
                "LUNCH_NOT_FOUND", "Комбо недоступно или меню не задано",
                details={"index": index, "lunch_combo_id": combo_id}, status=404,
            )

        unit = Decimal(menu.combo_price or 0)
        line_total = unit * Decimal(quantity)
        subtotal += line_total
        total_qty += quantity

        composition = menu.composition_names()
        lines.append({
            "type": "lunch_combo",
            "lunch_combo_id": menu.id,
            "date": menu.date.isoformat(),
            "name": menu.title or "Обед дня",
            "quantity": quantity,
            "unit_price": _money(unit),
            "total_price": _money(line_total),
            "composition": composition,
        })
        objects.append({
            "menu": menu,
            "quantity": quantity,
            "unit_price": unit,
            "line_total": line_total,
            "composition": composition,
        })

    return {
        "lines": lines,
        "subtotal": subtotal,
        "total_qty": total_qty,
        "objects": objects,
    }, None


def corporate_discount(country, total_qty, combo_subtotal):
    """Корп-скидка на сумму комбо по наибольшему подходящему порогу.

    Возвращает (discount_amount: Decimal, applied_tier: dict|None).
    """
    if total_qty <= 0 or combo_subtotal <= 0:
        return Decimal("0"), None
    tier = (
        LunchCorporateTier.objects
        .filter(country=country, min_qty__lte=total_qty)
        .order_by("-min_qty")
        .first()
    )
    if tier is None:
        return Decimal("0"), None
    pct = Decimal(tier.discount_percent or 0)
    if pct <= 0:
        return Decimal("0"), None
    amount = (combo_subtotal * pct / Decimal("100")).quantize(Decimal("0.01"))
    if amount > combo_subtotal:
        amount = combo_subtotal
    return amount, {"min_qty": tier.min_qty, "discount_percent": _to_float(pct)}
