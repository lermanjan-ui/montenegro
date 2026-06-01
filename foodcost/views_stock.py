"""
Складской модуль — страница "Остатки" (только чтение).

Остатки НЕ хранятся в Product. Текущее количество рассчитывается из журнала
StockMovement как Σ quantity_delta по складу + позиции (продукт/заготовка).
Все агрегаты считаются групповыми запросами (values + annotate), без запроса
на каждый товар, чтобы выдерживать большие объёмы данных.

Оценка стоимости остатка:
  • продукт   — последняя ПОДТВЕРЖДЁННАЯ закупочная цена (PurchaseReceiptItem);
  • заготовка — себестоимость из техкарты (Preparation.cached_cost_per_kg).

Деньги (цена/стоимость/сумма) скрыты для роли кухни — показываем только
количества.
"""

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Sum, Q
from django.shortcuts import render, get_object_or_404

from .models import (
    UserProfile,
    Location,
    Product,
    Preparation,
    StockMovement,
    PurchaseReceipt,
    PurchaseReceiptItem,
    ProductPrice,
)

from .views import (
    get_country,
    require_section_access,
)


def _fmt_money(value):
    """1234567.5 -> '1 234 568' (пробел-разделитель, без копеек)."""
    try:
        value = Decimal(value or 0)
    except Exception:
        value = Decimal(0)
    negative = value < 0
    value = abs(value).quantize(Decimal("1"))
    s = f"{int(value):,}".replace(",", " ")
    return f"-{s}" if negative else s


def _fmt_qty(value):
    """Количество без хвостовых нулей: 125.000 -> '125', 3.200 -> '3.2'."""
    try:
        value = Decimal(value or 0)
    except Exception:
        value = Decimal(0)
    s = f"{value:.3f}".rstrip("0").rstrip(".")
    return s if s else "0"


