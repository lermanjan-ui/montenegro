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

from .models import LunchMenu, LunchCorporateTier, Lunch, LunchSize
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
    """Слот состава: блюдо из системы (имя+фото) или просто текст (id/photo = null).
    grams — вес порции; separate_price — цена этого блюда по отдельности (0, если не задано)."""
    grams = menu.slot_grams(slot)
    separate_price = _to_float(menu.slot_price(slot))
    dish = menu.slot_dish(slot)
    if dish is not None:
        return {
            "slot": slot,
            "category_label": menu.SLOT_LABELS[slot],
            "grams": grams,
            "separate_price": separate_price,
            "dish": {
                "id": dish.id,
                "name": _display_name(dish),
                "photo": _dish_photo(request, dish),
            },
        }
    return {
        "slot": slot,
        "category_label": menu.SLOT_LABELS[slot],
        "grams": grams,
        "separate_price": separate_price,
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


def _new_lunch_composition(size):
    """Состав размера списком строк: «Название (120 г)» или «Название»."""
    out = []
    for it in size.items.all():
        nm = it.display_name()
        w = (it.weight or "").strip()
        out.append(f"{nm} ({w})" if w else nm)
    return out


def price_combos(request, country, combo_items):
    """Оценить позиции-комбо (новый Lunch/LunchSize приоритетно, затем LunchMenu).

    Цена и состав — с сервера. objects[] несут явные поля для снапшота и
    автозаписи в журнал:
      { lunch_menu (LunchMenu|None), lunch_id, size_id, date, name, quantity,
        unit_price, unit_cost, line_total, composition }.
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
        size_id = _coerce_int(raw.get("lunch_combo_size_id"))

        # --- НОВЫЙ слой: Lunch + размеры ---
        lunch = (
            Lunch.objects.filter(id=combo_id, country=country, is_active=True).first()
        )
        if lunch is not None:
            if not lunch.available:
                return None, api_error(
                    "LUNCH_UNAVAILABLE", "Обед сейчас недоступен",
                    details={"index": index, "lunch_combo_id": combo_id}, status=409,
                )
            if size_id:
                size = LunchSize.objects.filter(id=size_id, lunch=lunch).first()
                if size is None:
                    return None, api_error(
                        "LUNCH_SIZE_NOT_FOUND",
                        "Размер не найден или не принадлежит этому обеду",
                        details={"index": index, "lunch_combo_id": combo_id,
                                 "lunch_combo_size_id": size_id}, status=404,
                    )
            else:
                size = lunch.default_size()
                if size is None:
                    return None, api_error(
                        "LUNCH_NO_SIZE", "У обеда нет размеров",
                        details={"index": index, "lunch_combo_id": combo_id}, status=404,
                    )

            base_price = Decimal(size.price or 0)
            base_cost = size.total_cost()

            # --- доп. порции по пунктам (аддендум) ---
            # extra_qty задаётся НА ОДНУ единицу обеда; сервер умножит на quantity.
            size_items = {it.id: it for it in size.items.all()}
            extras_sum = Decimal("0")
            extras_cost = Decimal("0")
            extras_snapshot = []
            for ex in (raw.get("item_extras") or []):
                item_id = _coerce_int((ex or {}).get("item_id"))
                ex_qty = _coerce_int((ex or {}).get("extra_qty"), default=0) or 0
                it = size_items.get(item_id)
                if it is None:
                    return None, api_error(
                        "LUNCH_ITEM_NOT_FOUND",
                        "Пункт не принадлежит выбранному размеру",
                        details={"index": index, "item_id": item_id}, status=404,
                    )
                if it.extra_price is None:
                    return None, api_error(
                        "LUNCH_ITEM_NO_EXTRA",
                        "Для этого пункта доп. порция недоступна",
                        details={"index": index, "item_id": item_id}, status=409,
                    )
                if ex_qty < 1 or (it.extra_max is not None and ex_qty > it.extra_max):
                    return None, api_error(
                        "INVALID_EXTRA_QTY", "Неверное количество доп. порций",
                        details={"index": index, "item_id": item_id,
                                 "extra_qty": ex_qty, "extra_max": it.extra_max},
                        status=400,
                    )
                ep = Decimal(it.extra_price or 0)
                extras_sum += ep * Decimal(ex_qty)
                extras_cost += it.extra_unit_cost() * Decimal(ex_qty)
                extras_snapshot.append({
                    "item_id": it.id,
                    "name": it.display_name(),
                    "extra_qty": ex_qty,
                    "extra_price": _money(ep),
                })

            unit = base_price + extras_sum          # combo_total за 1 ед. обеда (§3)
            unit_cost = base_cost + extras_cost
            line_total = unit * Decimal(quantity)
            subtotal += line_total
            total_qty += quantity
            composition = _new_lunch_composition(size)
            name = f"{lunch.name} ({size.label})" if size.label else lunch.name

            lines.append({
                "type": "lunch_combo",
                "lunch_combo_id": lunch.id,
                "lunch_combo_size_id": size.id,
                "date": lunch.date.isoformat() if lunch.date else None,
                "name": name,
                "quantity": quantity,
                "unit_price": _money(unit),
                "total_price": _money(line_total),
                "composition": composition,
                "extras": extras_snapshot,
            })
            objects.append({
                "menu": None,
                "lunch_menu": None,
                "lunch_id": lunch.id,
                "size_id": size.id,
                "date": lunch.date,
                "name": name,
                "quantity": quantity,
                "unit_price": unit,
                "unit_cost": unit_cost,
                "line_total": line_total,
                "composition": composition,
                "extras": extras_snapshot,
            })
            continue

        # --- СТАРЫЙ слой: LunchMenu («Обед дня») ---
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
            "lunch_menu": menu,
            "lunch_id": None,
            "size_id": None,
            "date": menu.date,
            "name": menu.title or "Обед дня",
            "quantity": quantity,
            "unit_price": unit,
            "unit_cost": Decimal("0"),
            "line_total": line_total,
            "composition": composition,
            "extras": [],
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


# ===========================================================================
#  🍱 Публичный список обедов (несколько на дату × размеры × состав)
#     GET /api/public/lunches?date=YYYY-MM-DD ; /<date> ; /dates
# ===========================================================================

def _serialize_lunch_size(size):
    return {
        "id": size.id,
        "label": size.label,
        "is_default": bool(size.is_default),
        "price": _money(size.price),
        "separate_price": _money(size.separate_price()),
        "savings": _money(size.savings()),
        "weight_total": size.weight_total or None,
        "items": [
            {
                "id": it.id,
                "name": it.display_name(),
                "weight": (it.weight or None),
                "role": it.role,
                "extra_price": (_money(it.extra_price) if it.extra_price is not None else None),
                "extra_weight": (it.extra_weight or None),
                "extra_max": it.extra_max,
            }
            for it in size.items.all()
        ],
    }


def _serialize_lunch_full(request, lunch):
    return {
        "id": lunch.id,
        "name": lunch.name,
        "photo": _resolve_image(request, lunch.photo, lunch.photo_url or ""),
        "description": (lunch.description or None),
        "badge": (lunch.badge or None),
        "available": bool(lunch.available),
        "sizes": [_serialize_lunch_size(s) for s in lunch.sizes.all()],
    }


@csrf_exempt
def lunches(request, date=None):
    """Список обедов-комплексов на дату."""
    country, err = get_public_country(request)
    if err:
        return err
    raw = date if date is not None else request.GET.get("date")
    today = _today()
    if raw:
        target = _parse_date(raw)
        if target is None:
            return api_error("INVALID_DATE", "Неверный формат даты (YYYY-MM-DD)", status=400)
    else:
        target = (
            Lunch.objects
            .filter(country=country, is_active=True, date__gte=today)
            .order_by("date").values_list("date", flat=True).first()
        ) or today

    rows = list(
        Lunch.objects
        .filter(country=country, date=target, is_active=True)
        .order_by("sort_order", "id")
        .prefetch_related(
            "sizes__items__dish", "sizes__items__preparation", "sizes__items__product"
        )
    )
    order_cutoff = ""
    for lu in rows:
        if (lu.order_cutoff or "").strip():
            order_cutoff = lu.order_cutoff.strip()
            break

    return api_success({
        "date": target.isoformat(),
        "order_cutoff": order_cutoff or None,
        "lunches": [_serialize_lunch_full(request, lu) for lu in rows],
    })


@csrf_exempt
def lunches_dates(request):
    """Доступные даты с обедами (селектор дней)."""
    country, err = get_public_country(request)
    if err:
        return err
    today = _today()
    horizon = today + datetime.timedelta(days=_DAYS_AHEAD)
    raw_dates = (
        Lunch.objects
        .filter(country=country, is_active=True, date__gte=today, date__lte=horizon)
        .order_by("date").values_list("date", flat=True).distinct()
    )
    seen = []
    for d in raw_dates:
        if d not in seen:
            seen.append(d)
    return api_success({
        "dates": [
            {"date": d.isoformat(), "label": _label_for(d, today), "weekday": _weekday_ru(d)}
            for d in seen
        ]
    })


# ===========================================================================
#  🛒 Апсейл на странице обедов — ОДИН список на всю страницу (не на обед).
#     GET /api/public/lunches/upsell?country_slug=uzbekistan
#  Источник — блюда с флагом show_in_combo_block («Комбо с фудкорта»).
#  Формат карточки — тот же serialize_product_card, что у витрины меню,
#  поэтому фронт рисует их как обычные товары; в заказ уходят как dish_id.
# ===========================================================================

@csrf_exempt
def lunches_upsell(request):
    country, err = get_public_country(request)
    if err:
        return err

    # локальные импорты — избегаем циклической зависимости с public_api
    from .public_api import serialize_product_card
    from .models import Dish

    dishes = (
        Dish.objects
        .filter(country=country, show_in_combo_block=True, is_visible_on_site=True)
        .exclude(is_stop_list=True)
        .order_by("name")
    )

    items = []
    for d in dishes:
        try:
            items.append(serialize_product_card(request, d))
        except Exception:
            continue

    return api_success({"upsell": items})
