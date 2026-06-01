"""
Складской модуль — справочник поставщиков.

Шаг 1: список + KPI + фильтры + создание / редактирование / архивация.
Карточка поставщика с вкладками (Товары / Закупки / История цен / Долги)
добавляется на шаге "Приходы" — большинство вкладок наполняется данными
приходов.

Удаление поставщика запрещено (на уровне БД PROTECT + политика архивации):
поставщик с историей закупок только деактивируется (is_active=False).
"""

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Sum, Max, F, Q
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from .models import (
    UserProfile,
    Supplier,
    SupplierProduct,
    Product,
    PurchaseReceipt,
    PurchaseReceiptItem,
)

from .views import (
    get_country,
    require_section_access,
    user_can_edit,
)


def _fmt_money(value):
    try:
        value = Decimal(value or 0)
    except Exception:
        value = Decimal(0)
    negative = value < 0
    value = abs(value).quantize(Decimal("1"))
    s = f"{int(value):,}".replace(",", " ")
    return f"-{s}" if negative else s


@login_required(login_url="/login/")
def supplier_list(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_SUPPLIERS,
    )
    if access_error:
        return access_error

    can_edit = user_can_edit(request.user)

    # ----- POST: создание / редактирование / архивация -----
    if request.method == "POST":
        if not can_edit:
            return redirect(f"/c/{country.slug}/suppliers/")

        action = request.POST.get("action")

        if action == "create_supplier":
            name = (request.POST.get("name") or "").strip()
            if name:
                Supplier.objects.create(
                    country=country,
                    name=name,
                    contact_person=(request.POST.get("contact_person") or "").strip(),
                    phone=(request.POST.get("phone") or "").strip(),
                    telegram=(request.POST.get("telegram") or "").strip(),
                    email=(request.POST.get("email") or "").strip(),
                    comment=(request.POST.get("comment") or "").strip(),
                    is_active=bool(request.POST.get("is_active")),
                )

        elif action == "update_supplier":
            supplier = get_object_or_404(
                Supplier,
                id=request.POST.get("supplier_id"),
                country=country,
            )
            supplier.name = (request.POST.get("name") or "").strip() or supplier.name
            supplier.contact_person = (request.POST.get("contact_person") or "").strip()
            supplier.phone = (request.POST.get("phone") or "").strip()
            supplier.telegram = (request.POST.get("telegram") or "").strip()
            supplier.email = (request.POST.get("email") or "").strip()
            supplier.comment = (request.POST.get("comment") or "").strip()
            supplier.is_active = bool(request.POST.get("is_active"))
            supplier.save()

        elif action == "archive_supplier":
            supplier = get_object_or_404(
                Supplier,
                id=request.POST.get("supplier_id"),
                country=country,
            )
            supplier.is_active = False
            supplier.save(update_fields=["is_active", "updated_at"])

        elif action == "unarchive_supplier":
            supplier = get_object_or_404(
                Supplier,
                id=request.POST.get("supplier_id"),
                country=country,
            )
            supplier.is_active = True
            supplier.save(update_fields=["is_active", "updated_at"])

        return redirect(f"/c/{country.slug}/suppliers/")

    # ----- фильтры -----
    search = (request.GET.get("search") or "").strip()
    status = (request.GET.get("status") or "").strip()      # "", active, inactive
    has_debt = (request.GET.get("has_debt") or "").strip()  # "", yes, no

    try:
        per_page = int(request.GET.get("per_page") or 25)
    except (TypeError, ValueError):
        per_page = 25
    if per_page not in (25, 50, 100):
        per_page = 25

    suppliers_qs = Supplier.objects.filter(country=country)

    if search:
        suppliers_qs = suppliers_qs.filter(
            Q(name__icontains=search)
            | Q(contact_person__icontains=search)
            | Q(phone__icontains=search)
        )

    if status == "active":
        suppliers_qs = suppliers_qs.filter(is_active=True)
    elif status == "inactive":
        suppliers_qs = suppliers_qs.filter(is_active=False)

    suppliers = list(suppliers_qs.order_by("name"))
    ids = [s.id for s in suppliers]

    confirmed = PurchaseReceipt.STATUS_CONFIRMED

    # Кол-во товаров поставщика (SupplierProduct) — один запрос.
    products_count = {}
    if ids:
        for row in (
            SupplierProduct.objects
            .filter(supplier_id__in=ids)
            .values("supplier_id")
            .annotate(c=Count("id"))
        ):
            products_count[row["supplier_id"]] = row["c"]

    # Подтверждённые приходы: кол-во, сумма, последняя дата — один запрос.
    receipts_agg = {}
    if ids:
        for row in (
            PurchaseReceipt.objects
            .filter(country=country, supplier_id__in=ids, status=confirmed)
            .values("supplier_id")
            .annotate(cnt=Count("id"), total=Sum("total_amount"), last=Max("receipt_date"))
        ):
            receipts_agg[row["supplier_id"]] = row

    # Задолженность (подтверждённые неоплаченные) — один запрос.
    debt_agg = {}
    if ids:
        for row in (
            PurchaseReceipt.objects
            .filter(country=country, supplier_id__in=ids, status=confirmed, is_paid=False)
            .values("supplier_id")
            .annotate(debt=Sum(F("total_amount") - F("paid_amount")), cnt=Count("id"))
        ):
            debt_agg[row["supplier_id"]] = row

    rows = []
    for s in suppliers:
        ragg = receipts_agg.get(s.id, {})
        dagg = debt_agg.get(s.id, {})
        debt = dagg.get("debt") or Decimal(0)

        rows.append({
            "id": s.id,
            "name": s.name,
            "contact_person": s.contact_person,
            "phone": s.phone,
            "telegram": s.telegram,
            "email": s.email,
            "comment": s.comment,
            "is_active": s.is_active,
            "products_count": products_count.get(s.id, 0),
            "purchases_count": ragg.get("cnt", 0),
            "last_purchase": ragg.get("last"),
            "debt": debt,
            "debt_display": _fmt_money(debt),
        })

    # фильтр по задолженности (после расчёта)
    if has_debt == "yes":
        rows = [r for r in rows if r["debt"] > 0]
    elif has_debt == "no":
        rows = [r for r in rows if r["debt"] <= 0]

    # ----- KPI (по всей стране, не зависят от фильтров) -----
    kpi_total = Supplier.objects.filter(country=country).count()
    kpi_active = Supplier.objects.filter(country=country, is_active=True).count()

    unpaid = (
        PurchaseReceipt.objects
        .filter(country=country, status=confirmed, is_paid=False)
        .aggregate(cnt=Count("id"), debt=Sum(F("total_amount") - F("paid_amount")))
    )
    kpi_unpaid_count = unpaid["cnt"] or 0
    kpi_debt = unpaid["debt"] or Decimal(0)

    # ----- пагинация -----
    paginator = Paginator(rows, per_page)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    qs_parts = []
    if search:
        from urllib.parse import quote
        qs_parts.append(f"search={quote(search)}")
    if status:
        qs_parts.append(f"status={status}")
    if has_debt:
        qs_parts.append(f"has_debt={has_debt}")
    if per_page != 25:
        qs_parts.append(f"per_page={per_page}")
    base_qs = ("&" + "&".join(qs_parts)) if qs_parts else ""

    return render(request, "foodcost/supplier_list.html", {
        "country": country,
        "can_edit": can_edit,

        "search": search,
        "status": status,
        "has_debt": has_debt,
        "per_page": per_page,

        "page_obj": page_obj,
        "total_count": paginator.count,
        "base_qs": base_qs,

        "kpi_total": kpi_total,
        "kpi_active": kpi_active,
        "kpi_unpaid_count": kpi_unpaid_count,
        "kpi_debt_display": _fmt_money(kpi_debt),
    })