@login_required(login_url="/login/")
def stock_list(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_STOCK,
    )
    if access_error:
        return access_error

    profile = getattr(request.user, "profile", None)

    # Деньги скрываем для кухни.
    show_money = not (profile and profile.is_kitchen_staff())

    # ----- фильтры -----
    warehouse_id = (request.GET.get("warehouse") or "").strip()
    search = (request.GET.get("search") or "").strip()
    only_low = request.GET.get("only_low") == "1"
    only_negative = request.GET.get("only_negative") == "1"

    try:
        per_page = int(request.GET.get("per_page") or 50)
    except (TypeError, ValueError):
        per_page = 50
    if per_page not in (25, 50, 100):
        per_page = 50

    locations = Location.objects.filter(country=country).order_by("name")
    loc_name = {loc.id: loc.name for loc in locations}

    base = StockMovement.objects.filter(country=country)
    if warehouse_id:
        base = base.filter(warehouse_id=warehouse_id)

    # ----- агрегаты остатков (по одному запросу на тип позиции) -----
    prod_balances = (
        base
        .filter(item_type=StockMovement.ITEM_TYPE_PRODUCT, product__isnull=False)
        .values("warehouse_id", "product_id")
        .annotate(qty=Sum("quantity_delta"))
    )

    prep_balances = (
        base
        .filter(item_type=StockMovement.ITEM_TYPE_PREPARATION, preparation__isnull=False)
        .values("warehouse_id", "preparation_id")
        .annotate(qty=Sum("quantity_delta"))
    )

    product_ids = {row["product_id"] for row in prod_balances}
    prep_ids = {row["preparation_id"] for row in prep_balances}

    products = {
        p.id: p
        for p in Product.objects.filter(id__in=product_ids)
    }
    preparations = {
        p.id: p
        for p in Preparation.objects.filter(id__in=prep_ids)
    }

    # Последняя подтверждённая закупочная цена по каждому продукту (один запрос).
    last_price = {}
    if product_ids:
        items = (
            PurchaseReceiptItem.objects
            .filter(
                receipt__status=PurchaseReceipt.STATUS_CONFIRMED,
                product_id__in=product_ids,
            )
            .select_related("receipt")
            .order_by(
                "product_id",
                "-receipt__receipt_date",
                "-receipt__confirmed_at",
                "-id",
            )
        )
        for it in items:
            if it.product_id not in last_price:
                last_price[it.product_id] = it.unit_price or Decimal(0)

    # ----- собираем строки -----
    rows = []

    for row in prod_balances:
        product = products.get(row["product_id"])
        if not product:
            continue

        qty = row["qty"] or Decimal(0)
        price = last_price.get(product.id, Decimal(0))
        value = qty * price
        min_stock = product.minimum_stock or Decimal(0)

        if qty < 0:
            status = "negative"
        elif qty < min_stock:
            status = "low"
        else:
            status = "ok"

        rows.append({
            "type": "product",
            "type_label": "Продукт",
            "id": product.id,
            "name": product.name,
            "sku": product.sku or "",
            "unit": product.unit_label(),
            "warehouse_id": row["warehouse_id"],
            "warehouse_name": loc_name.get(row["warehouse_id"], "—"),
            "qty": qty,
            "qty_display": _fmt_qty(qty),
            "has_min": True,
            "min_stock": min_stock,
            "min_display": _fmt_qty(min_stock),
            "price": price,
            "price_display": _fmt_money(price),
            "value": value,
            "value_display": _fmt_money(value),
            "status": status,
        })

    for row in prep_balances:
        prep = preparations.get(row["preparation_id"])
        if not prep:
            continue

        qty = row["qty"] or Decimal(0)
        # Себестоимость заготовки — из техкарты (кэш cost_per_kg).
        price = prep.cached_cost_per_kg or Decimal(0)
        value = qty * price

        # У заготовок нет минимального остатка.
        if qty < 0:
            status = "negative"
        else:
            status = "ok"

        rows.append({
            "type": "preparation",
            "type_label": "Заготовка",
            "id": prep.id,
            "name": prep.name,
            "sku": "",
            "unit": "кг",
            "warehouse_id": row["warehouse_id"],
            "warehouse_name": loc_name.get(row["warehouse_id"], "—"),
            "qty": qty,
            "qty_display": _fmt_qty(qty),
            "has_min": False,
            "min_stock": Decimal(0),
            "min_display": "—",
            "price": price,
            "price_display": _fmt_money(price),
            "value": value,
            "value_display": _fmt_money(value),
            "status": status,
        })

    # ----- KPI (по складскому срезу, до поиска/переключателей) -----
    kpi_positions = sum(1 for r in rows if r["qty"] != 0)
    kpi_value = sum((r["value"] for r in rows), Decimal(0))
    kpi_low = sum(1 for r in rows if r["status"] == "low")
    kpi_negative = sum(1 for r in rows if r["status"] == "negative")

    # ----- отображаемые фильтры -----
    if search:
        needle = search.lower()
        rows = [
            r for r in rows
            if needle in r["name"].lower() or needle in r["sku"].lower()
        ]
    if only_low:
        rows = [r for r in rows if r["status"] == "low"]
    if only_negative:
        rows = [r for r in rows if r["status"] == "negative"]

    # сортировка: по названию
    rows.sort(key=lambda r: r["name"].lower())

    # ----- пагинация -----
    paginator = Paginator(rows, per_page)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    # querystring для пагинации (без page)
    qs_parts = []
    if warehouse_id:
        qs_parts.append(f"warehouse={warehouse_id}")
    if search:
        from urllib.parse import quote
        qs_parts.append(f"search={quote(search)}")
    if only_low:
        qs_parts.append("only_low=1")
    if only_negative:
        qs_parts.append("only_negative=1")
    if per_page != 50:
        qs_parts.append(f"per_page={per_page}")
    base_qs = ("&" + "&".join(qs_parts)) if qs_parts else ""

    return render(request, "foodcost/stock_list.html", {
        "country": country,
        "show_money": show_money,

        "locations": locations,
        "warehouse_id": warehouse_id,
        "search": search,
        "only_low": only_low,
        "only_negative": only_negative,
        "per_page": per_page,

        "page_obj": page_obj,
        "total_count": paginator.count,
        "base_qs": base_qs,

        "kpi_positions": kpi_positions,
        "kpi_value_display": _fmt_money(kpi_value),
        "kpi_low": kpi_low,
        "kpi_negative": kpi_negative,
    })


