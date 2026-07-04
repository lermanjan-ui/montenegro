# =============================================================================
#  CUSTOMERS — обновлённые view для foodcost/order_views.py
# =============================================================================
#
#  Заменяет ДВЕ функции: customer_list (~стр. 1641) и customer_detail (~стр. 958).
#  Плюс добавляет два модульных helper'а (_fmt_money, _build_qs) и расширяет
#  импорты.
#
#  --------------------------------------------------------------------------
#  1) ИМПОРТЫ — поправь верх файла order_views.py:
#
#     Было:
#         from django.db.models import Sum, Count, F
#         from .views import (
#             get_country,
#             require_section_access,
#         )
#
#     Стало:
#         from datetime import timedelta
#         from django.core.paginator import Paginator
#         from django.db.models import (
#             Sum, Count, F, Q, Max, Min, Value,
#             Subquery, OuterRef, IntegerField, DecimalField,
#         )
#         from django.db.models.functions import Coalesce
#         from .views import (
#             get_country,
#             require_section_access,
#             user_can_edit,
#         )
#
#     (Decimal, timezone, render, redirect, get_object_or_404,
#      HttpResponseForbidden уже импортированы — не трогаем.)
#
#  2) МОДЕЛЬ Customer — добавь два поля (см. отдельный файл
#     customer_model_patch.py) и сделай миграцию.
# =============================================================================


# ----------------------------------------------------------------------------
#  Helpers (модульный уровень — положить рядом с clean_decimal)
# ----------------------------------------------------------------------------
def _fmt_money(value):
    """1234567 -> '1 234 567' (без копеек, узкий пробел как разделитель)."""
    try:
        return f"{Decimal(value):,.0f}".replace(",", "\u00a0")
    except Exception:
        return "0"


def _build_qs(get_params, drop=None, **changes):
    """Собирает querystring из текущих GET-параметров.

    - всегда сбрасывает page
    - drop="key" — убрать один параметр (для chip "удалить фильтр")
    - changes: key=value добавить/заменить, key=None удалить
    Возвращает строку вида '?a=1&b=2' или '?' если пусто.
    """
    p = get_params.copy()
    p.pop("page", None)
    if drop:
        p.pop(drop, None)
    for key, val in changes.items():
        if val is None:
            p.pop(key, None)
        else:
            p[key] = val
    enc = p.urlencode()
    return "?" + enc if enc else "?"


def _sort_link(get_params, col, current_sort, default_desc=True):
    """Метаданные для кликабельного заголовка-сортировки.

    Возвращает {"url": "?...", "arrow": "↑"/"↓"/""}.
    Клик переключает направление; неактивная колонка стартует с
    default-направления.
    """
    asc, desc = col, "-" + col
    if current_sort == desc:
        nxt, arrow = asc, "↓"
    elif current_sort == asc:
        nxt, arrow = desc, "↑"
    else:
        nxt, arrow = (desc if default_desc else asc), ""
    return {"url": _build_qs(get_params, sort=nxt), "arrow": arrow}


