"""
Складской модуль — Инвентаризация.

Шаг 1: журнал (список + KPI + фильтры) + создание документа со снимком
остатков (system_qty по продуктам и заготовкам на складе на момент создания)
+ удаление черновика.

Шаг 2: форма повара — «слепой» подсчёт: вводятся ТОЛЬКО фактические остатки,
без показа системного количества, расхождений и денег (чтобы не подсказывать).
«Завершить» переводит документ в статус «Ожидает проверки».

Шаг 3 (отдельно): аналитика менеджера — система vs факт, рекурсивное
«ушло в заготовки», расхождения, стоимость, подтверждение (inventory_adjustment),
отклонение, пересчёт.

Статусы: draft → in_progress → awaiting → confirmed / rejected.
Создавать и заполнять может любой с доступом к разделу; подтверждать/отклонять
(шаг 3) — только менеджер (can_edit).
"""

from decimal import Decimal
from collections import defaultdict

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
    Inventory,
    InventoryItem,
    StockMovement,
    PurchaseReceipt,
    PurchaseReceiptItem,
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
        return None
    try:
        return Decimal(str(value).replace(",", ".").replace(" ", ""))
    except Exception:
        return None


def _parse_date(value):
    """'2026-06-01' (строка из формы) -> date. None при пустом/неверном."""
    if not value:
        return None
    if hasattr(value, "year"):  # уже date/datetime
        return value
    from datetime import datetime
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(str(value), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _safe_int(value):
    """Строка из формы -> int, либо None для пустого/нечислового
    (защита от ValueError на filter(id='')) ."""
    try:
        s = str(value).strip()
        if not s:
            return None
        return int(s)
    except (TypeError, ValueError):
        return None


def _current_qty(country, warehouse_id, item_type, product_id=None, preparation_id=None):
    """Текущий остаток позиции на складе (Σ quantity_delta)."""
    if not warehouse_id:
        return Decimal(0)
    qs = StockMovement.objects.filter(country=country, warehouse_id=warehouse_id)
    if item_type == InventoryItem.ITEM_TYPE_PRODUCT:
        qs = qs.filter(item_type=StockMovement.ITEM_TYPE_PRODUCT, product_id=product_id)
    else:
        qs = qs.filter(item_type=StockMovement.ITEM_TYPE_PREPARATION, preparation_id=preparation_id)
    return qs.aggregate(s=Sum("quantity_delta"))["s"] or Decimal(0)


def _product_last_price(country, product_id):
    it = (
        PurchaseReceiptItem.objects
        .filter(receipt__status=PurchaseReceipt.STATUS_CONFIRMED, product_id=product_id)
        .select_related("receipt")
        .order_by("-receipt__receipt_date", "-receipt__confirmed_at", "-id")
        .first()
    )
    return (it.unit_price or Decimal(0)) if it else Decimal(0)


def _next_doc_number(country, year):
    prefix = f"INV-{year}-"
    count = Inventory.objects.filter(
        country=country, document_number__startswith=prefix
    ).count()
    return f"{prefix}{count + 1:06d}"


def _snapshot_items(inventory, country):
    """Снимок остатков склада на момент создания: создаёт InventoryItem
    по всем продуктам и заготовкам, у которых есть движения на складе."""
    wid = inventory.warehouse_id

    prod_balances = (
        StockMovement.objects
        .filter(country=country, warehouse_id=wid,
                item_type=StockMovement.ITEM_TYPE_PRODUCT, product__isnull=False)
        .values("product_id").annotate(qty=Sum("quantity_delta"))
    )
    prep_balances = (
        StockMovement.objects
        .filter(country=country, warehouse_id=wid,
                item_type=StockMovement.ITEM_TYPE_PREPARATION, preparation__isnull=False)
        .values("preparation_id").annotate(qty=Sum("quantity_delta"))
    )

    product_ids = {r["product_id"] for r in prod_balances}
    prep_ids = {r["preparation_id"] for r in prep_balances}

    # Последняя закупочная цена по продуктам — один запрос.
    last_price = {}
    if product_ids:
        for it in (
            PurchaseReceiptItem.objects
            .filter(receipt__status=PurchaseReceipt.STATUS_CONFIRMED, product_id__in=product_ids)
            .select_related("receipt")
            .order_by("product_id", "-receipt__receipt_date", "-receipt__confirmed_at", "-id")
        ):
            if it.product_id not in last_price:
                last_price[it.product_id] = it.unit_price or Decimal(0)

    preps = {p.id: p for p in Preparation.objects.filter(id__in=prep_ids)}

    bulk = []
    for r in prod_balances:
        bulk.append(InventoryItem(
            inventory=inventory,
            item_type=InventoryItem.ITEM_TYPE_PRODUCT,
            product_id=r["product_id"],
            system_qty=r["qty"] or Decimal(0),
            fact_qty=None,
            unit_cost=last_price.get(r["product_id"], Decimal(0)),
        ))
    for r in prep_balances:
        prep = preps.get(r["preparation_id"])
        bulk.append(InventoryItem(
            inventory=inventory,
            item_type=InventoryItem.ITEM_TYPE_PREPARATION,
            preparation_id=r["preparation_id"],
            system_qty=r["qty"] or Decimal(0),
            fact_qty=None,
            unit_cost=(prep.cached_cost_per_kg if prep else Decimal(0)) or Decimal(0),
        ))
    InventoryItem.objects.bulk_create(bulk)


# =========================================================================
# ЖУРНАЛ
# =========================================================================
@login_required(login_url="/login/")
def inventory_list(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_INVENTORY)
    if access_error:
        return access_error

    can_edit = user_can_edit(request.user)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create":
            warehouse = Location.objects.filter(
                id=request.POST.get("warehouse_id"), country=country
            ).first()
            if warehouse:
                inv_date = _parse_date(request.POST.get("inventory_date")) or timezone.now().date()
                with transaction.atomic():
                    inv = Inventory.objects.create(
                        country=country,
                        warehouse=warehouse,
                        inventory_date=inv_date,
                        status=Inventory.STATUS_DRAFT,
                        comment=(request.POST.get("comment") or "").strip(),
                        created_by=request.user,
                    )
                    inv.document_number = _next_doc_number(country, inv_date.year)
                    inv.save(update_fields=["document_number"])
                    _snapshot_items(inv, country)
                    DocumentLog.objects.create(
                        country=country, document_type=DocumentLog.DOC_INVENTORY,
                        document_id=inv.id, user=request.user, action="created",
                        comment=f"Инвентаризация {inv.document_number} создана",
                    )
                return redirect(f"/c/{country.slug}/inventory/{inv.id}/count/")
            return redirect(f"/c/{country.slug}/inventory/")

        if action == "delete":
            inv = get_object_or_404(Inventory, id=request.POST.get("inventory_id"), country=country)
            if inv.status in (Inventory.STATUS_DRAFT, Inventory.STATUS_IN_PROGRESS):
                inv.delete()
            return redirect(f"/c/{country.slug}/inventory/")

        return redirect(f"/c/{country.slug}/inventory/")

    # ----- фильтры -----
    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    warehouse_id = (request.GET.get("warehouse") or "").strip()
    status = (request.GET.get("status") or "").strip()
    search = (request.GET.get("search") or "").strip()

    try:
        per_page = int(request.GET.get("per_page") or 25)
    except (TypeError, ValueError):
        per_page = 25
    if per_page not in (25, 50, 100):
        per_page = 25

    qs = (
        Inventory.objects
        .filter(country=country)
        .select_related("warehouse", "created_by")
        .annotate(items_count=Count("items"))
    )
    if date_from:
        qs = qs.filter(inventory_date__gte=date_from)
    if date_to:
        qs = qs.filter(inventory_date__lte=date_to)
    if warehouse_id:
        qs = qs.filter(warehouse_id=warehouse_id)
    if status:
        qs = qs.filter(status=status)
    if search:
        qs = qs.filter(Q(document_number__icontains=search) | Q(comment__icontains=search))
    qs = qs.order_by("-inventory_date", "-id")

    kpi_total = Inventory.objects.filter(country=country).count()
    kpi_in_work = Inventory.objects.filter(
        country=country, status__in=[Inventory.STATUS_DRAFT, Inventory.STATUS_IN_PROGRESS]
    ).count()
    kpi_awaiting = Inventory.objects.filter(country=country, status=Inventory.STATUS_AWAITING).count()
    kpi_confirmed = Inventory.objects.filter(country=country, status=Inventory.STATUS_CONFIRMED).count()

    paginator = Paginator(qs, per_page)
    page_obj = paginator.get_page(request.GET.get("page") or 1)

    from urllib.parse import quote
    qs_parts = []
    for k, v in [("date_from", date_from), ("date_to", date_to), ("warehouse", warehouse_id),
                 ("status", status), ("search", quote(search) if search else "")]:
        if v:
            qs_parts.append(f"{k}={v}")
    if per_page != 25:
        qs_parts.append(f"per_page={per_page}")
    base_qs = ("&" + "&".join(qs_parts)) if qs_parts else ""

    return render(request, "foodcost/inventory_list.html", {
        "country": country,
        "can_edit": can_edit,
        "today": timezone.now().date(),
        "page_obj": page_obj,
        "total_count": paginator.count,
        "base_qs": base_qs,
        "warehouses": Location.objects.filter(country=country).order_by("name"),
        "statuses": Inventory.STATUS_CHOICES,
        "date_from": date_from, "date_to": date_to, "warehouse_id": warehouse_id,
        "status": status, "search": search, "per_page": per_page,
        "kpi_total": kpi_total, "kpi_in_work": kpi_in_work,
        "kpi_awaiting": kpi_awaiting, "kpi_confirmed": kpi_confirmed,
    })


# =========================================================================
# ФОРМА ПОВАРА — «слепой» подсчёт
# =========================================================================
@login_required(login_url="/login/")
def inventory_count(request, country_slug, inventory_id):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_INVENTORY)
    if access_error:
        return access_error

    inventory = get_object_or_404(Inventory, id=inventory_id, country=country)
    editable = inventory.status in (Inventory.STATUS_DRAFT, Inventory.STATUS_IN_PROGRESS)

    if request.method == "POST" and editable:
        action = request.POST.get("action")

        # сохраняем введённые факты (всегда, при любом действии)
        items = list(inventory.items.all())
        for it in items:
            raw = request.POST.get(f"fact_{it.id}")
            if raw is not None:
                val = _clean_decimal(raw)
                it.fact_qty = val  # None = не посчитано
        InventoryItem.objects.bulk_update(items, ["fact_qty"])

        if inventory.status == Inventory.STATUS_DRAFT:
            inventory.status = Inventory.STATUS_IN_PROGRESS
            inventory.started_at = timezone.now()
            inventory.save(update_fields=["status", "started_at", "updated_at"])

        # добавить продукт из справочника
        if action == "add_product":
            pid = _safe_int(request.POST.get("product_id"))
            if not pid:
                return redirect(f"/c/{country.slug}/inventory/{inventory.id}/count/?noitem=1")
            product = Product.objects.filter(id=pid, country=country).first()
            if product and not inventory.items.filter(
                item_type=InventoryItem.ITEM_TYPE_PRODUCT, product=product
            ).exists():
                InventoryItem.objects.create(
                    inventory=inventory,
                    item_type=InventoryItem.ITEM_TYPE_PRODUCT,
                    product=product,
                    system_qty=_current_qty(country, inventory.warehouse_id,
                                            InventoryItem.ITEM_TYPE_PRODUCT, product_id=product.id),
                    fact_qty=None,
                    unit_cost=_product_last_price(country, product.id),
                )
            return redirect(f"/c/{country.slug}/inventory/{inventory.id}/count/?added=1")

        # добавить заготовку из справочника
        if action == "add_preparation":
            prep_id = _safe_int(request.POST.get("preparation_id"))
            if not prep_id:
                return redirect(f"/c/{country.slug}/inventory/{inventory.id}/count/?noitem=1")
            prep = Preparation.objects.filter(id=prep_id, country=country).first()
            if prep and not inventory.items.filter(
                item_type=InventoryItem.ITEM_TYPE_PREPARATION, preparation=prep
            ).exists():
                InventoryItem.objects.create(
                    inventory=inventory,
                    item_type=InventoryItem.ITEM_TYPE_PREPARATION,
                    preparation=prep,
                    system_qty=_current_qty(country, inventory.warehouse_id,
                                            InventoryItem.ITEM_TYPE_PREPARATION, preparation_id=prep.id),
                    fact_qty=None,
                    unit_cost=(prep.cached_cost_per_kg or Decimal(0)),
                )
            return redirect(f"/c/{country.slug}/inventory/{inventory.id}/count/?added=1")

        # удалить строку (случайно добавленную / ненужную)
        if action == "remove_item":
            rid = _safe_int(request.POST.get("remove_id"))
            if rid:
                inventory.items.filter(id=rid).delete()
            return redirect(f"/c/{country.slug}/inventory/{inventory.id}/count/?removed=1")

        if action == "finish":
            inventory.status = Inventory.STATUS_AWAITING
            inventory.finished_at = timezone.now()
            inventory.save(update_fields=["status", "finished_at", "updated_at"])
            DocumentLog.objects.create(
                country=country, document_type=DocumentLog.DOC_INVENTORY,
                document_id=inventory.id, user=request.user, action="finished",
                comment=f"Подсчёт завершён, отправлено на проверку",
            )
            return redirect(f"/c/{country.slug}/inventory/{inventory.id}/count/?finished=1")

        return redirect(f"/c/{country.slug}/inventory/{inventory.id}/count/?saved=1")

    # GET — раздельно продукты и заготовки, БЕЗ системы/денег/расхождений
    items = list(inventory.items.select_related("product", "preparation").all())
    products, preparations = [], []
    filled = 0
    existing_product_ids, existing_prep_ids = set(), set()
    for it in items:
        if it.fact_qty is not None:
            filled += 1
        if it.item_type == InventoryItem.ITEM_TYPE_PRODUCT:
            existing_product_ids.add(it.product_id)
            name = it.product.name if it.product else "—"
            unit = it.product.unit_label() if it.product else "кг"
        else:
            existing_prep_ids.add(it.preparation_id)
            name = it.preparation.name if it.preparation else "—"
            unit = "кг"
        row = {
            "id": it.id,
            "name": name,
            "unit": unit,
            "fact": ("" if it.fact_qty is None else _fmt_qty(it.fact_qty)),
            "filled": it.fact_qty is not None,
        }
        if it.item_type == InventoryItem.ITEM_TYPE_PRODUCT:
            products.append(row)
        else:
            preparations.append(row)
    products.sort(key=lambda r: r["name"].lower())
    preparations.sort(key=lambda r: r["name"].lower())

    avail_products = []
    avail_preparations = []
    if editable:
        avail_products = (
            Product.objects.filter(country=country)
            .exclude(id__in=existing_product_ids).order_by("name")
        )
        avail_preparations = (
            Preparation.objects.filter(country=country)
            .exclude(id__in=existing_prep_ids).order_by("name")
        )

    return render(request, "foodcost/inventory_count.html", {
        "country": country,
        "inventory": inventory,
        "editable": editable,
        "products": products,
        "preparations": preparations,
        "avail_products": avail_products,
        "avail_preparations": avail_preparations,
        "total_items": len(items),
        "filled": filled,
        "saved_flag": request.GET.get("saved") == "1",
        "finished_flag": request.GET.get("finished") == "1",
        "added_flag": request.GET.get("added") == "1",
        "removed_flag": request.GET.get("removed") == "1",
        "noitem_flag": request.GET.get("noitem") == "1",
    })


