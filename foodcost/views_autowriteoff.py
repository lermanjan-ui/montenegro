"""
Складской модуль — автосписание продаж.

Движок: за выбранную дату собирает все НЕотменённые заказы, раскладывает
каждое блюдо по техкарте (продукты + заготовки, валовый расход gross ×
количество), агрегирует за день по (склад, позиция) и создаёт движения
склада типа «Списание по заказу» (sale, −количество).

Заготовка списывается КАК ЕДИНИЦА (техкарта заготовки не разворачивается).
Склад = склад заказа (order.location).

ИДЕМПОТЕНТНО: пересчёт за дату сначала удаляет прежние движения-продажи
(source=order) за эту дату (и склад, если задан), затем создаёт заново.
Поэтому повторный запуск безопасен, а отменённые с тех пор заказы просто
не воссоздаются.

Страница ручного пересчёта доступна только суперадмину. Этот же пересчёт
ночью будет дёргать внешний планировщик (настроим отдельно — Render Free
засыпает, нужен внешний крон).
"""

from decimal import Decimal
from collections import defaultdict
from datetime import datetime, time, timedelta

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.utils import timezone
from django.db.models.functions import TruncDate

from .models import (
    UserProfile,
    Location,
    Order,
    StockMovement,
    PurchaseReceipt,
    PurchaseReceiptItem,
    Preparation,
    ProductPrice,
)

from .views import get_country, require_section_access


def _fmt_money(value):
    try:
        value = Decimal(value or 0)
    except Exception:
        value = Decimal(0)
    negative = value < 0
    value = abs(value).quantize(Decimal("1"))
    s = f"{int(value):,}".replace(",", " ")
    return f"-{s}" if negative else s


