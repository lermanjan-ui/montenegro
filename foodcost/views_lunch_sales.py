"""
🍱📒 Журнал «Учёт обедов» (этап 2) — отдельный модуль.

  /c/<slug>/lunch-sales/   — список продаж обедов (ручные + с сайта),
  фильтры (период / точка / источник / клиент), метрики за период,
  добавление/редактирование ручной записи (состав из блюд + цена сета),
  выборка по клиенту за период.

Себестоимость и маржа считаются методами модели LunchSale.
"""

from decimal import Decimal, InvalidOperation
import datetime

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.db.models import Q

from .models import UserProfile, Location, Dish, Customer
from .models_lunch_sales import LunchSale, LunchSaleItem
from .views import get_country, require_section_access


LUNCH_SECTION = UserProfile.SECTION_FINANCE


def _int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _dec(value, default="0"):
    raw = str(value if value is not None else default).strip()
    raw = raw.replace(" ", "").replace("\u00a0", "").replace(",", ".")
    if raw == "":
        raw = default
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return Decimal(default)


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


def _sales_url(request, country):
    return request.POST.get("next") or f"/c/{country.slug}/lunch-sales/"


@login_required
def lunch_sales_list(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = require_section_access(request.user, LUNCH_SECTION)
    if access_error:
        return access_error

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "create_sale":
            LunchSale.objects.create(
                country=country,
                location=_loc(request.POST.get("location_id"), country),
                date=_date(request.POST.get("date")) or timezone.localdate(),
                customer_name=(request.POST.get("customer_name") or "").strip(),
                customer_phone=(request.POST.get("customer_phone") or "").strip(),
                title=(request.POST.get("title") or "").strip(),
                quantity=_int(request.POST.get("quantity"), 1) or 1,
                sale_price=_dec(request.POST.get("sale_price")),
                cost_override=(
                    _dec(request.POST.get("cost_override"))
                    if (request.POST.get("cost_override") or "").strip() != ""
                    else None
                ),
                comment=(request.POST.get("comment") or "").strip(),
                source=LunchSale.SOURCE_MANUAL,
                created_by=request.user,
            )
            return redirect(_sales_url(request, country))

        if action == "update_sale":
            sale = get_object_or_404(
                LunchSale, id=_int(request.POST.get("sale_id")), country=country
            )
            sale.location = _loc(request.POST.get("location_id"), country)
            sale.date = _date(request.POST.get("date")) or sale.date
            sale.customer_name = (request.POST.get("customer_name") or "").strip()
            sale.customer_phone = (request.POST.get("customer_phone") or "").strip()
            sale.title = (request.POST.get("title") or "").strip()
            sale.quantity = _int(request.POST.get("quantity"), 1) or 1
            sale.sale_price = _dec(request.POST.get("sale_price"))
            co = request.POST.get("cost_override")
            sale.cost_override = _dec(co) if (co or "").strip() != "" else None
            sale.comment = (request.POST.get("comment") or "").strip()
            sale.save()
            return redirect(_sales_url(request, country))

        if action == "delete_sale":
            LunchSale.objects.filter(
                id=_int(request.POST.get("sale_id")), country=country
            ).delete()
            return redirect(_sales_url(request, country))

        if action == "add_item":
            sale = get_object_or_404(
                LunchSale, id=_int(request.POST.get("sale_id")), country=country
            )
            dish_id = _int(request.POST.get("dish_id"))
            dish = Dish.objects.filter(id=dish_id, country=country).first() if dish_id else None
            LunchSaleItem.objects.create(
                sale=sale,
                dish=dish,
                name=(request.POST.get("name") or "").strip(),
                quantity=_dec(request.POST.get("quantity"), "1"),
            )
            return redirect(_sales_url(request, country))

        if action == "delete_item":
            LunchSaleItem.objects.filter(
                id=_int(request.POST.get("item_id")), sale__country=country
            ).delete()
            return redirect(_sales_url(request, country))

        if action == "mark_corporate":
            Customer.objects.filter(
                id=_int(request.POST.get("customer_id")), country=country
            ).update(is_corporate=True)
            return redirect(_sales_url(request, country))

        if action == "unmark_corporate":
            Customer.objects.filter(
                id=_int(request.POST.get("customer_id")), country=country
            ).update(is_corporate=False)
            return redirect(_sales_url(request, country))

        if action == "create_corporate":
            name = (request.POST.get("new_name") or "").strip()
            phone = (request.POST.get("new_phone") or "").strip()
            if name:
                existing = (
                    Customer.objects.filter(country=country, phone=phone).first()
                    if phone else None
                )
                if existing is not None:
                    existing.is_corporate = True
                    if not existing.name:
                        existing.name = name
                    existing.save(update_fields=["is_corporate", "name"])
                else:
                    Customer.objects.create(
                        country=country, name=name, phone=phone, is_corporate=True
                    )
            return redirect(_sales_url(request, country))

        return redirect(_sales_url(request, country))

    # ----- GET: фильтры -----
    f_from = request.GET.get("date_from", "")
    f_to = request.GET.get("date_to", "")
    f_location = request.GET.get("location_id", "")
    f_source = request.GET.get("source", "")
    f_customer = request.GET.get("customer", "").strip()

    qs = LunchSale.objects.filter(country=country)
    d_from = _date(f_from)
    d_to = _date(f_to)
    if d_from:
        qs = qs.filter(date__gte=d_from)
    if d_to:
        qs = qs.filter(date__lte=d_to)
    loc_id = _int(f_location)
    if loc_id:
        qs = qs.filter(location_id=loc_id)
    if f_source in (LunchSale.SOURCE_MANUAL, LunchSale.SOURCE_SITE):
        qs = qs.filter(source=f_source)
    if f_customer:
        qs = qs.filter(
            Q(customer_name__icontains=f_customer)
            | Q(customer_phone__icontains=f_customer)
        )
    qs = qs.select_related("location").prefetch_related("items__dish").order_by("-date", "-id")

    sales = list(qs)
    source_labels = dict(LunchSale.SOURCE_CHOICES)

    total_revenue = Decimal(0)
    total_cost = Decimal(0)
    total_sets = 0
    rows = []
    for s in sales:
        rev = s.revenue()
        cost = s.cost_total()
        total_revenue += rev
        total_cost += cost
        total_sets += (s.quantity or 0)
        rows.append({
            "sale": s,
            "source_label": source_labels.get(s.source, s.source),
            "composition": [
                {"id": it.id, "name": it.display_name(),
                 "dish_id": it.dish_id, "qty": it.quantity}
                for it in s.items.all()
            ],
            "per_set_cost_fmt": _money(s.per_set_cost()),
            "sale_price_fmt": _money(s.sale_price),
            "revenue_fmt": _money(rev),
            "cost_fmt": _money(cost),
            "margin": s.margin(),
            "margin_fmt": _money(s.margin()),
            "foodcost": s.foodcost_percent(),
            "date_value": s.date.strftime("%Y-%m-%d") if s.date else "",
        })

    total_margin = total_revenue - total_cost
    foodcost = (
        (total_cost / total_revenue * Decimal(100)).quantize(Decimal("0.1"))
        if total_revenue > 0 else Decimal(0)
    )
    avg_check = (
        (total_revenue / Decimal(len(sales))).quantize(Decimal("1"))
        if sales else Decimal(0)
    )

    dishes = list(Dish.objects.filter(country=country).order_by("name").values("id", "name"))
    locations = Location.objects.filter(country=country).order_by("site_sort_order", "name")

    # Корпоративные клиенты — для выпадающего выбора в записи.
    corporate_customers = list(
        Customer.objects.filter(country=country, is_corporate=True)
        .order_by("name").values("id", "name", "phone")
    )
    # Поиск клиента, чтобы отметить его корпоративным (по имени/телефону).
    cust_q = request.GET.get("cust_q", "").strip()
    customer_matches = []
    if cust_q:
        customer_matches = list(
            Customer.objects.filter(country=country)
            .filter(Q(name__icontains=cust_q) | Q(phone__icontains=cust_q))
            .exclude(is_corporate=True)
            .order_by("name")
            .values("id", "name", "phone")[:20]
        )

    context = {
        "country": country,
        "rows": rows,
        "dishes": dishes,
        "locations": locations,
        "source_choices": LunchSale.SOURCE_CHOICES,
        "corporate_customers": corporate_customers,
        "customer_matches": customer_matches,
        "cust_q": cust_q,
        # метрики
        "kpi_count": len(sales),
        "kpi_sets": total_sets,
        "kpi_revenue_fmt": _money(total_revenue),
        "kpi_cost_fmt": _money(total_cost),
        "kpi_margin_fmt": _money(total_margin),
        "kpi_margin_neg": total_margin < 0,
        "kpi_foodcost": foodcost,
        "kpi_avg_fmt": _money(avg_check),
        # фильтры
        "f_from": f_from, "f_to": f_to, "f_location": f_location,
        "f_source": f_source, "f_customer": f_customer,
        "today": timezone.localdate().strftime("%Y-%m-%d"),
    }
    return render(request, "foodcost/lunch_sales.html", context)


def _loc(value, country):
    lid = _int(value)
    if not lid:
        return None
    return Location.objects.filter(id=lid, country=country).first()
