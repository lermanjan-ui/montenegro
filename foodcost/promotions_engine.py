"""Движок маркетинговых акций (этапы 2-4 контракта фронта).

Чистая логика расчёта вынесена отдельно, чтобы:
  - её можно было тестировать без БД;
  - и витрина (/products), и корзина (/cart/calculate/), и оформление
    (/order/create) считали акции ОДИНАКОВО (общий код).

Покрывает автоматические акции модели Promotion:
  percent_off, amount_off, buy_x_pay_y, gift.
Промокоды (PromoCode) считаются отдельно в _validate_and_price_cart, как и
раньше — здесь их нет.

ВАЖНО: деньги считает только бэк. Любые «бесплатные»/подарочные позиции от
клиента игнорируются (их добавляет этот движок).
"""

from decimal import Decimal

ITEM_TYPES = ("percent_off", "amount_off", "buy_x_pay_y")


def _q(value):
    if value is None:
        return Decimal("0")
    return Decimal(str(value))


def _money2(value):
    return Decimal(value).quantize(Decimal("0.01"))


def _fmt_int(value):
    """12000 -> '12 000' (неразрывный пробел как разделитель тысяч)."""
    try:
        return f"{int(Decimal(value)):,}".replace(",", "\u00a0")
    except Exception:
        return "0"


def _fmt_pct(value):
    d = _q(value)
    if d == d.to_integral_value():
        return str(int(d))
    return str(d.normalize())


# ---------------------------------------------------------------------------
#  Загрузка активных акций из БД -> список простых dict (specs).
#  ORM здесь; вся математика ниже работает только со specs, без БД.
# ---------------------------------------------------------------------------
def load_active_promotions(country_id):
    from .models import Promotion

    qs = (
        Promotion.objects
        .filter(country_id=country_id)
        .select_related("gift_dish")
        .prefetch_related("scope_dishes", "required_dishes", "excludes")
    )

    specs = []
    for p in qs:
        if not p.is_live_now():
            continue
        specs.append({
            "id": p.id,
            "type": p.type,
            "label": p.label or "",
            "style": p.style or "",
            "scope_type": p.scope_type or "all",
            "scope_ids": set(d.id for d in p.scope_dishes.all()),
            "percent": p.percent,
            "amount": p.amount,
            "buy_quantity": p.buy_quantity,
            "pay_quantity": p.pay_quantity,
            "threshold_amount": p.threshold_amount,
            "required_ids": set(d.id for d in p.required_dishes.all()),
            "gift_dish_id": p.gift_dish_id,
            "gift_dish_name": (p.gift_dish.name if p.gift_dish_id else ""),
            "gift_quantity": p.gift_quantity or 1,
            "stackable": bool(p.stackable),
            "priority": p.priority or 0,
            "exclude_ids": set(e.id for e in p.excludes.all()),
        })
    return specs


def _scope_matches(spec, dish_id):
    st = spec.get("scope_type", "all")
    if st == "all":
        return True
    if st == "include":
        return dish_id in spec["scope_ids"]
    if st == "exclude":
        return dish_id not in spec["scope_ids"]
    return False


def _choose_for_dish(specs, dish_id):
    """Какие item-акции применяются к строке (с учётом приоритета/стекинга)."""
    cand = [
        s for s in specs
        if s["type"] in ITEM_TYPES and _scope_matches(s, dish_id)
    ]
    cand.sort(key=lambda s: (-s["priority"], s["id"]))

    chosen = []
    for s in cand:
        if not chosen:
            chosen.append(s)
            if not s["stackable"]:
                break
            continue
        if not s["stackable"]:
            continue
        conflict = any(
            (s["id"] in c["exclude_ids"] or c["id"] in s["exclude_ids"])
            for c in chosen
        )
        if not conflict:
            chosen.append(s)
    return chosen


