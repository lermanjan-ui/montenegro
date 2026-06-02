"""
Складской модуль — Приходы (шаг A).

Список + карточка прихода: создание/редактирование черновика, добавление
товаров, подтверждение, копирование, удаление черновика, частичная оплата.

Подтверждение прихода (в транзакции):
  • присваивает номер документа PR-YYYY-NNNNNN;
  • считает сумму документа;
  • на каждую строку создаёт StockMovement (receipt, +qty) на склад прихода;
  • создаёт ProductPrice (обновляет закупочную цену продукта);
  • привязывает товар к поставщику (SupplierProduct.get_or_create);
  • пишет запись в общий журнал изменений (DocumentLog);
  • переводит документ в статус "Подтверждён" (редактирование запрещено).

Деньги (цены/суммы) скрыты для роли кухни. Редактирование/создание строк —
только can_edit (админ/суперадмин); подтверждать может любой, у кого есть
доступ к разделу (по решению: админ/повар/кассир/суперадмин).

Шаг B (отдельно): вложения, правая колонка истории закупочных цен,
разблокировка подтверждённого документа суперадмином, карточка поставщика.
"""

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Sum, Q
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from .models import (
    UserProfile,
    Supplier,
    SupplierProduct,
    Product,
    Location,
    PurchaseReceipt,
    PurchaseReceiptItem,
    StockMovement,
    ProductPrice,
    DocumentLog,
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


def _clean_decimal(value):
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", ".").replace(" ", ""))
    except Exception:
        return Decimal("0")


def _next_doc_number(country, year):
    prefix = f"PR-{year}-"
    count = PurchaseReceipt.objects.filter(
        country=country,
        document_number__startswith=prefix,
    ).count()
    return f"{prefix}{count + 1:06d}"


def _show_money(request):
    profile = getattr(request.user, "profile", None)
    return not (profile and profile.is_kitchen_staff())


def _is_superadmin(user):
    """Суперадмин = Django is_superuser ИЛИ UserProfile.role == super_admin.
    Только он может править уже ПОДТВЕРЖДЁННЫЙ приход."""
    if getattr(user, "is_superuser", False):
        return True
    profile = getattr(user, "profile", None)
    return bool(profile and profile.is_super_admin())


# =========================================================================
# СПИСОК ПРИХОДОВ
# =========================================================================
@login_required(login_url="/login/")
def purchase_list(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_PURCHASES)
    if access_error:
        return access_error

    can_edit = user_can_edit(request.user)
    show_money = _show_money(request)

    # "+ Новый приход" — создаём пустой черновик и уходим в карточку.
    if request.method == "POST" and request.POST.get("action") == "create_draft":
        if not can_edit:
            return redirect(f"/c/{country.slug}/purchases/")
        receipt = PurchaseReceipt.objects.create(
            country=country,
            status=PurchaseReceipt.STATUS_DRAFT,
            receipt_date=timezone.now().date(),
            created_by=request.user,
        )
        return redirect(f"/c/{country.slug}/purchases/{receipt.id}/")

    # ----- фильтры -----
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    warehouse_id = (request.GET.get("warehouse") or "").strip()
    supplier_id = (request.GET.get("supplier") or "").strip()
    status = (request.GET.get("status") or "").strip()
    paid = (request.GET.get("paid") or "").strip()
    search = (request.GET.get("search") or "").strip()

    try:
        per_page = int(request.GET.get("per_page") or 25)
    except (TypeError, ValueError):
        per_page = 25
    if per_page not in (25, 50, 100):
        per_page = 25

    qs = (
        PurchaseReceipt.objects
        .filter(country=country)
        .select_related("supplier", "warehouse", "created_by")
        .annotate(items_count=Count("items"))
    )

    if date_from:
        qs = qs.filter(receipt_date__gte=date_from)
    if date_to:
        qs = qs.filter(receipt_date__lte=date_to)
    if warehouse_id:
        qs = qs.filter(warehouse_id=warehouse_id)
    if supplier_id:
        qs = qs.filter(supplier_id=supplier_id)
    if status:
        qs = qs.filter(status=status)
    if paid == "yes":
        qs = qs.filter(is_paid=True)
    elif paid == "no":
        qs = qs.filter(is_paid=False)
    if search:
        qs = qs.filter(
            Q(document_number__icontains=search)
            | Q(supplier__name__icontains=search)
            | Q(comment__icontains=search)
        )

    qs = qs.order_by("-receipt_date", "-id")

    # ----- KPI (по стране, не зависят от фильтров) -----
    confirmed = PurchaseReceipt.STATUS_CONFIRMED
    kpi_total = PurchaseReceipt.objects.filter(country=country).count()
    kpi_sum = (
        PurchaseReceipt.objects
        .filter(country=country, status=confirmed)
        .aggregate(s=Sum("total_amount"))["s"] or Decimal(0)
    )
    unpaid = (
        PurchaseReceipt.objects
        .filter(country=country, status=confirmed, is_paid=False)
        .aggregate(c=Count("id"), s=Sum("total_amount"))
    )
    kpi_unpaid_count = unpaid["c"] or 0
    kpi_unpaid_sum = unpaid["s"] or Decimal(0)
    kpi_suppliers = (
        PurchaseReceipt.objects
        .filter(country=country, status=confirmed, supplier__isnull=False)
        .values("supplier_id").distinct().count()
    )

    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    rows = []
    for r in page_obj:
        rows.append({
            "obj": r,
            "total_display": _fmt_money(r.total_amount),
        })

    qs_parts = []
    from urllib.parse import quote
    for key, val in [
        ("date_from", date_from), ("date_to", date_to), ("warehouse", warehouse_id),
        ("supplier", supplier_id), ("status", status), ("paid", paid),
        ("search", quote(search) if search else ""),
    ]:
        if val:
            qs_parts.append(f"{key}={val}")
    if per_page != 25:
        qs_parts.append(f"per_page={per_page}")
    base_qs = ("&" + "&".join(qs_parts)) if qs_parts else ""

    return render(request, "foodcost/purchase_list.html", {
        "country": country,
        "can_edit": can_edit,
        "show_money": show_money,

        "rows": rows,
        "page_obj": page_obj,
        "total_count": paginator.count,
        "base_qs": base_qs,

        "warehouses": Location.objects.filter(country=country).order_by("name"),
        "suppliers": Supplier.objects.filter(country=country).order_by("name"),
        "statuses": PurchaseReceipt.STATUS_CHOICES,

        "date_from": date_from, "date_to": date_to,
        "warehouse_id": warehouse_id, "supplier_id": supplier_id,
        "status": status, "paid": paid, "search": search, "per_page": per_page,

        "kpi_total": kpi_total,
        "kpi_sum_display": _fmt_money(kpi_sum),
        "kpi_unpaid_count": kpi_unpaid_count,
        "kpi_unpaid_sum_display": _fmt_money(kpi_unpaid_sum),
        "kpi_suppliers": kpi_suppliers,
    })


# =========================================================================
# КАРТОЧКА ПРИХОДА
# =========================================================================
@login_required(login_url="/login/")
def purchase_detail(request, country_slug, receipt_id):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_PURCHASES)
    if access_error:
        return access_error

    can_edit = user_can_edit(request.user)
    show_money = _show_money(request)

    receipt = get_object_or_404(PurchaseReceipt, id=receipt_id, country=country)
    is_draft = receipt.status == PurchaseReceipt.STATUS_DRAFT
    is_confirmed = receipt.status == PurchaseReceipt.STATUS_CONFIRMED

    # Суперадмин может править ПОДТВЕРЖДЁННЫЙ приход «на месте»: статус
    # остаётся confirmed, но любое изменение строк запускает атомарный
    # пересбор движений склада и истории цен (см. _rebuild_receipt_stock).
    is_super = _is_superadmin(request.user)
    can_edit_confirmed = is_super and is_confirmed
    # Можно ли вообще менять строки в этом запросе: черновик (как раньше)
    # ИЛИ суперадмин по подтверждённому.
    items_editable = is_draft or can_edit_confirmed

    if request.method == "POST":
        action = request.POST.get("action")

        # --- действия редактирования строк ---
        # В черновике — обычное право can_edit. В подтверждённом приходе
        # эти же действия разрешены ТОЛЬКО суперадмину (правка на месте).
        editing_actions = ("update_header", "add_item", "edit_item", "remove_item",
                           "delete", "copy", "create_supplier_inline")
        if action in editing_actions:
            if is_draft and not can_edit:
                return redirect(f"/c/{country.slug}/purchases/{receipt.id}/")
            if is_confirmed and not can_edit_confirmed:
                return redirect(f"/c/{country.slug}/purchases/{receipt.id}/")
            if receipt.status == PurchaseReceipt.STATUS_CANCELLED:
                return redirect(f"/c/{country.slug}/purchases/{receipt.id}/")

        if action == "update_header":
            if is_draft:
                supplier_id = request.POST.get("supplier_id")
                warehouse_id = request.POST.get("warehouse_id")
                receipt.supplier = (
                    Supplier.objects.filter(id=supplier_id, country=country).first()
                    if supplier_id else None
                )
                receipt.warehouse = (
                    Location.objects.filter(id=warehouse_id, country=country).first()
                    if warehouse_id else None
                )
                receipt.receipt_date = request.POST.get("receipt_date") or receipt.receipt_date
                receipt.comment = (request.POST.get("comment") or "").strip()
            # оплата редактируется в любом статусе (кроме отменённого)
            if receipt.status != PurchaseReceipt.STATUS_CANCELLED:
                receipt.is_paid = bool(request.POST.get("is_paid"))
                receipt.paid_at = request.POST.get("paid_at") or None
                paid_amount = _clean_decimal(request.POST.get("paid_amount"))
                if receipt.is_paid and paid_amount == 0:
                    paid_amount = receipt.total_amount
                receipt.paid_amount = paid_amount
            receipt.save()
            return redirect(f"/c/{country.slug}/purchases/{receipt.id}/")

        if action == "create_supplier_inline" and is_draft:
            name = (request.POST.get("supplier_name") or "").strip()
            if name:
                supplier = Supplier.objects.create(country=country, name=name, is_active=True)
                receipt.supplier = supplier
                receipt.save(update_fields=["supplier", "updated_at"])
            return redirect(f"/c/{country.slug}/purchases/{receipt.id}/")

        if action == "add_item" and items_editable:
            # Подхватываем несохранённые значения шапки (скрытые поля формы),
            # чтобы выбор поставщика/склада/даты не обнулялся при добавлении.
            # Для подтверждённого прихода шапку не трогаем (склад уже привязан).
            if is_draft and "hdr_supplier" in request.POST:
                hs = request.POST.get("hdr_supplier")
                hw = request.POST.get("hdr_warehouse")
                receipt.supplier = (
                    Supplier.objects.filter(id=hs, country=country).first() if hs else None
                )
                receipt.warehouse = (
                    Location.objects.filter(id=hw, country=country).first() if hw else None
                )
                if request.POST.get("hdr_date"):
                    receipt.receipt_date = request.POST.get("hdr_date")
                receipt.comment = (request.POST.get("hdr_comment") or "").strip()
                receipt.save()

            product = Product.objects.filter(
                id=request.POST.get("product_id"), country=country
            ).first()
            qty = _clean_decimal(request.POST.get("quantity"))
            # Кассир вводит ФАКТ прихода (quantity) и ОПЛАЧЕННУЮ СУММУ за всю
            # партию (line_total). Цену за единицу (unit_price) вычисляем сами:
            # unit_price = сумма / количество. unit_price хранится как цена за
            # кг и дальше уходит в склад/себестоимость — её смысл не меняем.
            # Поддерживаем старое поле unit_price как запасной вариант (если
            # форма прислала уже цену за единицу).
            line_total = _clean_decimal(request.POST.get("line_total"))
            if line_total <= 0:
                # запасной путь: пришла цена за единицу (старое поле)
                price = _clean_decimal(request.POST.get("unit_price"))
            elif qty > 0:
                price = line_total / qty
            else:
                price = Decimal("0")
            if product and qty > 0:
                PurchaseReceiptItem.objects.create(
                    receipt=receipt, product=product, quantity=qty, unit_price=price,
                )
                _recalc_total(receipt)
                if is_confirmed:
                    _rebuild_receipt_stock(receipt, request.user, country,
                                           reason="добавлена позиция")
            return redirect(f"/c/{country.slug}/purchases/{receipt.id}/")

        if action == "remove_item" and items_editable:
            PurchaseReceiptItem.objects.filter(
                id=request.POST.get("item_id"), receipt=receipt
            ).delete()
            _recalc_total(receipt)
            if is_confirmed:
                _rebuild_receipt_stock(receipt, request.user, country,
                                       reason="удалена позиция")
            return redirect(f"/c/{country.slug}/purchases/{receipt.id}/")

        if action == "edit_item" and items_editable:
            # Правка существующей строки: новое количество и/или новая
            # оплаченная сумма за партию. unit_price пересчитываем сами.
            it = PurchaseReceiptItem.objects.filter(
                id=request.POST.get("item_id"), receipt=receipt
            ).first()
            if it:
                qty = _clean_decimal(request.POST.get("quantity"))
                line_total = _clean_decimal(request.POST.get("line_total"))
                if qty > 0:
                    it.quantity = qty
                    if line_total > 0:
                        it.unit_price = line_total / qty
                    # если сумму не ввели — оставляем прежнюю unit_price,
                    # total пересчитается в save() как qty × unit_price
                    it.save()
                    _recalc_total(receipt)
                    if is_confirmed:
                        _rebuild_receipt_stock(receipt, request.user, country,
                                               reason="изменена позиция")
            return redirect(f"/c/{country.slug}/purchases/{receipt.id}/")

        if action == "delete" and is_draft:
            receipt.delete()
            return redirect(f"/c/{country.slug}/purchases/")

        if action == "copy":
            new_receipt = PurchaseReceipt.objects.create(
                country=country,
                status=PurchaseReceipt.STATUS_DRAFT,
                supplier=receipt.supplier,
                warehouse=receipt.warehouse,
                receipt_date=timezone.now().date(),
                comment=receipt.comment,
                created_by=request.user,
            )
            for it in receipt.items.all():
                PurchaseReceiptItem.objects.create(
                    receipt=new_receipt, product=it.product,
                    quantity=it.quantity, unit_price=it.unit_price,
                )
            _recalc_total(new_receipt)
            return redirect(f"/c/{country.slug}/purchases/{new_receipt.id}/")

        # --- подтверждение: доступно любому с доступом к разделу ---
        if action == "confirm" and is_draft:
            error = _confirm_receipt(receipt, request.user, country)
            return redirect(f"/c/{country.slug}/purchases/{receipt.id}/?{'confirmed=1' if not error else 'err=' + error}")

        return redirect(f"/c/{country.slug}/purchases/{receipt.id}/")

    # ----- GET -----
    items = list(receipt.items.select_related("product").all())
    item_rows = [{
        "obj": it,
        "name": it.product.name if it.product else "—",
        "sku": (it.product.sku if it.product else "") or "",
        "unit": it.product.unit_label() if it.product else "",
        "qty_display": _fmt_qty(it.quantity),
        "price_display": _fmt_money(it.unit_price),
        "total_display": _fmt_money(it.total),
        # сырые значения для формы правки (без форматирования пробелами)
        "qty_raw": _fmt_qty(it.quantity),
        "total_raw": str(int(it.total)) if it.total == it.total.to_integral_value() else str(it.total),
    } for it in items]

    logs = (
        DocumentLog.objects
        .filter(country=country, document_type=DocumentLog.DOC_PURCHASE_RECEIPT, document_id=receipt.id)
        .select_related("user")
        .order_by("-created_at")[:50]
    )

    return render(request, "foodcost/purchase_detail.html", {
        "country": country,
        "can_edit": can_edit,
        "show_money": show_money,

        "receipt": receipt,
        "is_draft": is_draft,
        "is_confirmed": receipt.status == PurchaseReceipt.STATUS_CONFIRMED,
        "is_cancelled": receipt.status == PurchaseReceipt.STATUS_CANCELLED,
        "is_super": is_super,
        "can_edit_confirmed": can_edit_confirmed,
        "items_editable": items_editable,

        "item_rows": item_rows,
        "items_count": len(items),
        "total_display": _fmt_money(receipt.total_amount),
        "paid_display": _fmt_money(receipt.paid_amount),
        "debt_display": _fmt_money(receipt.debt()),

        "suppliers": Supplier.objects.filter(country=country, is_active=True).order_by("name"),
        "warehouses": Location.objects.filter(country=country).order_by("name"),
        "products": Product.objects.filter(country=country).order_by("name"),

        "logs": logs,

        "confirmed_flag": request.GET.get("confirmed") == "1",
        "err": request.GET.get("err") or "",
    })


