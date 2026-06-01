"""
Складской модуль — Перемещения между складами.

Список + карточка: создание/редактирование черновика, добавление позиций
(товары И заготовки — двигаются как единицы, техкарта не раскрывается),
подтверждение, копирование, удаление черновика.

Подтверждение (в транзакции) на каждую строку создаёт ДВА движения:
  • transfer_out (склад-отправитель, −qty)
  • transfer_in  (склад-получатель, +qty)

Контроль остатка: показываем доступный остаток на складе-отправителя; если
количество больше — предупреждаем, но НЕ блокируем (минус разрешён).

Нельзя подтвердить, если: нет позиций / не выбран отправитель / не выбран
получатель / склады совпадают.

Примечание: раздельные права по локациям (отправитель/получатель) —
архитектурно заложены, но включатся позже (когда добавим UserProfile.locations).
Сейчас подтверждать может любой с доступом к разделу.

Деньги (стоимость доставки) скрыты для роли кухни.
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
    Location,
    Product,
    Preparation,
    Transfer,
    TransferItem,
    StockMovement,
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


def _fmt_qty(value):
    try:
        value = Decimal(value or 0)
    except Exception:
        value = Decimal(0)
    s = f"{value:.3f}".rstrip("0").rstrip(".")
    return s if s else "0"


def _clean_decimal(value):
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", ".").replace(" ", ""))
    except Exception:
        return Decimal("0")


def _show_money(request):
    profile = getattr(request.user, "profile", None)
    return not (profile and profile.is_kitchen_staff())


def _next_doc_number(country, year):
    prefix = f"TR-{year}-"
    count = Transfer.objects.filter(
        country=country, document_number__startswith=prefix
    ).count()
    return f"{prefix}{count + 1:06d}"


def _current_qty(country, warehouse_id, item):
    """Текущий остаток позиции на складе (Σ quantity_delta)."""
    if not warehouse_id:
        return Decimal(0)
    qs = StockMovement.objects.filter(country=country, warehouse_id=warehouse_id)
    if item.item_type == TransferItem.ITEM_TYPE_PRODUCT:
        qs = qs.filter(item_type=StockMovement.ITEM_TYPE_PRODUCT, product_id=item.product_id)
    else:
        qs = qs.filter(item_type=StockMovement.ITEM_TYPE_PREPARATION, preparation_id=item.preparation_id)
    return qs.aggregate(s=Sum("quantity_delta"))["s"] or Decimal(0)


def _item_unit_cost(item):
    """Себестоимость единицы: продукт — последняя закупка; заготовка — техкарта."""
    if item.item_type == TransferItem.ITEM_TYPE_PRODUCT and item.product:
        price = item.product.get_price()
        return price.price if price else Decimal(0)
    if item.item_type == TransferItem.ITEM_TYPE_PREPARATION and item.preparation:
        return item.preparation.cached_cost_per_kg or Decimal(0)
    return Decimal(0)


# =========================================================================
# СПИСОК
# =========================================================================
@login_required(login_url="/login/")
def transfer_list(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_TRANSFERS)
    if access_error:
        return access_error

    can_edit = user_can_edit(request.user)

    if request.method == "POST" and request.POST.get("action") == "create_draft":
        if not can_edit:
            return redirect(f"/c/{country.slug}/transfers/")
        transfer = Transfer.objects.create(
            country=country,
            status=Transfer.STATUS_DRAFT,
            transfer_date=timezone.now().date(),
            created_by=request.user,
        )
        return redirect(f"/c/{country.slug}/transfers/{transfer.id}/")

    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    from_id = (request.GET.get("from_warehouse") or "").strip()
    to_id = (request.GET.get("to_warehouse") or "").strip()
    status = (request.GET.get("status") or "").strip()
    search = (request.GET.get("search") or "").strip()

    try:
        per_page = int(request.GET.get("per_page") or 25)
    except (TypeError, ValueError):
        per_page = 25
    if per_page not in (25, 50, 100):
        per_page = 25

    qs = (
        Transfer.objects
        .filter(country=country)
        .select_related("from_warehouse", "to_warehouse", "created_by")
        .annotate(items_count=Count("items"))
    )
    if date_from:
        qs = qs.filter(transfer_date__gte=date_from)
    if date_to:
        qs = qs.filter(transfer_date__lte=date_to)
    if from_id:
        qs = qs.filter(from_warehouse_id=from_id)
    if to_id:
        qs = qs.filter(to_warehouse_id=to_id)
    if status:
        qs = qs.filter(status=status)
    if search:
        qs = qs.filter(Q(document_number__icontains=search) | Q(comment__icontains=search))
    qs = qs.order_by("-transfer_date", "-id")

    confirmed = Transfer.STATUS_CONFIRMED
    kpi_total = Transfer.objects.filter(country=country).count()
    kpi_confirmed = Transfer.objects.filter(country=country, status=confirmed).count()
    kpi_drafts = Transfer.objects.filter(country=country, status=Transfer.STATUS_DRAFT).count()
    kpi_positions = TransferItem.objects.filter(
        transfer__country=country, transfer__status=confirmed
    ).count()

    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    from urllib.parse import quote
    qs_parts = []
    for k, v in [("date_from", date_from), ("date_to", date_to), ("from_warehouse", from_id),
                 ("to_warehouse", to_id), ("status", status), ("search", quote(search) if search else "")]:
        if v:
            qs_parts.append(f"{k}={v}")
    if per_page != 25:
        qs_parts.append(f"per_page={per_page}")
    base_qs = ("&" + "&".join(qs_parts)) if qs_parts else ""

    return render(request, "foodcost/transfer_list.html", {
        "country": country,
        "can_edit": can_edit,
        "page_obj": page_obj,
        "total_count": paginator.count,
        "base_qs": base_qs,
        "warehouses": Location.objects.filter(country=country).order_by("name"),
        "statuses": Transfer.STATUS_CHOICES,
        "date_from": date_from, "date_to": date_to, "from_id": from_id, "to_id": to_id,
        "status": status, "search": search, "per_page": per_page,
        "kpi_total": kpi_total, "kpi_confirmed": kpi_confirmed,
        "kpi_drafts": kpi_drafts, "kpi_positions": kpi_positions,
    })


# =========================================================================
# КАРТОЧКА
# =========================================================================
@login_required(login_url="/login/")
def transfer_detail(request, country_slug, transfer_id):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_TRANSFERS)
    if access_error:
        return access_error

    can_edit = user_can_edit(request.user)
    show_money = _show_money(request)

    transfer = get_object_or_404(Transfer, id=transfer_id, country=country)
    is_draft = transfer.status == Transfer.STATUS_DRAFT

    if request.method == "POST":
        action = request.POST.get("action")

        if action in ("update_header", "add_item", "remove_item", "delete", "copy") and not can_edit:
            return redirect(f"/c/{country.slug}/transfers/{transfer.id}/")

        if action == "update_header" and is_draft:
            from_id = request.POST.get("from_warehouse_id")
            to_id = request.POST.get("to_warehouse_id")
            transfer.from_warehouse = Location.objects.filter(id=from_id, country=country).first() if from_id else None
            transfer.to_warehouse = Location.objects.filter(id=to_id, country=country).first() if to_id else None
            transfer.transfer_date = request.POST.get("transfer_date") or transfer.transfer_date
            transfer.comment = (request.POST.get("comment") or "").strip()
            transfer.delivery_cost = _clean_decimal(request.POST.get("delivery_cost"))
            transfer.save()
            return redirect(f"/c/{country.slug}/transfers/{transfer.id}/")

        if action == "add_item" and is_draft:
            # Подхватываем несохранённые значения шапки (из скрытых полей формы),
            # чтобы выбор складов/даты не обнулялся при добавлении позиции.
            if "hdr_from" in request.POST:
                hf = request.POST.get("hdr_from")
                ht = request.POST.get("hdr_to")
                transfer.from_warehouse = Location.objects.filter(id=hf, country=country).first() if hf else transfer.from_warehouse
                transfer.to_warehouse = Location.objects.filter(id=ht, country=country).first() if ht else transfer.to_warehouse
                if request.POST.get("hdr_date"):
                    transfer.transfer_date = request.POST.get("hdr_date")
                transfer.comment = (request.POST.get("hdr_comment") or "").strip()
                if request.POST.get("hdr_delivery") not in (None, ""):
                    transfer.delivery_cost = _clean_decimal(request.POST.get("hdr_delivery"))
                transfer.save()

            item_type = request.POST.get("item_type")
            qty = _clean_decimal(request.POST.get("quantity"))
            comment = (request.POST.get("comment") or "").strip()
            if item_type == TransferItem.ITEM_TYPE_PRODUCT:
                product = Product.objects.filter(id=request.POST.get("product_id"), country=country).first()
                if product and qty > 0:
                    TransferItem.objects.create(
                        transfer=transfer, item_type=item_type, product=product,
                        quantity=qty, comment=comment,
                    )
            elif item_type == TransferItem.ITEM_TYPE_PREPARATION:
                prep = Preparation.objects.filter(id=request.POST.get("preparation_id"), country=country).first()
                if prep and qty > 0:
                    TransferItem.objects.create(
                        transfer=transfer, item_type=item_type, preparation=prep,
                        quantity=qty, comment=comment,
                    )
            return redirect(f"/c/{country.slug}/transfers/{transfer.id}/")

        if action == "remove_item" and is_draft:
            TransferItem.objects.filter(id=request.POST.get("item_id"), transfer=transfer).delete()
            return redirect(f"/c/{country.slug}/transfers/{transfer.id}/")

        if action == "delete" and is_draft:
            transfer.delete()
            return redirect(f"/c/{country.slug}/transfers/")

        if action == "copy":
            new_t = Transfer.objects.create(
                country=country, status=Transfer.STATUS_DRAFT,
                from_warehouse=transfer.from_warehouse, to_warehouse=transfer.to_warehouse,
                transfer_date=timezone.now().date(), comment=transfer.comment,
                created_by=request.user,
            )
            for it in transfer.items.all():
                TransferItem.objects.create(
                    transfer=new_t, item_type=it.item_type, product=it.product,
                    preparation=it.preparation, quantity=it.quantity, comment=it.comment,
                )
            return redirect(f"/c/{country.slug}/transfers/{new_t.id}/")

        if action == "confirm" and is_draft:
            error = _confirm_transfer(transfer, request.user, country)
            suffix = "confirmed=1" if not error else "err=" + error
            return redirect(f"/c/{country.slug}/transfers/{transfer.id}/?{suffix}")

        return redirect(f"/c/{country.slug}/transfers/{transfer.id}/")

    # GET
    items = list(transfer.items.select_related("product", "preparation").all())
    item_rows = []
    for it in items:
        if it.item_type == TransferItem.ITEM_TYPE_PRODUCT:
            name = it.product.name if it.product else "—"
            unit = it.product.unit_label() if it.product else ""
            type_label = "Товар"
        else:
            name = it.preparation.name if it.preparation else "—"
            unit = "кг"
            type_label = "Заготовка"
        avail = _current_qty(country, transfer.from_warehouse_id, it)
        item_rows.append({
            "obj": it, "name": name, "unit": unit, "type_label": type_label,
            "qty": it.quantity, "qty_display": _fmt_qty(it.quantity),
            "avail_display": _fmt_qty(avail),
            "short": it.quantity > avail,
            "comment": it.comment,
        })

    logs = (
        DocumentLog.objects
        .filter(country=country, document_type=DocumentLog.DOC_TRANSFER, document_id=transfer.id)
        .select_related("user").order_by("-created_at")[:50]
    )

    return render(request, "foodcost/transfer_detail.html", {
        "country": country,
        "can_edit": can_edit,
        "show_money": show_money,
        "transfer": transfer,
        "is_draft": is_draft,
        "is_confirmed": transfer.status == Transfer.STATUS_CONFIRMED,
        "is_cancelled": transfer.status == Transfer.STATUS_CANCELLED,
        "item_rows": item_rows,
        "items_count": len(items),
        "total_qty_display": _fmt_qty(sum((it.quantity for it in items), Decimal(0))),
        "delivery_cost_display": _fmt_money(transfer.delivery_cost),
        "warehouses": Location.objects.filter(country=country).order_by("name"),
        "products": Product.objects.filter(country=country).order_by("name"),
        "preparations": Preparation.objects.filter(country=country).order_by("name"),
        "logs": logs,
        "confirmed_flag": request.GET.get("confirmed") == "1",
        "err": request.GET.get("err") or "",
    })


def _confirm_transfer(transfer, user, country):
    if transfer.status != Transfer.STATUS_DRAFT:
        return "status"
    if not transfer.from_warehouse_id:
        return "from"
    if not transfer.to_warehouse_id:
        return "to"
    if transfer.from_warehouse_id == transfer.to_warehouse_id:
        return "same"

    items = list(transfer.items.select_related("product", "preparation").all())
    if not items:
        return "items"

    with transaction.atomic():
        year = (transfer.transfer_date or timezone.now().date()).year
        transfer.document_number = _next_doc_number(country, year)
        transfer.status = Transfer.STATUS_CONFIRMED
        transfer.confirmed_at = timezone.now()
        transfer.save()

        for it in items:
            unit_cost = _item_unit_cost(it)
            total_cost = (it.quantity or Decimal(0)) * unit_cost
            if it.item_type == TransferItem.ITEM_TYPE_PRODUCT:
                sm_item_type = StockMovement.ITEM_TYPE_PRODUCT
                product, preparation = it.product, None
            else:
                sm_item_type = StockMovement.ITEM_TYPE_PREPARATION
                product, preparation = None, it.preparation

            # выход со склада-отправителя
            StockMovement.objects.create(
                country=country, warehouse=transfer.from_warehouse,
                item_type=sm_item_type, product=product, preparation=preparation,
                quantity_delta=-(it.quantity or Decimal(0)),
                movement_type=StockMovement.TYPE_TRANSFER_OUT,
                source_type=StockMovement.SOURCE_TRANSFER, source_id=transfer.id,
                unit_cost=unit_cost, total_cost=-total_cost,
                comment=f"Перемещение {transfer.document_number} → {transfer.to_warehouse.name}",
                created_by=user,
            )
            # вход на склад-получатель
            StockMovement.objects.create(
                country=country, warehouse=transfer.to_warehouse,
                item_type=sm_item_type, product=product, preparation=preparation,
                quantity_delta=(it.quantity or Decimal(0)),
                movement_type=StockMovement.TYPE_TRANSFER_IN,
                source_type=StockMovement.SOURCE_TRANSFER, source_id=transfer.id,
                unit_cost=unit_cost, total_cost=total_cost,
                comment=f"Перемещение {transfer.document_number} ← {transfer.from_warehouse.name}",
                created_by=user,
            )

        DocumentLog.objects.create(
            country=country, document_type=DocumentLog.DOC_TRANSFER, document_id=transfer.id,
            user=user, action="confirmed",
            comment=f"Перемещение {transfer.document_number} подтверждено",
        )

    return ""