# =========================================================================
# АНАЛИТИКА МЕНЕДЖЕРА (шаг 3)
# =========================================================================
def _expand_products_per_unit(prep, memo, stack):
    """Сколько каждого ПРОДУКТА уходит на 1 единицу (кг) заготовки —
    рекурсивно, разворачивая вложенные заготовки до продуктов.
    Возвращает {product_id: Decimal qty per 1 kg of prep}. Защита от циклов."""
    if prep is None:
        return {}
    if prep.id in memo:
        return memo[prep.id]
    if prep.id in stack:
        return {}  # цикл — прерываем
    stack.add(prep.id)

    result = defaultdict(lambda: Decimal(0))
    fw = prep.final_weight or Decimal(0)
    if fw and fw != 0:
        # прямые продукты техкарты (gross = валовый расход)
        for item in prep.items.all():
            if item.product_id:
                result[item.product_id] += (item.gross or Decimal(0)) / fw
        # вложенные заготовки
        for sub in prep.subitems.all():
            sub_prep = sub.sub_preparation
            ratio = (sub.gross or Decimal(0)) / fw  # кг под-заготовки на 1 кг этой
            for pid, qty in _expand_products_per_unit(sub_prep, memo, stack).items():
                result[pid] += ratio * qty

    stack.discard(prep.id)
    memo[prep.id] = result
    return result