def _fmt_qty(value):
    try:
        value = Decimal(value or 0)
    except Exception:
        value = Decimal(0)
    s = f"{value:.3f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _recalc_total(receipt):
    total = receipt.items.aggregate(s=Sum("total"))["s"] or Decimal(0)
    receipt.total_amount = total
    receipt.save(update_fields=["total_amount", "updated_at"])


def _confirm_receipt(receipt, user, country):
    """Подтверждение прихода. Возвращает код ошибки или пустую строку."""
    if receipt.status != PurchaseReceipt.STATUS_DRAFT:
        return "status"
    if not receipt.supplier_id:
        return "supplier"
    if not receipt.warehouse_id:
        return "warehouse"
    if not receipt.receipt_date:
        return "date"

    items = list(receipt.items.select_related("product").all())
    if not items:
        return "items"

    with transaction.atomic():
        year = receipt.receipt_date.year
        receipt.document_number = _next_doc_number(country, year)
        receipt.total_amount = sum((it.total for it in items), Decimal(0))
        receipt.status = PurchaseReceipt.STATUS_CONFIRMED
        receipt.confirmed_at = timezone.now()
        if receipt.is_paid and (receipt.paid_amount or 0) == 0:
            receipt.paid_amount = receipt.total_amount
        receipt.save()

        for it in items:
            StockMovement.objects.create(
                country=country,
                warehouse=receipt.warehouse,
                item_type=StockMovement.ITEM_TYPE_PRODUCT,
                product=it.product,
                quantity_delta=it.quantity,
                movement_type=StockMovement.TYPE_RECEIPT,
                source_type=StockMovement.SOURCE_PURCHASE_RECEIPT,
                source_id=receipt.id,
                unit_cost=it.unit_price,
                total_cost=it.total,
                comment=f"Приход {receipt.document_number}",
                created_by=user,
            )
            # Обновляем закупочную цену продукта.
            ProductPrice.objects.create(
                product=it.product,
                price=it.unit_price,
                date_from=receipt.receipt_date,
            )
            # Автопривязка товара к поставщику.
            SupplierProduct.objects.get_or_create(
                supplier=receipt.supplier,
                product=it.product,
            )

        DocumentLog.objects.create(
            country=country,
            document_type=DocumentLog.DOC_PURCHASE_RECEIPT,
            document_id=receipt.id,
            user=user,
            action="confirmed",
            comment=f"Приход {receipt.document_number} подтверждён",
        )

    return ""


