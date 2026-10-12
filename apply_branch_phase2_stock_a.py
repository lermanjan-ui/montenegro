# -*- coding: utf-8 -*-
"""
Фаза 2 (СКЛАД, часть A): ограничение по филиалам — ПРИХОДЫ и ИНВЕНТАРИЗАЦИЯ.
«Филиал» на складе = склад (Location). Поле у этих документов — warehouse.

Что меняется:
  ПРИХОДЫ (views_purchases.py)
    • список сужается до складов пользователя (+ его собственные черновики
      без склада — чтобы не терять только что созданный черновик);
    • KPI считаются в рамках его складов;
    • выпадашка складов (фильтр и карточка) сужена;
    • карточка чужого прихода недоступна (redirect на список);
    • нельзя назначить приход на чужой склад (защита POST-а).
  ИНВЕНТАРИЗАЦИЯ (views_inventory.py)
    • журнал и KPI сужены до складов пользователя;
    • выпадашка складов сужена;
    • нельзя создать инвентаризацию по чужому складу;
    • форма подсчёта и аналитика чужого документа недоступны.

Перемещения и остатки — отдельно. Требует Фазу 1 (branch_scope.py);
скрипт при необходимости дописывает туда helper scope_locations.
Супер-админ / без филиалов видят всё. Миграций нет. Идемпотентно.
Запуск из корня проекта: python apply_branch_phase2_stock_a.py
"""
import ast
import pathlib
import sys

PURCH = "foodcost/views_purchases.py"
INV = "foodcost/views_inventory.py"
SCOPE = "foodcost/branch_scope.py"

IMPORT_OLD = (
    "from .views import (\n"
    "    get_country,\n"
    "    require_section_access,\n"
    "    user_can_edit,\n"
    ")"
)
IMPORT_NEW = IMPORT_OLD + (
    "\nfrom .branch_scope import scope_by_location, scope_locations, user_location_ids"
)

SCOPE_LOCATIONS_FN = '''

def scope_locations(location_qs, user):
    """Сузить справочник складов/филиалов (Location) до филиалов пользователя.
    None из user_location_ids = полный доступ (супер-админ)."""
    ids = user_location_ids(user)
    return location_qs if ids is None else location_qs.filter(id__in=list(ids))
'''