def _confirm_inventory(inventory, user, country):
    """Подтверждение: остаток склада = факт (по каждой строке).
    Движения inventory_adjustment считаются от ФАКТИЧЕСКОГО текущего остатка
    на момент подтверждения (не от снимка). Непосчитанные позиции пропускаем."""
    wid = inventory.warehouse_id
    with transaction.atomic():
        for it in inventory.items.select_related("product", "preparation").all():
            if it.fact_qty is None:
                continue
            if it.item_type == InventoryItem.ITEM_TYPE_PRODUCT:
                cur = _current_qty(country, wid, InventoryItem.ITEM_TYPE_PRODUCT, product_id=it.product_id)
                delta = it.fact_qty - cur
                if delta != 0:
                    StockMovement.objects.create(
                        country=country, warehouse_id=wid,
                        item_type=StockMovement.ITEM_TYPE_PRODUCT, product_id=it.product_id,
                        quantity_delta=delta,
                        movement_type=StockMovement.TYPE_INVENTORY_ADJUSTMENT,
                        source_type=StockMovement.SOURCE_INVENTORY, source_id=inventory.id,
                        unit_cost=it.unit_cost, total_cost=delta * (it.unit_cost or Decimal(0)),
                        comment=f"Инвентаризация {inventory.document_number}",
                        created_by=user,
                    )
            else:
                cur = _current_qty(country, wid, InventoryItem.ITEM_TYPE_PREPARATION, preparation_id=it.preparation_id)
                delta = it.fact_qty - cur
                if delta != 0:
                    StockMovement.objects.create(
                        country=country, warehouse_id=wid,
                        item_type=StockMovement.ITEM_TYPE_PREPARATION, preparation_id=it.preparation_id,
                        quantity_delta=delta,
                        movement_type=StockMovement.TYPE_INVENTORY_ADJUSTMENT,
                        source_type=StockMovement.SOURCE_INVENTORY, source_id=inventory.id,
                        unit_cost=it.unit_cost, total_cost=delta * (it.unit_cost or Decimal(0)),
                        comment=f"Инвентаризация {inventory.document_number}",
                        created_by=user,
                    )
        inventory.status = Inventory.STATUS_CONFIRMED
        inventory.confirmed_at = timezone.now()
        inventory.save(update_fields=["status", "confirmed_at", "updated_at"])
        DocumentLog.objects.create(
            country=country, document_type=DocumentLog.DOC_INVENTORY,
            document_id=inventory.id, user=user, action="confirmed",
            comment=f"Инвентаризация {inventory.document_number} подтверждена, остатки приведены к факту",
        )