# =========================================================================
# ДВИЖЕНИЯ СКЛАДА — общий журнал
# =========================================================================
@login_required(login_url="/login/")
def stock_movements(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_STOCK)
    if access_error:
        return access_error

    profile = getattr(request.user, "profile", None)
    show_money = not (profile and profile.is_kitchen_staff())

    # ----- фильтры -----
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    warehouse_id = (request.GET.get("warehouse") or "").strip()
    mtype = (request.GET.get("type") or "").strip()
    source = (request.GET.get("source") or "").strip()
    search = (request.GET.get("search") or "").strip()

    try:
        per_page = int(request.GET.get("per_page") or 50)
    except (TypeError, ValueError):
        per_page = 50
    if per_page not in (50, 100, 200):
        per_page = 50

    qs = (
        StockMovement.objects
        .filter(country=country)
        .select_related("warehouse", "product", "preparation", "created_by")
    )
    if date_from:
        qs = qs.filter(movement_datetime__date__gte=date_from)
    if date_to:
        qs = qs.filter(movement_datetime__date__lte=date_to)
    if warehouse_id:
        qs = qs.filter(warehouse_id=warehouse_id)
    if mtype:
        qs = qs.filter(movement_type=mtype)
    if source:
        qs = qs.filter(source_type=source)
    if search:
        qs = qs.filter(
            Q(product__name__icontains=search)
            | Q(product__sku__icontains=search)
            | Q(preparation__name__icontains=search)
            | Q(comment__icontains=search)
        )
    qs = qs.order_by("-movement_datetime", "-id")

    # ----- KPI (по текущему фильтру, по количеству движений) -----
    kpi_total = qs.count()
    income_types = [StockMovement.TYPE_RECEIPT, StockMovement.TYPE_TRANSFER_IN, StockMovement.TYPE_RETURN_CANCEL]
    outcome_types = [StockMovement.TYPE_SALE, StockMovement.TYPE_WRITEOFF, StockMovement.TYPE_TRANSFER_OUT]
    kpi_income = qs.filter(movement_type__in=income_types).count()
    kpi_outcome = qs.filter(movement_type__in=outcome_types).count()
    kpi_adjust = qs.filter(movement_type=StockMovement.TYPE_INVENTORY_ADJUSTMENT).count()

    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    # ссылки на документ-источник
    src_url = {
        StockMovement.SOURCE_PURCHASE_RECEIPT: "purchases",
        StockMovement.SOURCE_TRANSFER: "transfers",
        StockMovement.SOURCE_INVENTORY: "inventory",
        StockMovement.SOURCE_ORDER: "orders",
    }

    rows = []
    for m in page_obj:
        if m.item_type == StockMovement.ITEM_TYPE_PRODUCT:
            name = m.product.name if m.product else "—"
            sku = (m.product.sku if m.product else "") or ""
            unit = m.product.unit_label() if m.product else ""
        else:
            name = m.preparation.name if m.preparation else "—"
            sku = ""
            unit = "кг"
        qty = m.quantity_delta or Decimal(0)
        prefix = src_url.get(m.source_type)
        rows.append({
            "obj": m,
            "name": name,
            "sku": sku,
            "unit": unit,
            "qty_display": ("+" if qty > 0 else "") + _fmt_qty(qty),
            "qty_dir": ("in" if qty > 0 else ("out" if qty < 0 else "zero")),
            "unit_cost_display": _fmt_money(m.unit_cost),
            "total_cost_display": _fmt_money(m.total_cost),
            "doc_url": (f"/c/{country.slug}/{prefix}/{m.source_id}/" if (prefix and m.source_id) else None),
        })

    from urllib.parse import quote
    qs_parts = []
    for k, v in [("date_from", date_from), ("date_to", date_to), ("warehouse", warehouse_id),
                 ("type", mtype), ("source", source), ("search", quote(search) if search else "")]:
        if v:
            qs_parts.append(f"{k}={v}")
    if per_page != 50:
        qs_parts.append(f"per_page={per_page}")
    base_qs = ("&" + "&".join(qs_parts)) if qs_parts else ""

    return render(request, "foodcost/stock_movements.html", {
        "country": country,
        "show_money": show_money,
        "rows": rows,
        "page_obj": page_obj,
        "total_count": paginator.count,
        "base_qs": base_qs,
        "warehouses": Location.objects.filter(country=country).order_by("name"),
        "movement_types": StockMovement.MOVEMENT_TYPE_CHOICES,
        "source_types": StockMovement.SOURCE_TYPE_CHOICES,
        "date_from": date_from, "date_to": date_to, "warehouse_id": warehouse_id,
        "type": mtype, "source": source, "search": search, "per_page": per_page,
        "kpi_total": kpi_total, "kpi_income": kpi_income,
        "kpi_outcome": kpi_outcome, "kpi_adjust": kpi_adjust,
    })