# ---------------------------------------------------------------------------
#  Расчёт по корзине. line_objects — список dict со ключами:
#     dish (объект с .id), quantity (int), base_price (Decimal, цена блюда без
#     добавок), addons_price (Decimal), per_unit (Decimal, base+addons).
#  Мутирует каждый line_object, добавляя:
#     per_unit_after, line_total_after, free_quantity, promo_id, promo_label.
#  Возвращает {promotions, gifts, auto_discount}.
# ---------------------------------------------------------------------------
def apply_to_cart(specs, line_objects):
    promo_discount_acc = {}   # promo_id -> Decimal вклад в скидку
    promo_applied = {}        # promo_id -> bool

    auto_discount_total = Decimal("0")
    qty_by_dish = {}

    for lo in line_objects:
        dish_id = lo["dish"].id
        qty = int(lo["quantity"])
        qty_by_dish[dish_id] = qty_by_dish.get(dish_id, 0) + qty

        base = _q(lo["base_price"])
        addons = _q(lo["addons_price"])
        per_unit = _q(lo["per_unit"])

        lo["free_quantity"] = 0
        lo["promo_id"] = None
        lo["promo_label"] = None

        chosen = _choose_for_dish(specs, dish_id)
        primary = chosen[0] if chosen else None

        # percent_off / amount_off — снижают цену блюда (без добавок)
        cur_base = base
        for s in chosen:
            if s["type"] == "percent_off" and s["percent"] is not None:
                new_base = cur_base * (Decimal("100") - _q(s["percent"])) / Decimal("100")
                if new_base < 0:
                    new_base = Decimal("0")
                delta = (cur_base - new_base) * qty
                if delta > 0:
                    promo_discount_acc[s["id"]] = promo_discount_acc.get(s["id"], Decimal("0")) + delta
                    promo_applied[s["id"]] = True
                cur_base = new_base
            elif s["type"] == "amount_off" and s["amount"] is not None:
                new_base = cur_base - _q(s["amount"])
                if new_base < 0:
                    new_base = Decimal("0")
                delta = (cur_base - new_base) * qty
                if delta > 0:
                    promo_discount_acc[s["id"]] = promo_discount_acc.get(s["id"], Decimal("0")) + delta
                    promo_applied[s["id"]] = True
                cur_base = new_base

        eff_per_unit = cur_base + addons

        # buy_x_pay_y — часть единиц бесплатна
        free_qty = 0
        bxp = next((s for s in chosen if s["type"] == "buy_x_pay_y"), None)
        if bxp and bxp["buy_quantity"] and bxp["pay_quantity"] is not None:
            bq = int(bxp["buy_quantity"])
            pq = int(bxp["pay_quantity"])
            if bq > 0 and 0 <= pq < bq:
                groups = qty // bq
                free_qty = groups * (bq - pq)
                if free_qty > 0:
                    free_val = eff_per_unit * free_qty
                    promo_discount_acc[bxp["id"]] = promo_discount_acc.get(bxp["id"], Decimal("0")) + free_val
                    promo_applied[bxp["id"]] = True

        paid_qty = qty - free_qty
        line_total_after = eff_per_unit * paid_qty

        lo["per_unit_after"] = eff_per_unit
        lo["line_total_after"] = line_total_after
        lo["free_quantity"] = free_qty
        if primary:
            lo["promo_id"] = primary["id"]
            lo["promo_label"] = primary["label"]

        auto_discount_total += (per_unit * qty - line_total_after)

    # ---- Подарки (gift) ----
    goods_after = sum((lo["line_total_after"] for lo in line_objects), Decimal("0"))
    cart_dish_ids = set(lo["dish"].id for lo in line_objects)

    gifts = []
    gift_states = {}
    for s in specs:
        if s["type"] != "gift":
            continue
        threshold = s["threshold_amount"]
        required = s["required_ids"]
        by_threshold = threshold is not None and _q(threshold) > 0
        by_required = len(required) > 0

        applied = False
        hint = None
        if by_required:
            if required.issubset(cart_dish_ids):
                applied = True
            else:
                hint = "Добавьте блюда из набора, чтобы получить подарок"
        elif by_threshold:
            if goods_after >= _q(threshold):
                applied = True
            else:
                left = _q(threshold) - goods_after
                hint = f"До подарка осталось {_fmt_int(left)} сум"

        if applied and s["gift_dish_id"]:
            gifts.append({
                "dish_id": s["gift_dish_id"],
                "name": s["gift_dish_name"],
                "quantity": int(s["gift_quantity"]),
                "auto_added": True,
                "promo_id": s["id"],
                "promo_label": s["label"],
            })
        gift_states[s["id"]] = (applied, hint)

    # ---- promotions[] (сводка применённых/доступных акций) ----
    promotions = []
    for s in specs:
        if s["type"] in ITEM_TYPES:
            applied = promo_applied.get(s["id"], False)
            disc = _money2(promo_discount_acc.get(s["id"], Decimal("0")))
            hint = None
            relevant = applied
            if s["type"] == "buy_x_pay_y" and not applied and s["buy_quantity"]:
                in_cart = sum(
                    q for d, q in qty_by_dish.items() if _scope_matches(s, d)
                )
                if in_cart > 0:
                    need = int(s["buy_quantity"]) - in_cart
                    if need > 0:
                        hint = f"Добавьте ещё {need} шт., чтобы сработала акция «{s['label']}»"
                        relevant = True
            if relevant:
                promotions.append({
                    "id": s["id"],
                    "type": s["type"],
                    "label": s["label"],
                    "applied": applied,
                    "discount_amount": float(disc),
                    "hint": hint,
                })
        elif s["type"] == "gift":
            applied, hint = gift_states.get(s["id"], (False, None))
            promotions.append({
                "id": s["id"],
                "type": "gift",
                "label": s["label"],
                "applied": applied,
                "discount_amount": 0,
                "hint": hint,
            })

    return {
        "promotions": promotions,
        "gifts": gifts,
        "auto_discount": _money2(auto_discount_total),
    }