def _revert_receipt_stock(receipt, country):
    """Снимает складские следы подтверждённого прихода:
      • удаляет StockMovement этого прихода (по source_type/source_id);
      • удаляет записи ProductPrice, созданные этим приходом
        (точное совпадение product + date_from + price из его строк).
    Используется как первый шаг пересбора при правке на месте.
    ВНИМАНИЕ: вызывать только внутри transaction.atomic()."""
    # 1) движения склада этого прихода
    StockMovement.objects.filter(
        country=country,
        source_type=StockMovement.SOURCE_PURCHASE_RECEIPT,
        source_id=receipt.id,
    ).delete()

    # 2) записи истории цен, которые создал этот приход.
    # Прямой связи ProductPrice→приход нет, поэтому удаляем по точному
    # совпадению (product, date_from, price) с текущими строками прихода.
    # Это снимает ровно «свои» цены и не трогает чужие записи.
    if receipt.receipt_date:
        for it in receipt.items.select_related("product").all():
            ProductPrice.objects.filter(
                product=it.product,
                date_from=receipt.receipt_date,
                price=it.unit_price,
            ).delete()


def _rebuild_receipt_stock(receipt, user, country, reason=""):
    """Пересобирает складские следы ПОДТВЕРЖДЁННОГО прихода после правки
    строк «на месте» (доступно только суперадмину).

    Делает всё атомарно:
      1) откатывает прежние движения и цены этого прихода;
      2) пересчитывает сумму документа;
      3) заново создаёт StockMovement и ProductPrice по текущим строкам;
      4) пишет запись в журнал (action="edited").

    Статус документа НЕ меняется (остаётся confirmed)."""
    if receipt.status != PurchaseReceipt.STATUS_CONFIRMED:
        return

    with transaction.atomic():
        # 1) снять прежние следы
        _revert_receipt_stock(receipt, country)

        # 2) пересчитать сумму
        items = list(receipt.items.select_related("product").all())
        receipt.total_amount = sum((it.total for it in items), Decimal(0))
        if receipt.is_paid and (receipt.paid_amount or 0) == 0:
            receipt.paid_amount = receipt.total_amount
        receipt.save(update_fields=["total_amount", "paid_amount", "updated_at"])

        # 3) пересоздать движения и цены по текущим строкам
        for it in items:
            StockMovement.objects.create(
                country=country,
                warehouse=receipt.warehouse,
                item_type=StockMovement.ITEM_TYPE_PRODUCT,
                product=it.product,
                quantity_delta=it.quantity,
                movement_type=StockMovement.TYPE_RECEIPT,
                source_type=StockMovement.SOURCE_PURCHASE_RECEIPT,
                source_id=receipt.id,
                unit_cost=it.unit_price,
                total_cost=it.total,
                comment=f"Приход {receipt.document_number} (правка)",
                created_by=user,
            )
            ProductPrice.objects.create(
                product=it.product,
                price=it.unit_price,
                date_from=receipt.receipt_date,
            )

        # 4) журнал
        DocumentLog.objects.create(
            country=country,
            document_type=DocumentLog.DOC_PURCHASE_RECEIPT,
            document_id=receipt.id,
            user=user,
            action="edited",
            comment=(
                f"Приход {receipt.document_number} отредактирован суперадмином"
                + (f": {reason}" if reason else "")
                + ". Движения склада и цены пересобраны."
            ),
        )