PURCH_EDITS = [
    (IMPORT_OLD, IMPORT_NEW),

    # список: scope qs (+ собственные черновики без склада)
    ('    qs = (\n'
     '        PurchaseReceipt.objects\n'
     '        .filter(country=country)\n'
     '        .select_related("supplier", "warehouse", "created_by")\n'
     '        .annotate(items_count=Count("items"))\n'
     '    )\n',
     '    qs = (\n'
     '        PurchaseReceipt.objects\n'
     '        .filter(country=country)\n'
     '        .select_related("supplier", "warehouse", "created_by")\n'
     '        .annotate(items_count=Count("items"))\n'
     '    )\n'
     '    _ids = user_location_ids(request.user)  # None = все филиалы (супер-админ)\n'
     '    if _ids is not None:\n'
     '        qs = qs.filter(Q(warehouse_id__in=_ids) | Q(warehouse__isnull=True, created_by=request.user))\n'),

    # KPI: scoped base
    ('    # ----- KPI (по стране, не зависят от фильтров) -----\n'
     '    confirmed = PurchaseReceipt.STATUS_CONFIRMED\n'
     '    kpi_total = PurchaseReceipt.objects.filter(country=country).count()\n'
     '    kpi_sum = (\n'
     '        PurchaseReceipt.objects\n'
     '        .filter(country=country, status=confirmed)\n'
     '        .aggregate(s=Sum("total_amount"))["s"] or Decimal(0)\n'
     '    )\n'
     '    unpaid = (\n'
     '        PurchaseReceipt.objects\n'
     '        .filter(country=country, status=confirmed, is_paid=False)\n'
     '        .aggregate(c=Count("id"), s=Sum("total_amount"))\n'
     '    )\n'
     '    kpi_unpaid_count = unpaid["c"] or 0\n'
     '    kpi_unpaid_sum = unpaid["s"] or Decimal(0)\n'
     '    kpi_suppliers = (\n'
     '        PurchaseReceipt.objects\n'
     '        .filter(country=country, status=confirmed, supplier__isnull=False)\n'
     '        .values("supplier_id").distinct().count()\n'
     '    )',
     '    # ----- KPI (в рамках филиалов пользователя) -----\n'
     '    confirmed = PurchaseReceipt.STATUS_CONFIRMED\n'
     '    _kpi = scope_by_location(\n'
     '        PurchaseReceipt.objects.filter(country=country), request.user, field="warehouse"\n'
     '    )\n'
     '    kpi_total = _kpi.count()\n'
     '    kpi_sum = (\n'
     '        _kpi.filter(status=confirmed)\n'
     '        .aggregate(s=Sum("total_amount"))["s"] or Decimal(0)\n'
     '    )\n'
     '    unpaid = (\n'
     '        _kpi.filter(status=confirmed, is_paid=False)\n'
     '        .aggregate(c=Count("id"), s=Sum("total_amount"))\n'
     '    )\n'
     '    kpi_unpaid_count = unpaid["c"] or 0\n'
     '    kpi_unpaid_sum = unpaid["s"] or Decimal(0)\n'
     '    kpi_suppliers = (\n'
     '        _kpi.filter(status=confirmed, supplier__isnull=False)\n'
     '        .values("supplier_id").distinct().count()\n'
     '    )'),

    # список: выпадашка складов (перед suppliers)
    ('        "warehouses": Location.objects.filter(country=country).order_by("name"),\n'
     '        "suppliers": Supplier.objects.filter(country=country).order_by("name"),',
     '        "warehouses": scope_locations(Location.objects.filter(country=country).order_by("name"), request.user),\n'
     '        "suppliers": Supplier.objects.filter(country=country).order_by("name"),'),

    # карточка: доступ только к своему филиалу (+ собственный черновик)
    ('    receipt = get_object_or_404(PurchaseReceipt, id=receipt_id, country=country)\n',
     '    receipt = get_object_or_404(PurchaseReceipt, id=receipt_id, country=country)\n'
     '    _ids = user_location_ids(request.user)  # None = супер-админ (все филиалы)\n'
     '    if _ids is not None and receipt.warehouse_id not in _ids and receipt.created_by_id != request.user.id:\n'
     '        return redirect(f"/c/{country.slug}/purchases/")\n'),

    # update_header: нельзя назначить чужой склад
    ('                receipt.warehouse = (\n'
     '                    Location.objects.filter(id=warehouse_id, country=country).first()\n'
     '                    if warehouse_id else None\n'
     '                )',
     '                _wh = Location.objects.filter(id=warehouse_id, country=country).first() if warehouse_id else None\n'
     '                if _wh and _ids is not None and _wh.id not in _ids:\n'
     '                    _wh = None  # нельзя назначить приход на чужой филиал\n'
     '                receipt.warehouse = _wh'),

    # add_item (подхват шапки): тот же запрет чужого склада
    ('                receipt.warehouse = (\n'
     '                    Location.objects.filter(id=hw, country=country).first() if hw else None\n'
     '                )',
     '                _wh = Location.objects.filter(id=hw, country=country).first() if hw else None\n'
     '                if _wh and _ids is not None and _wh.id not in _ids:\n'
     '                    _wh = None  # нельзя назначить приход на чужой филиал\n'
     '                receipt.warehouse = _wh'),

    # карточка: выпадашка складов (перед products)
    ('        "warehouses": Location.objects.filter(country=country).order_by("name"),\n'
     '        "products": Product.objects.filter(country=country).order_by("name"),',
     '        "warehouses": scope_locations(Location.objects.filter(country=country).order_by("name"), request.user),\n'
     '        "products": Product.objects.filter(country=country).order_by("name"),'),
]