# ----------------------------------------------------------------------------
#  CUSTOMER LIST
# ----------------------------------------------------------------------------
@login_required(login_url="/login/")
def customer_list(request, country_slug):

    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_CUSTOMERS,
    )
    if access_error:
        return access_error

    can_edit = user_can_edit(request.user)

    # ---- создание клиента вручную (клиент заказал не через сайт) ----
    if request.method == "POST" and request.POST.get("action") == "create_customer":
        if not can_edit:
            return redirect(f"/c/{country.slug}/customers/")
        name = (request.POST.get("name") or "").strip()
        phone = (request.POST.get("phone") or "").strip()
        is_corp = bool(request.POST.get("is_corporate"))
        # Если телефон уже есть — не плодим дубль, открываем существующего.
        if phone:
            existing = Customer.objects.filter(country=country, phone=phone).first()
            if existing is not None:
                if is_corp and not existing.is_corporate:
                    existing.is_corporate = True
                    existing.save(update_fields=["is_corporate"])
                return redirect(f"/c/{country.slug}/customers/{existing.id}/")
        if name or phone:
            new_customer = Customer.objects.create(
                country=country,
                name=name or "Без имени",
                phone=phone,
                is_corporate=is_corp,
            )
            return redirect(f"/c/{country.slug}/customers/{new_customer.id}/?saved=1")
        return redirect(f"/c/{country.slug}/customers/")

    g = request.GET
    search = g.get("search", "").strip()
    period = g.get("period", "all")
    status = g.get("status", "all")
    min_orders = g.get("min_orders", "all")
    source = g.get("source", "all")
    amount = g.get("amount", "all")
    lunches = g.get("lunches", "all")
    sort = g.get("sort", "-last")

    try:
        per_page = int(g.get("per_page", 50))
    except (TypeError, ValueError):
        per_page = 50
    if per_page not in (25, 50, 100):
        per_page = 50

    now = timezone.now()
    money_field = DecimalField(max_digits=16, decimal_places=2)

    # ---- основной queryset: все агрегаты одним SQL (убирает N+1) ----
    # Все аннотации идут по ОДНОЙ связи (orders) -> один LEFT JOIN, без
    # размножения строк. site_orders / tilda_orders считаем по
    # is_legacy_import (надёжнее, чем сверять source.name строкой).
    customers = (
        Customer.objects
        .filter(country=country)
        .annotate(
            orders_count=Count("orders", distinct=True),
            total_spent=Coalesce(
                Sum("orders__total_amount"),
                Value(Decimal("0")),
                output_field=money_field,
            ) - Coalesce(
                Sum("orders__lunch_combo_subtotal"),
                Value(Decimal("0")),
                output_field=money_field,
            ) + Coalesce(
                Sum("orders__lunch_corporate_discount"),
                Value(Decimal("0")),
                output_field=money_field,
            ),
            last_order_date=Max("orders__order_date"),
            site_orders=Count(
                "orders",
                filter=Q(orders__is_legacy_import=False),
                distinct=True,
            ),
            tilda_orders=Count(
                "orders",
                filter=Q(orders__is_legacy_import=True),
                distinct=True,
            ),
            lunch_orders=Count(
                "orders__lunch_combos",
                distinct=True,
            ),
        )
    )

    if search:
        customers = customers.filter(
            Q(name__icontains=search) | Q(phone__icontains=search)
        )

    if status == "regular":
        customers = customers.filter(is_regular=True)
    elif status == "problematic":
        customers = customers.filter(is_problematic=True)
    elif status == "blocked":
        customers = customers.filter(delivery_blocked=True)
    elif status == "normal":
        customers = customers.filter(
            is_regular=False,
            is_problematic=False,
            delivery_blocked=False,
        )

    if min_orders == "none":
        customers = customers.filter(orders_count=0)
    elif min_orders == "1":
        customers = customers.filter(orders_count__gte=1)
    elif min_orders == "5":
        customers = customers.filter(orders_count__gte=5)

    if source == "site":
        customers = customers.filter(site_orders__gt=0)
    elif source == "tilda":
        customers = customers.filter(tilda_orders__gt=0)

    if lunches == "yes":
        customers = customers.filter(lunch_orders__gt=0)

    # период = активность: последний заказ в окне.
    # (нужна дата регистрации вместо активности? замени last_order_date__gte
    # на created_at__gte.)
    period_days = {"month": 30, "quarter": 90, "year": 365}.get(period)
    if period_days:
        customers = customers.filter(
            last_order_date__gte=now - timedelta(days=period_days)
        )

    # топ-50 по тратам (внутри уже применённых фильтров)
    if amount == "top50":
        top_ids = list(
            customers.order_by("-total_spent").values_list("id", flat=True)[:50]
        )
        customers = customers.filter(id__in=top_ids)

    # ---- сортировка (NULL по датам/суммам всегда вниз) ----
    sort_map = {
        "name":    ["name", "id"],
        "-name":   ["-name", "id"],
        "orders":  [F("orders_count").asc(nulls_last=True), "name"],
        "-orders": [F("orders_count").desc(nulls_last=True), "name"],
        "spent":   [F("total_spent").asc(nulls_last=True), "name"],
        "-spent":  [F("total_spent").desc(nulls_last=True), "name"],
        "last":    [F("last_order_date").asc(nulls_last=True), "name"],
        "-last":   [F("last_order_date").desc(nulls_last=True), "name"],
        "reg":     [F("created_at").asc(nulls_last=True), "name"],
        "-reg":    [F("created_at").desc(nulls_last=True), "name"],
    }
    if sort not in sort_map:
        sort = "-last"
    customers = customers.order_by(*sort_map[sort])

    paginator = Paginator(customers, per_page)
    page_obj = paginator.get_page(g.get("page") or 1)

    # ---- строки для шаблона (только текущая страница) ----
    rows = []
    for c in page_obj:
        oc = c.orders_count or 0
        spent = c.total_spent or Decimal("0")
        avg = (spent / oc) if oc else Decimal("0")

        if c.is_problematic:
            status_label, status_class = "Проблемный", "fc-badge-red"
        elif c.is_regular:
            status_label, status_class = "Постоянный", "fc-badge-green"
        else:
            status_label, status_class = "Обычный", "fc-badge-gray"

        source_badges = []
        if c.site_orders:
            source_badges.append({"label": "Сайт", "cls": "fc-badge-blue"})
        if c.tilda_orders:
            source_badges.append({"label": "Tilda", "cls": "fc-badge-amber"})

        rows.append({
            "id": c.id,
            "name": c.name or "Без имени",
            "phone": c.phone,
            "orders_count": oc,
            "total_display": _fmt_money(spent),
            "avg_display": _fmt_money(avg),
            "last_order_date": c.last_order_date,
            "status_label": status_label,
            "status_class": status_class,
            "blocked": c.delivery_blocked,
            "source_badges": source_badges,
            "is_corporate": c.is_corporate,
            "ordered_lunches": (c.lunch_orders or 0) > 0,
        })

    # ---- KPI: фиксированное число запросов, по всей базе страны ----
    base = Customer.objects.filter(country=country)

    cust_agg = base.aggregate(
        total=Count("pk"),
        new_month=Count("pk", filter=Q(created_at__gte=now - timedelta(days=30))),
        problematic=Count("pk", filter=Q(is_problematic=True)),
    )

    order_count_sub = (
        Order.objects
        .filter(customer=OuterRef("pk"))
        .order_by()
        .values("customer")
        .annotate(c=Count("pk"))
        .values("c")
    )
    buckets = (
        base
        .annotate(oc=Coalesce(
            Subquery(order_count_sub, output_field=IntegerField()),
            Value(0),
        ))
        .aggregate(
            repeat=Count("pk", filter=Q(oc__gte=2)),
            vip=Count("pk", filter=Q(oc__gte=5)),
        )
    )

    order_agg = (
        Order.objects
        .filter(country=country)
        .aggregate(
            revenue=Sum("total_amount"),
            combo=Sum("lunch_combo_subtotal"),
            combo_disc=Sum("lunch_corporate_discount"),
            n=Count("pk"),
        )
    )
    revenue = (
        (order_agg["revenue"] or Decimal("0"))
        - (order_agg["combo"] or Decimal("0"))
        + (order_agg["combo_disc"] or Decimal("0"))
    )
    n_orders = order_agg["n"] or 0
    avg_check = (revenue / n_orders) if n_orders else Decimal("0")

    kpis = {
        "total": cust_agg["total"] or 0,
        "new_month": cust_agg["new_month"] or 0,
        "repeat": buckets["repeat"] or 0,
        "vip": buckets["vip"] or 0,
        "avg_check": _fmt_money(avg_check),
        "problematic": cust_agg["problematic"] or 0,
    }

    # ---- кликабельные заголовки-сортировки ----
    sort_links = {
        "name":   _sort_link(g, "name", sort, default_desc=False),
        "orders": _sort_link(g, "orders", sort),
        "spent":  _sort_link(g, "spent", sort),
        "last":   _sort_link(g, "last", sort),
        "reg":    _sort_link(g, "reg", sort),
    }

    # ---- chips активных фильтров (каждый ведёт на URL без этого фильтра) ----
    chips = []
    if search:
        chips.append({"label": f"Поиск: {search}", "url": _build_qs(g, drop="search")})
    period_labels = {"month": "Период: месяц", "quarter": "Период: квартал", "year": "Период: год"}
    if period in period_labels:
        chips.append({"label": period_labels[period], "url": _build_qs(g, drop="period")})
    status_labels = {
        "regular": "Статус: постоянный",
        "problematic": "Статус: проблемный",
        "blocked": "Статус: отказ в доставке",
        "normal": "Статус: обычный",
    }
    if status in status_labels:
        chips.append({"label": status_labels[status], "url": _build_qs(g, drop="status")})
    min_orders_labels = {"none": "Заказов: без заказов", "1": "Заказов: 1+", "5": "Заказов: 5+"}
    if min_orders in min_orders_labels:
        chips.append({"label": min_orders_labels[min_orders], "url": _build_qs(g, drop="min_orders")})
    source_labels = {"site": "Источник: сайт", "tilda": "Источник: Tilda"}
    if source in source_labels:
        chips.append({"label": source_labels[source], "url": _build_qs(g, drop="source")})
    if amount == "top50":
        chips.append({"label": "Топ-50 по тратам", "url": _build_qs(g, drop="amount")})

    # querystring без page — для ссылок пагинации (сохраняет фильтры/сортировку)
    pag = g.copy()
    pag.pop("page", None)
    base_qs = pag.urlencode()

    return render(request, "foodcost/customer_list.html", {
        "country": country,
        "rows": rows,
        "page_obj": page_obj,
        "base_qs": base_qs,
        "kpis": kpis,
        "sort_links": sort_links,
        "chips": chips,
        "reset_url": f"/c/{country.slug}/customers/",
        # эхо фильтров обратно в форму
        "search": search,
        "period": period,
        "status": status,
        "min_orders": min_orders,
        "source": source,
        "amount": amount,
        "lunches": lunches,
        "sort": sort,
        "per_page": per_page,
        "can_edit": can_edit,
    })