def _parse_date(value):
    if not value:
        return None
    if hasattr(value, "year"):
        return value
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(str(value), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _aware(date_obj):
    """Дату → datetime на 23:00 этого дня (для корректной даты движения)."""
    naive = datetime.combine(date_obj, time(23, 0))
    try:
        if timezone.is_naive(naive):
            return timezone.make_aware(naive)
    except Exception:
        pass
    return naive


def recompute_sales_for_date(country, date_obj, location_id=None, user=None):
    """Пересчёт автосписания за дату. Идемпотентно.

    Списание заготовок по правилу:
      • есть остаток заготовки на складе → списываем заготовку;
      • остатка нет → раскрываем заготовку в продукты по её техкарте;
      • остатка не хватает → списываем доступный остаток заготовки, а недостающее —
        продуктами (рекурсивно: вложенные заготовки — по тому же правилу).
    """
    from django.db import transaction
    from django.db.models import Sum

    orders = (
        Order.objects
        .filter(country=country, order_date__date=date_obj, is_cancelled=False)
        .exclude(status=Order.STATUS_CANCELLED)
    )
    if location_id:
        orders = orders.filter(location_id=location_id)
    orders = orders.prefetch_related(
        "items__dish__product_items",
        "items__dish__preparation_items",
    ).select_related("location")

    # Сырой расход по техкартам блюд (валовый × количество).
    raw_product = defaultdict(lambda: Decimal(0))   # (loc, product_id) -> qty
    raw_prep = defaultdict(lambda: Decimal(0))      # (loc, prep_id) -> qty
    order_count = 0
    skipped_no_location = 0

    for o in orders:
        if not o.location_id:
            skipped_no_location += 1
            continue
        order_count += 1
        for it in o.items.all():
            if not it.dish_id:
                continue
            q = it.quantity or Decimal(0)
            if q == 0:
                continue
            for pi in it.dish.product_items.all():
                raw_product[(o.location_id, pi.product_id)] += (pi.gross or Decimal(0)) * q
            for pp in it.dish.preparation_items.all():
                raw_prep[(o.location_id, pp.preparation_id)] += (pp.gross or Decimal(0)) * q

    dt = _aware(date_obj)
    label = date_obj.strftime("%d.%m.%Y")

    total_cost = Decimal(0)
    created = 0
    with transaction.atomic():
        # удаляем прежние движения-продажи за дату (идемпотентность)
        del_qs = StockMovement.objects.filter(
            country=country,
            source_type=StockMovement.SOURCE_ORDER,
            movement_datetime__date=date_obj,
        )
        if location_id:
            del_qs = del_qs.filter(warehouse_id=location_id)
        deleted = del_qs.count()
        del_qs.delete()

        # ---- распределение заготовок: остаток заготовки vs раскрытие в продукты ----
        product_consumption = defaultdict(lambda: Decimal(0))   # (loc, product_id) -> qty
        prep_consumption = defaultdict(lambda: Decimal(0))      # (loc, prep_id) -> qty
        for key, qty in raw_product.items():
            product_consumption[key] += qty

        _bal = {}   # (loc, prep_id) -> доступный остаток заготовки (уменьшается при распределении)

        def _remaining(loc, prep_id):
            k = (loc, prep_id)
            if k not in _bal:
                _bal[k] = StockMovement.objects.filter(
                    country=country, warehouse_id=loc,
                    item_type=StockMovement.ITEM_TYPE_PREPARATION,
                    preparation_id=prep_id,
                ).aggregate(s=Sum("quantity_delta"))["s"] or Decimal(0)
            return _bal[k]

        _prep_obj = {}

        def _get_prep(prep_id):
            if prep_id not in _prep_obj:
                _prep_obj[prep_id] = (
                    Preparation.objects
                    .filter(id=prep_id)
                    .prefetch_related("items", "subitems")
                    .first()
                )
            return _prep_obj[prep_id]

        def _allocate(loc, prep_id, qty, depth=0):
            """Списать qty заготовки: со склада сколько есть, недостающее — в продукты."""
            if qty <= 0:
                return
            avail = _remaining(loc, prep_id)
            take = qty if qty < avail else (avail if avail > 0 else Decimal(0))
            if take > 0:
                prep_consumption[(loc, prep_id)] += take
                _bal[(loc, prep_id)] = avail - take
            short = qty - take
            if short <= 0:
                return
            prep = _get_prep(prep_id)
            # нельзя раскрыть (нет техкарты/веса/слишком глубоко) — спишем как заготовку
            if depth >= 10 or not prep or not prep.final_weight:
                prep_consumption[(loc, prep_id)] += short
                return
            ratio = short / prep.final_weight
            for item in prep.items.all():
                if item.product_id:
                    product_consumption[(loc, item.product_id)] += (item.gross or Decimal(0)) * ratio
            for sub in prep.subitems.all():
                if sub.sub_preparation_id:
                    _allocate(loc, sub.sub_preparation_id, (sub.gross or Decimal(0)) * ratio, depth + 1)

        for (loc, prep_id), qty in raw_prep.items():
            _allocate(loc, prep_id, qty)

        # ---- цены: продукт — последняя ProductPrice (как техкарты/остатки);
        #            заготовка — кэш техкарты, иначе живой расчёт ----
        product_ids = [pid for (_, pid) in product_consumption]
        last_price = {}
        if product_ids:
            for pp in (
                ProductPrice.objects
                .filter(product_id__in=product_ids)
                .order_by("product_id", "-date_from", "-id")
            ):
                if pp.product_id not in last_price:
                    last_price[pp.product_id] = pp.price or Decimal(0)

        prep_ids = [pid for (_, pid) in prep_consumption]
        prep_cost = {}
        if prep_ids:
            for p in Preparation.objects.filter(id__in=prep_ids):
                c = p.cached_cost_per_kg or Decimal(0)
                if not c:
                    try:
                        c = Decimal(str(p.cost_per_kg() or 0))
                    except Exception:
                        c = Decimal(0)
                prep_cost[p.id] = c

        # ---- создаём движения списания ----
        for (loc, pid), qty in product_consumption.items():
            if qty == 0:
                continue
            unit_cost = last_price.get(pid, Decimal(0))
            cost = qty * unit_cost
            StockMovement.objects.create(
                country=country, warehouse_id=loc,
                item_type=StockMovement.ITEM_TYPE_PRODUCT, product_id=pid,
                quantity_delta=-qty,
                movement_type=StockMovement.TYPE_SALE,
                source_type=StockMovement.SOURCE_ORDER, source_id=0,
                unit_cost=unit_cost, total_cost=-cost,
                comment=f"Автосписание продаж за {label}",
                created_by=user, movement_datetime=dt,
            )
            total_cost += cost
            created += 1

        for (loc, pid), qty in prep_consumption.items():
            if qty == 0:
                continue
            unit_cost = prep_cost.get(pid, Decimal(0))
            cost = qty * unit_cost
            StockMovement.objects.create(
                country=country, warehouse_id=loc,
                item_type=StockMovement.ITEM_TYPE_PREPARATION, preparation_id=pid,
                quantity_delta=-qty,
                movement_type=StockMovement.TYPE_SALE,
                source_type=StockMovement.SOURCE_ORDER, source_id=0,
                unit_cost=unit_cost, total_cost=-cost,
                comment=f"Автосписание продаж за {label}",
                created_by=user, movement_datetime=dt,
            )
            total_cost += cost
            created += 1

    return {
        "date": date_obj,
        "orders": order_count,
        "movements": created,
        "deleted": deleted,
        "total_cost": total_cost,
        "skipped_no_location": skipped_no_location,
    }


@login_required(login_url="/login/")
def auto_writeoff_page(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_STOCK)
    if access_error:
        return access_error

    profile = getattr(request.user, "profile", None)
    if not (profile and profile.is_super_admin()):
        from django.http import HttpResponseForbidden
        return HttpResponseForbidden("Доступно только главному администратору")

    result = None
    if request.method == "POST":
        loc_id = request.POST.get("location_id") or None
        action = request.POST.get("action") or "recompute_one"

        if action == "recompute_range":
            # Пересчёт за период (пустые даты = за всё время). Кнопка «Пересчитать склад».
            df = _parse_date(request.POST.get("date_from"))
            dt = _parse_date(request.POST.get("date_to"))
            qs = (
                Order.objects
                .filter(country=country, is_cancelled=False)
                .exclude(status=Order.STATUS_CANCELLED)
            )
            if loc_id:
                qs = qs.filter(location_id=loc_id)
            dates = sorted(
                d for d in set(
                    qs.annotate(_d=TruncDate("order_date")).values_list("_d", flat=True)
                ) if d
            )
            if df:
                dates = [d for d in dates if d >= df]
            if dt:
                dates = [d for d in dates if d <= dt]

            agg_orders = agg_moves = agg_deleted = 0
            agg_cost = Decimal(0)
            for d in dates:
                s = recompute_sales_for_date(country, d, location_id=loc_id, user=request.user)
                agg_orders += s["orders"]
                agg_moves += s["movements"]
                agg_deleted += s["deleted"]
                agg_cost += s["total_cost"]
            result = {
                "range": True,
                "days": len(dates),
                "date_span": (
                    f"{dates[0].strftime('%d.%m.%Y')} – {dates[-1].strftime('%d.%m.%Y')}"
                    if dates else "—"
                ),
                "orders": agg_orders,
                "movements": agg_moves,
                "deleted": agg_deleted,
                "total_cost_display": _fmt_money(agg_cost),
                "skipped": 0,
            }
        else:
            date_obj = _parse_date(request.POST.get("date"))
            if date_obj:
                summary = recompute_sales_for_date(country, date_obj, location_id=loc_id, user=request.user)
                result = {
                    "date": summary["date"].strftime("%d.%m.%Y"),
                    "orders": summary["orders"],
                    "movements": summary["movements"],
                    "deleted": summary["deleted"],
                    "total_cost_display": _fmt_money(summary["total_cost"]),
                    "skipped": summary["skipped_no_location"],
                }

    yesterday = (timezone.now().date() - timedelta(days=1))

    return render(request, "foodcost/auto_writeoff.html", {
        "country": country,
        "warehouses": Location.objects.filter(country=country).order_by("name"),
        "default_date": yesterday,
        "result": result,
    })
