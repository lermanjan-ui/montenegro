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

from .models import (
    UserProfile,
    Location,
    Order,
    StockMovement,
    PurchaseReceipt,
    PurchaseReceiptItem,
    Preparation,
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
    """Пересчёт автосписания за дату. Возвращает сводку.
    Идемпотентно: удаляет прежние движения-продажи за дату и создаёт заново."""
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

    # накапливаем расход: (loc_id, item_type, item_id) -> qty
    consumption = defaultdict(lambda: Decimal(0))
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
                consumption[(o.location_id, "product", pi.product_id)] += (pi.gross or Decimal(0)) * q
            for pp in it.dish.preparation_items.all():
                consumption[(o.location_id, "preparation", pp.preparation_id)] += (pp.gross or Decimal(0)) * q

    # последние закупочные цены по продуктам (один запрос)
    product_ids = [iid for (_, itype, iid) in consumption if itype == "product"]
    last_price = {}
    if product_ids:
        for pit in (
            PurchaseReceiptItem.objects
            .filter(receipt__status=PurchaseReceipt.STATUS_CONFIRMED, product_id__in=product_ids)
            .select_related("receipt")
            .order_by("product_id", "-receipt__receipt_date", "-receipt__confirmed_at", "-id")
        ):
            if pit.product_id not in last_price:
                last_price[pit.product_id] = pit.unit_price or Decimal(0)

    prep_ids = [iid for (_, itype, iid) in consumption if itype == "preparation"]
    prep_cost = {}
    if prep_ids:
        for p in Preparation.objects.filter(id__in=prep_ids):
            prep_cost[p.id] = p.cached_cost_per_kg or Decimal(0)

    dt = _aware(date_obj)
    label = date_obj.strftime("%d.%m.%Y")

    from django.db import transaction
    total_cost = Decimal(0)
    created = 0
    with transaction.atomic():
        # удаляем прежние движения-продажи за дату
        del_qs = StockMovement.objects.filter(
            country=country,
            source_type=StockMovement.SOURCE_ORDER,
            movement_datetime__date=date_obj,
        )
        if location_id:
            del_qs = del_qs.filter(warehouse_id=location_id)
        deleted = del_qs.count()
        del_qs.delete()

        for (loc, itype, iid), qty in consumption.items():
            if qty == 0:
                continue
            if itype == "product":
                unit_cost = last_price.get(iid, Decimal(0))
                cost = qty * unit_cost
                StockMovement.objects.create(
                    country=country, warehouse_id=loc,
                    item_type=StockMovement.ITEM_TYPE_PRODUCT, product_id=iid,
                    quantity_delta=-qty,
                    movement_type=StockMovement.TYPE_SALE,
                    source_type=StockMovement.SOURCE_ORDER, source_id=0,
                    unit_cost=unit_cost, total_cost=-cost,
                    comment=f"Автосписание продаж за {label}",
                    created_by=user, movement_datetime=dt,
                )
            else:
                unit_cost = prep_cost.get(iid, Decimal(0))
                cost = qty * unit_cost
                StockMovement.objects.create(
                    country=country, warehouse_id=loc,
                    item_type=StockMovement.ITEM_TYPE_PREPARATION, preparation_id=iid,
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
        date_obj = _parse_date(request.POST.get("date"))
        loc_id = request.POST.get("location_id") or None
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