# =========================================================================
# КАРТОЧКА ОСТАТКА ТОВАРА (продукт)
# =========================================================================
@login_required(login_url="/login/")
def stock_product_detail(request, country_slug, product_id):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_STOCK)
    if access_error:
        return access_error

    profile = getattr(request.user, "profile", None)
    show_money = not (profile and profile.is_kitchen_staff())

    product = get_object_or_404(Product, id=product_id, country=country)

    # последняя закупочная цена
    last_item = (
        PurchaseReceiptItem.objects
        .filter(receipt__status=PurchaseReceipt.STATUS_CONFIRMED, product=product)
        .select_related("receipt")
        .order_by("-receipt__receipt_date", "-receipt__confirmed_at", "-id")
        .first()
    )
    last_price = (last_item.unit_price or Decimal(0)) if last_item else Decimal(0)

    min_stock = product.minimum_stock or Decimal(0)

    # остатки по складам
    loc_name = {l.id: l.name for l in Location.objects.filter(country=country)}
    balances = (
        StockMovement.objects
        .filter(country=country, item_type=StockMovement.ITEM_TYPE_PRODUCT, product=product)
        .values("warehouse_id").annotate(qty=Sum("quantity_delta"))
    )
    wh_rows = []
    total_qty = Decimal(0)
    for b in balances:
        qty = b["qty"] or Decimal(0)
        total_qty += qty
        if qty < 0:
            status = "negative"
        elif qty < min_stock:
            status = "low"
        else:
            status = "ok"
        wh_rows.append({
            "warehouse": loc_name.get(b["warehouse_id"], "—"),
            "qty_display": _fmt_qty(qty),
            "status": status,
            "value_display": _fmt_money(qty * last_price),
        })
    wh_rows.sort(key=lambda r: r["warehouse"].lower())
    total_value = total_qty * last_price

    # история цен
    price_history = [
        {"date": p.date_from, "price_display": _fmt_money(p.price)}
        for p in ProductPrice.objects.filter(product=product).order_by("-date_from")[:20]
    ]

    # последние движения
    movements = []
    for m in (
        StockMovement.objects
        .filter(country=country, item_type=StockMovement.ITEM_TYPE_PRODUCT, product=product)
        .select_related("warehouse", "created_by")
        .order_by("-movement_datetime", "-id")[:100]
    ):
        qty = m.quantity_delta or Decimal(0)
        movements.append({
            "obj": m,
            "warehouse": m.warehouse.name if m.warehouse else "—",
            "qty_display": ("+" if qty > 0 else "") + _fmt_qty(qty),
            "qty_dir": ("in" if qty > 0 else ("out" if qty < 0 else "zero")),
            "total_cost_display": _fmt_money(m.total_cost),
        })

    return render(request, "foodcost/stock_product_detail.html", {
        "country": country,
        "show_money": show_money,
        "product": product,
        "unit": product.unit_label(),
        "sku": product.sku or "",
        "min_display": _fmt_qty(min_stock),
        "last_price_display": _fmt_money(last_price),
        "total_qty_display": _fmt_qty(total_qty),
        "total_value_display": _fmt_money(total_value),
        "wh_rows": wh_rows,
        "price_history": price_history,
        "movements": movements,
    })
