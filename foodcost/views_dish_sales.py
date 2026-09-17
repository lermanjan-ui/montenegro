"""
📊 Продаваемость блюд (CRM) — отдельный модуль.

  /c/<slug>/dish-sales/  — по каждому блюду за период:
  продано (шт), выручка, себестоимость, прибыль, маржа %, цена.
  Фильтры: категория, период (с/по). Сортировка: кол-во / выручка /
  прибыль / маржинальность / цена.

Считается по позициям заказов (OrderItem) за период, исключая отменённые
и неоплаченные онлайн-заказы. Себестоимость и цена — снимки на момент заказа.
"""

from decimal import Decimal
import datetime

from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from django.utils import timezone
from django.db.models import Sum, F, DecimalField

from .models import UserProfile, Order, OrderItem, DishCategory
from .views import get_country, require_section_access


def _date(value):
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        return None


def _money(value):
    try:
        n = int(round(float(value or 0)))
    except (TypeError, ValueError):
        return "0"
    s = f"{abs(n):,}".replace(",", " ")
    return f"-{s}" if n < 0 else s


SORTS = {
    "qty": ("Кол-во продаж", lambda r: r["qty"]),
    "revenue": ("Выручка", lambda r: r["revenue"]),
    "profit": ("Прибыль", lambda r: r["profit"]),
    "margin": ("Маржинальность", lambda r: r["margin_pct"]),
    "price": ("Цена", lambda r: r["price"]),
}


@login_required(login_url="/login/")
def dish_sales(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = require_section_access(
        request.user, UserProfile.SECTION_ORDER_ANALYTICS
    )
    if access_error:
        return access_error

    today = timezone.localdate()
    # период по умолчанию — с начала текущего месяца по сегодня
    f_from = request.GET.get("date_from", "")
    f_to = request.GET.get("date_to", "")
    d_from = _date(f_from) or today.replace(day=1)
    d_to = _date(f_to) or today
    f_category = (request.GET.get("category_id") or "").strip()
    sort = request.GET.get("sort", "qty")
    if sort not in SORTS:
        sort = "qty"

    # заказы, которые считаем «продажами»: без отменённых и неоплаченных онлайн
    excluded = [s for s in [
        getattr(Order, "STATUS_CANCELLED", "cancelled"),
        getattr(Order, "STATUS_AWAITING_PAYMENT", "awaiting_payment"),
        getattr(Order, "STATUS_PAYMENT_FAILED", "payment_failed"),
    ] if s]

    qs = (
        OrderItem.objects
        .filter(order__country=country)
        .filter(order__order_date__date__gte=d_from, order__order_date__date__lte=d_to)
        .exclude(order__status__in=excluded)
    )
    if f_category:
        qs = qs.filter(dish__category_id=f_category)

    agg = qs.values(
        "dish_id", "dish__name", "dish__selling_price", "dish__category__name"
    ).annotate(
        qty=Sum("quantity"),
        revenue=Sum("total_price"),
        cost=Sum(F("cost_snapshot") * F("quantity"), output_field=DecimalField()),
    )

    rows = []
    tot_qty = Decimal(0)
    tot_rev = Decimal(0)
    tot_cost = Decimal(0)
    for a in agg:
        qty = a["qty"] or Decimal(0)
        revenue = a["revenue"] or Decimal(0)
        cost = a["cost"] or Decimal(0)
        profit = revenue - cost
        margin_pct = (
            (profit / revenue * Decimal(100)).quantize(Decimal("0.1"))
            if revenue > 0 else Decimal(0)
        )
        tot_qty += qty
        tot_rev += revenue
        tot_cost += cost
        rows.append({
            "name": a["dish__name"] or "Без названия",
            "category": a["dish__category__name"] or "—",
            "price": a["dish__selling_price"] or Decimal(0),
            "qty": qty,
            "revenue": revenue,
            "cost": cost,
            "profit": profit,
            "margin_pct": margin_pct,
            "price_fmt": _money(a["dish__selling_price"]),
            "revenue_fmt": _money(revenue),
            "cost_fmt": _money(cost),
            "profit_fmt": _money(profit),
        })

    rows.sort(key=SORTS[sort][1], reverse=True)

    tot_profit = tot_rev - tot_cost
    tot_margin = (
        (tot_profit / tot_rev * Decimal(100)).quantize(Decimal("0.1"))
        if tot_rev > 0 else Decimal(0)
    )

    context = {
        "country": country,
        "rows": rows,
        "categories": DishCategory.objects.filter(country=country).order_by("name"),
        "sort_options": [(k, v[0]) for k, v in SORTS.items()],
        # фильтры
        "f_from": d_from.strftime("%Y-%m-%d"),
        "f_to": d_to.strftime("%Y-%m-%d"),
        "f_category": f_category,
        "sort": sort,
        # итоги
        "kpi_positions": len(rows),
        "kpi_qty": tot_qty,
        "kpi_revenue_fmt": _money(tot_rev),
        "kpi_cost_fmt": _money(tot_cost),
        "kpi_profit_fmt": _money(tot_profit),
        "kpi_margin": tot_margin,
    }
    return render(request, "foodcost/dish_sales.html", context)