# ----------------------------------------------------------------------------
#  CUSTOMER DETAIL  (+ редактирование статусов, в т.ч. "отказ в доставке")
# ----------------------------------------------------------------------------
@login_required(login_url="/login/")
def customer_detail(request, country_slug, customer_id):

    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_CUSTOMERS,
    )
    if access_error:
        return access_error

    customer = get_object_or_404(
        Customer,
        id=customer_id,
        country=country,
    )

    can_edit = user_can_edit(request.user)

    # ---- сохранение статусов (только роли с правом редактирования) ----
    if request.method == "POST":
        if not can_edit:
            return HttpResponseForbidden("Недостаточно прав для изменения клиента")

        customer.is_regular = bool(request.POST.get("is_regular"))
        customer.is_problematic = bool(request.POST.get("is_problematic"))
        customer.is_corporate = bool(request.POST.get("is_corporate"))
        customer.delivery_blocked = bool(request.POST.get("delivery_blocked"))
        customer.delivery_block_reason = request.POST.get(
            "delivery_block_reason", ""
        ).strip()
        customer.comment = request.POST.get("comment", "").strip()
        customer.save(update_fields=[
            "is_regular",
            "is_problematic",
            "is_corporate",
            "delivery_blocked",
            "delivery_block_reason",
            "comment",
            "updated_at",
        ])
        return redirect(f"/c/{country.slug}/customers/{customer.id}/?saved=1")

    orders = (
        customer.orders
        .select_related("location", "payment_method")
        .order_by("-created_at")
    )

    total_orders = orders.count()
    _agg = orders.aggregate(
        total=Sum("total_amount"),
        combo=Sum("lunch_combo_subtotal"),
        combo_disc=Sum("lunch_corporate_discount"),
    )
    total_amount = (
        (_agg["total"] or Decimal("0"))
        - (_agg["combo"] or Decimal("0"))
        + (_agg["combo_disc"] or Decimal("0"))
    )
    average_check = (total_amount / total_orders) if total_orders else Decimal("0")

    order_rows = [{
        "id": o.id,
        "created_at": o.created_at,
        "sum_display": _fmt_money(o.total_amount),
        "location_name": o.location.name if o.location else "—",
    } for o in orders]

    return render(request, "foodcost/customer_detail.html", {
        "country": country,
        "customer": customer,
        "order_rows": order_rows,
        "total_orders": total_orders,
        "total_amount_display": _fmt_money(total_amount),
        "average_check_display": _fmt_money(average_check),
        "addresses_count": customer.addresses.count(),
        "can_edit": can_edit,
        "ordered_lunches": customer.orders.filter(lunch_combos__isnull=False).exists(),
        "saved": request.GET.get("saved") == "1",
    })