@login_required(login_url="/login/")
def inventory_detail(request, country_slug, inventory_id):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_INVENTORY)
    if access_error:
        return access_error

    can_edit = user_can_edit(request.user)
    inventory = get_object_or_404(Inventory, id=inventory_id, country=country)

    # Аналитика — интерфейс менеджера. Повар (без прав) уходит на форму подсчёта.
    if not can_edit:
        return redirect(f"/c/{country.slug}/inventory/{inventory.id}/count/")

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "confirm" and inventory.status == Inventory.STATUS_AWAITING:
            _confirm_inventory(inventory, request.user, country)
            return redirect(f"/c/{country.slug}/inventory/{inventory.id}/?confirmed=1")

        if action == "reject" and inventory.status == Inventory.STATUS_AWAITING:
            inventory.status = Inventory.STATUS_REJECTED
            inventory.save(update_fields=["status", "updated_at"])
            DocumentLog.objects.create(
                country=country, document_type=DocumentLog.DOC_INVENTORY,
                document_id=inventory.id, user=request.user, action="rejected",
                comment=(request.POST.get("reason") or "Отклонено менеджером"),
            )
            return redirect(f"/c/{country.slug}/inventory/{inventory.id}/?rejected=1")

        if action == "recount" and inventory.status in (Inventory.STATUS_AWAITING, Inventory.STATUS_REJECTED):
            inventory.status = Inventory.STATUS_IN_PROGRESS
            inventory.save(update_fields=["status", "updated_at"])
            DocumentLog.objects.create(
                country=country, document_type=DocumentLog.DOC_INVENTORY,
                document_id=inventory.id, user=request.user, action="recount",
                comment="Возвращено на пересчёт",
            )
            return redirect(f"/c/{country.slug}/inventory/{inventory.id}/count/")

        return redirect(f"/c/{country.slug}/inventory/{inventory.id}/")

    # ----- GET: аналитика -----
    items = list(inventory.items.select_related("product", "preparation").all())

    # «Ушло в заготовки» по продуктам: Σ(факт_заготовки × норма продукта, рекурсивно)
    memo = {}
    gone = defaultdict(lambda: Decimal(0))
    for it in items:
        if it.item_type == InventoryItem.ITEM_TYPE_PREPARATION and it.fact_qty is not None and it.preparation:
            for pid, per in _expand_products_per_unit(it.preparation, memo, set()).items():
                gone[pid] += it.fact_qty * per

    product_rows, prep_rows = [], []
    total_shortage_cost = Decimal(0)   # стоимость недостач (положительные)
    total_surplus_cost = Decimal(0)    # стоимость излишков (по модулю)
    counted_count = 0

    for it in items:
        unit_cost = it.unit_cost or Decimal(0)
        if it.item_type == InventoryItem.ITEM_TYPE_PRODUCT:
            system = it.system_qty or Decimal(0)
            gone_q = gone.get(it.product_id, Decimal(0))
            counted = it.fact_qty is not None
            fact = it.fact_qty if counted else None
            if counted:
                counted_count += 1
                shortage = system - fact - gone_q   # >0 недостача, <0 излишек
                cost = shortage * unit_cost
                if shortage > 0:
                    total_shortage_cost += cost
                elif shortage < 0:
                    total_surplus_cost += -cost
            else:
                shortage = None
                cost = Decimal(0)
            product_rows.append({
                "name": it.product.name if it.product else "—",
                "unit": it.product.unit_label() if it.product else "",
                "system": _fmt_qty(system),
                "fact": (_fmt_qty(fact) if counted else None),
                "gone": _fmt_qty(gone_q),
                "shortage": (_fmt_qty(shortage) if shortage is not None else None),
                "shortage_dir": ("short" if (shortage is not None and shortage > 0) else ("surplus" if (shortage is not None and shortage < 0) else "ok")),
                "cost": (_fmt_money(abs(cost)) if shortage is not None else "—"),
                "counted": counted,
            })
        else:
            system = it.system_qty or Decimal(0)
            counted = it.fact_qty is not None
            fact = it.fact_qty if counted else None
            if counted:
                counted_count += 1
                diff = fact - system            # >0 излишек, <0 недостача
                shortage = -diff                # >0 недостача
                cost = shortage * unit_cost
                if shortage > 0:
                    total_shortage_cost += cost
                elif shortage < 0:
                    total_surplus_cost += -cost
            else:
                diff = None
                cost = Decimal(0)
            prep_rows.append({
                "name": it.preparation.name if it.preparation else "—",
                "unit": "кг",
                "system": _fmt_qty(system),
                "fact": (_fmt_qty(fact) if counted else None),
                "diff": (_fmt_qty(diff) if diff is not None else None),
                "diff_dir": ("surplus" if (diff is not None and diff > 0) else ("short" if (diff is not None and diff < 0) else "ok")),
                "cost": (_fmt_money(abs(cost)) if diff is not None else "—"),
                "counted": counted,
            })

    product_rows.sort(key=lambda r: r["name"].lower())
    prep_rows.sort(key=lambda r: r["name"].lower())

    logs = (
        DocumentLog.objects
        .filter(country=country, document_type=DocumentLog.DOC_INVENTORY, document_id=inventory.id)
        .select_related("user").order_by("-created_at")[:50]
    )

    return render(request, "foodcost/inventory_detail.html", {
        "country": country,
        "inventory": inventory,
        "is_awaiting": inventory.status == Inventory.STATUS_AWAITING,
        "is_confirmed": inventory.status == Inventory.STATUS_CONFIRMED,
        "is_rejected": inventory.status == Inventory.STATUS_REJECTED,
        "editable_stage": inventory.status in (Inventory.STATUS_DRAFT, Inventory.STATUS_IN_PROGRESS),
        "product_rows": product_rows,
        "prep_rows": prep_rows,
        "counted_count": counted_count,
        "total_items": len(items),
        "total_shortage_display": _fmt_money(total_shortage_cost),
        "total_surplus_display": _fmt_money(total_surplus_cost),
        "net_display": _fmt_money(total_shortage_cost - total_surplus_cost),
        "logs": logs,
        "confirmed_flag": request.GET.get("confirmed") == "1",
        "rejected_flag": request.GET.get("rejected") == "1",
    })