INV_EDITS = [
    (IMPORT_OLD, IMPORT_NEW),

    # создание: нельзя по чужому складу
    ('            warehouse = Location.objects.filter(\n'
     '                id=request.POST.get("warehouse_id"), country=country\n'
     '            ).first()\n'
     '            if warehouse:',
     '            warehouse = Location.objects.filter(\n'
     '                id=request.POST.get("warehouse_id"), country=country\n'
     '            ).first()\n'
     '            _ids = user_location_ids(request.user)\n'
     '            if warehouse and _ids is not None and warehouse.id not in _ids:\n'
     '                warehouse = None  # нельзя создавать инвентаризацию по чужому филиалу\n'
     '            if warehouse:'),

    # журнал: scope qs
    ('    qs = (\n'
     '        Inventory.objects\n'
     '        .filter(country=country)\n'
     '        .select_related("warehouse", "created_by")\n'
     '        .annotate(items_count=Count("items"))\n'
     '    )\n',
     '    qs = (\n'
     '        Inventory.objects\n'
     '        .filter(country=country)\n'
     '        .select_related("warehouse", "created_by")\n'
     '        .annotate(items_count=Count("items"))\n'
     '    )\n'
     '    qs = scope_by_location(qs, request.user, field="warehouse")  # филиалы пользователя\n'),

    # KPI: scoped base
    ('    kpi_total = Inventory.objects.filter(country=country).count()\n'
     '    kpi_in_work = Inventory.objects.filter(\n'
     '        country=country, status__in=[Inventory.STATUS_DRAFT, Inventory.STATUS_IN_PROGRESS]\n'
     '    ).count()\n'
     '    kpi_awaiting = Inventory.objects.filter(country=country, status=Inventory.STATUS_AWAITING).count()\n'
     '    kpi_confirmed = Inventory.objects.filter(country=country, status=Inventory.STATUS_CONFIRMED).count()',
     '    _kpi = scope_by_location(\n'
     '        Inventory.objects.filter(country=country), request.user, field="warehouse"\n'
     '    )\n'
     '    kpi_total = _kpi.count()\n'
     '    kpi_in_work = _kpi.filter(\n'
     '        status__in=[Inventory.STATUS_DRAFT, Inventory.STATUS_IN_PROGRESS]\n'
     '    ).count()\n'
     '    kpi_awaiting = _kpi.filter(status=Inventory.STATUS_AWAITING).count()\n'
     '    kpi_confirmed = _kpi.filter(status=Inventory.STATUS_CONFIRMED).count()'),

    # журнал: выпадашка складов
    ('        "warehouses": Location.objects.filter(country=country).order_by("name"),\n'
     '        "statuses": Inventory.STATUS_CHOICES,',
     '        "warehouses": scope_locations(Location.objects.filter(country=country).order_by("name"), request.user),\n'
     '        "statuses": Inventory.STATUS_CHOICES,'),

    # форма подсчёта: доступ только к своему филиалу
    ('    inventory = get_object_or_404(Inventory, id=inventory_id, country=country)\n'
     '    editable = inventory.status in (Inventory.STATUS_DRAFT, Inventory.STATUS_IN_PROGRESS)',
     '    inventory = get_object_or_404(Inventory, id=inventory_id, country=country)\n'
     '    _ids = user_location_ids(request.user)\n'
     '    if _ids is not None and inventory.warehouse_id not in _ids and inventory.created_by_id != request.user.id:\n'
     '        return redirect(f"/c/{country.slug}/inventory/")\n'
     '    editable = inventory.status in (Inventory.STATUS_DRAFT, Inventory.STATUS_IN_PROGRESS)'),

    # аналитика менеджера: доступ только к своему филиалу
    ('    inventory = get_object_or_404(Inventory, id=inventory_id, country=country)\n'
     '\n'
     '    # Аналитика — интерфейс менеджера. Повар (без прав) уходит на форму подсчёта.\n'
     '    if not can_edit:\n'
     '        return redirect(f"/c/{country.slug}/inventory/{inventory.id}/count/")',
     '    inventory = get_object_or_404(Inventory, id=inventory_id, country=country)\n'
     '    _ids = user_location_ids(request.user)\n'
     '    if _ids is not None and inventory.warehouse_id not in _ids and inventory.created_by_id != request.user.id:\n'
     '        return redirect(f"/c/{country.slug}/inventory/")\n'
     '\n'
     '    # Аналитика — интерфейс менеджера. Повар (без прав) уходит на форму подсчёта.\n'
     '    if not can_edit:\n'
     '        return redirect(f"/c/{country.slug}/inventory/{inventory.id}/count/")'),
]


def ensure_scope_locations():
    p = pathlib.Path(SCOPE)
    if not p.exists():
        print(f"! нет {SCOPE} — сначала примени Фазу 1"); sys.exit(1)
    s = p.read_text(encoding="utf-8")
    if "def scope_locations(" in s:
        return
    s = s.rstrip() + "\n" + SCOPE_LOCATIONS_FN
    ast.parse(s)
    p.write_text(s, encoding="utf-8")
    print(f"✓ {SCOPE}: добавлен helper scope_locations")


def apply(path, edits):
    p = pathlib.Path(path)
    if not p.exists():
        print(f"! не найден {path} — запусти из корня проекта"); sys.exit(1)
    s = p.read_text(encoding="utf-8")
    changed = 0
    for anchor, repl in edits:
        if repl in s:
            continue
        n = s.count(anchor)
        if n != 1:
            print(f"! {path}: якорь найден {n} раз (ожидалось 1), не меняю:\n---\n{anchor[:110]}\n---")
            sys.exit(1)
        s = s.replace(anchor, repl, 1)
        changed += 1
    if changed:
        ast.parse(s)
        p.write_text(s, encoding="utf-8")
        print(f"✓ {path}: применено изменений: {changed}")
    else:
        print(f"~ {path}: уже применено")


def main():
    ensure_scope_locations()
    apply(PURCH, PURCH_EDITS)
    apply(INV, INV_EDITS)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e); sys.exit(1)