# ---------------------------------------------------------------------------
#  Отображение акции в карточке товара (/products, /products/{slug}).
#  base_price — цена блюда (selling_price). Возвращает price/old_price/
#  savings_label/promo_hint/badges.
# ---------------------------------------------------------------------------
def display_for_dish(specs, dish_id, base_price, compare_at=None):
    base = _q(base_price)
    chosen = _choose_for_dish(specs, dish_id)

    cur = base
    for s in chosen:
        if s["type"] == "percent_off" and s["percent"] is not None:
            cur = cur * (Decimal("100") - _q(s["percent"])) / Decimal("100")
        elif s["type"] == "amount_off" and s["amount"] is not None:
            cur = cur - _q(s["amount"])
        if cur < 0:
            cur = Decimal("0")

    price = cur
    # «Было»: максимум из текущей цены и заданной вручную старой цены.
    was = base
    if compare_at is not None:
        ca = _q(compare_at)
        if ca > was:
            was = ca

    old_price = None
    savings_label = None
    if price < was:
        old_price = was
        savings_label = f"Экономия {_fmt_int(was - price)} сум"

    # подсказка для N+M
    promo_hint = None
    scoped = [s for s in specs if s["type"] in ITEM_TYPES and _scope_matches(s, dish_id)]
    bxp = next((s for s in scoped if s["type"] == "buy_x_pay_y"), None)
    if bxp and bxp["buy_quantity"] and bxp["pay_quantity"] is not None:
        bq = int(bxp["buy_quantity"])
        pq = int(bxp["pay_quantity"])
        if bq > pq:
            promo_hint = f"Возьмите {bq} — заплатите за {pq}"

    # бейджи
    badges = []
    for s in scoped:
        if s["type"] == "percent_off" and s["percent"] is not None:
            badges.append({
                "type": "discount",
                "label": f"-{_fmt_pct(s['percent'])}%",
                "style": s["style"] or "red",
            })
        elif s["type"] == "amount_off" and s["amount"] is not None:
            badges.append({
                "type": "discount",
                "label": f"-{_fmt_int(s['amount'])}",
                "style": s["style"] or "red",
            })
        elif s["type"] == "buy_x_pay_y":
            badges.append({
                "type": "promo",
                "label": s["label"],
                "style": s["style"] or "purple",
            })

    return {
        "price": price,
        "old_price": old_price,
        "savings_label": savings_label,
        "promo_hint": promo_hint,
        "badges": badges,
    }