def _fmt_qty(value):
    try:
        value = Decimal(value or 0)
    except Exception:
        value = Decimal(0)
    s = f"{value:.3f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _pct(old, new):
    """Изменение в % от старой цены. None, если старой нет или она 0."""
    if not old or old == 0:
        return None
    return (new - old) / old * Decimal(100)


@login_required(login_url="/login/")
def supplier_detail(request, country_slug, supplier_id):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_SUPPLIERS)
    if access_error:
        return access_error

    can_edit = user_can_edit(request.user)
    profile = getattr(request.user, "profile", None)
    show_money = not (profile and profile.is_kitchen_staff())

    supplier = get_object_or_404(Supplier, id=supplier_id, country=country)
    confirmed = PurchaseReceipt.STATUS_CONFIRMED

    # ----- POST -----
    if request.method == "POST":
        if not can_edit:
            return redirect(f"/c/{country.slug}/suppliers/{supplier.id}/")

        action = request.POST.get("action")

        if action == "add_product":
            product = Product.objects.filter(
                id=request.POST.get("product_id"), country=country
            ).first()
            if product:
                SupplierProduct.objects.get_or_create(supplier=supplier, product=product)
            return redirect(f"/c/{country.slug}/suppliers/{supplier.id}/?tab=products")

        if action == "remove_product":
            SupplierProduct.objects.filter(
                id=request.POST.get("supplier_product_id"), supplier=supplier
            ).delete()
            return redirect(f"/c/{country.slug}/suppliers/{supplier.id}/?tab=products")

        if action == "create_receipt_for_supplier":
            receipt = PurchaseReceipt.objects.create(
                country=country,
                status=PurchaseReceipt.STATUS_DRAFT,
                supplier=supplier,
                receipt_date=timezone.now().date(),
                created_by=request.user,
            )
            return redirect(f"/c/{country.slug}/purchases/{receipt.id}/")

        return redirect(f"/c/{country.slug}/suppliers/{supplier.id}/")

    # ----- KPI -----
    agg = (
        PurchaseReceipt.objects
        .filter(country=country, supplier=supplier, status=confirmed)
        .aggregate(total=Sum("total_amount"), cnt=Count("id"), last=Max("receipt_date"))
    )
    debt = (
        PurchaseReceipt.objects
        .filter(country=country, supplier=supplier, status=confirmed, is_paid=False)
        .aggregate(d=Sum(F("total_amount") - F("paid_amount")))["d"] or Decimal(0)
    )

    # ----- подтверждённые позиции этого поставщика (для Товаров и Истории цен) -----
    conf_items = list(
        PurchaseReceiptItem.objects
        .filter(receipt__country=country, receipt__supplier=supplier, receipt__status=confirmed)
        .select_related("product", "receipt")
    )

    # Товары: последняя цена/дата/кол-во закупок по каждому продукту.
    by_product = {}
    for it in conf_items:
        pid = it.product_id
        d = it.receipt.receipt_date
        rec = by_product.setdefault(pid, {"last_price": None, "last_date": None, "count": 0})
        rec["count"] += 1
        if rec["last_date"] is None or (d and d >= rec["last_date"]):
            rec["last_date"] = d
            rec["last_price"] = it.unit_price

    product_rows = []
    for sp in SupplierProduct.objects.filter(supplier=supplier).select_related("product").order_by("product__name"):
        info = by_product.get(sp.product_id, {})
        product_rows.append({
            "sp_id": sp.id,
            "name": sp.product.name,
            "sku": sp.product.sku or "",
            "unit": sp.product.unit_label(),
            "last_price_display": _fmt_money(info.get("last_price")) if info.get("last_price") is not None else "—",
            "last_date": info.get("last_date"),
            "count": info.get("count", 0),
        })

    # История цен: по каждому продукту по возрастанию даты, считаем ± к прошлой.
    items_asc = sorted(
        conf_items,
        key=lambda i: (i.product_id, i.receipt.receipt_date or timezone.now().date(), i.id),
    )
    prev_price = {}
    price_history = []
    for it in items_asc:
        old = prev_price.get(it.product_id)
        change = (it.unit_price - old) if old is not None else None
        pct = _pct(old, it.unit_price) if old is not None else None
        price_history.append({
            "name": it.product.name,
            "date": it.receipt.receipt_date,
            "price_display": _fmt_money(it.unit_price),
            "prev_display": _fmt_money(old) if old is not None else "—",
            "change_display": (("+" if change >= 0 else "") + _fmt_money(change)) if change is not None else "—",
            "pct": (float(pct) if pct is not None else None),
            "pct_display": ((("+" if pct >= 0 else "") + f"{pct:.1f}%") if pct is not None else "—"),
            "doc": it.receipt.document_number or f"#{it.receipt_id}",
            "dir": ("up" if change and change > 0 else ("down" if change and change < 0 else "flat")),
        })
        prev_price[it.product_id] = it.unit_price
    price_history.sort(key=lambda r: (r["date"] or timezone.now().date()), reverse=True)

    # Закупки поставщика.
    receipts = list(
        PurchaseReceipt.objects
        .filter(country=country, supplier=supplier)
        .annotate(items_count=Count("items"))
        .order_by("-receipt_date", "-id")
    )

    # Долги (подтверждённые неоплаченные).
    debt_rows = list(
        PurchaseReceipt.objects
        .filter(country=country, supplier=supplier, status=confirmed, is_paid=False)
        .annotate(items_count=Count("items"))
        .order_by("-receipt_date", "-id")
    )

    return render(request, "foodcost/supplier_detail.html", {
        "country": country,
        "can_edit": can_edit,
        "show_money": show_money,
        "supplier": supplier,
        "active_tab": request.GET.get("tab") or "products",

        "kpi_total_display": _fmt_money(agg["total"] or 0),
        "kpi_count": agg["cnt"] or 0,
        "kpi_debt_display": _fmt_money(debt),
        "kpi_last": agg["last"],

        "product_rows": product_rows,
        "price_history": price_history,
        "receipts": receipts,
        "debt_rows": debt_rows,
        "debt_total_display": _fmt_money(debt),

        "all_products": Product.objects.filter(country=country).order_by("name"),
    })
