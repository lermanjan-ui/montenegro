# -*- coding: utf-8 -*-
"""
Фаза 2 (СКЛАД, остатки): ограничение по филиалам — views_stock.py.
«Филиал» = склад (Location), поле StockMovement.warehouse.

Все остатки считаются из StockMovement как Σ quantity_delta, поэтому
достаточно сузить базовые queryset'ы — таблицы, KPI и суммы наследуют фильтр:

  • stock_list (Остатки): база движений сужена до складов пользователя,
    выпадашка складов сужена → строки, KPI (позиции/сумма/ниже минимума/
    отрицательные) считаются только по его филиалам;
  • stock_movements (Журнал движений): список и KPI сужены, выпадашка складов сужена;
  • stock_product_detail (Карточка товара): остаток по складам и журнал
    движений сужены до филиалов пользователя (итого/стоимость — тоже).

Супер-админ / без филиалов видят всё. Только чтение, миграций нет. Идемпотентно.
Требует Фазу 1 (branch_scope.py); скрипт дописывает helper scope_locations,
если его ещё нет (тот же, что в части A склада).
Запуск из корня проекта: python apply_branch_phase2_stock.py
"""
import ast
import pathlib
import sys

S = "foodcost/views_stock.py"
SCOPE = "foodcost/branch_scope.py"

IMPORT_OLD = (
    "from .views import (\n"
    "    get_country,\n"
    "    require_section_access,\n"
    ")"
)
IMPORT_NEW = IMPORT_OLD + "\nfrom .branch_scope import scope_by_location, scope_locations"

SCOPE_LOCATIONS_FN = '''

def scope_locations(location_qs, user):
    """Сузить справочник складов/филиалов (Location) до филиалов пользователя.
    None из user_location_ids = полный доступ (супер-админ)."""
    ids = user_location_ids(user)
    return location_qs if ids is None else location_qs.filter(id__in=list(ids))
'''

EDITS = [
    (IMPORT_OLD, IMPORT_NEW),

    # stock_list: выпадашка складов
    ('    locations = Location.objects.filter(country=country).order_by("name")\n'
     '    loc_name = {loc.id: loc.name for loc in locations}',
     '    locations = scope_locations(Location.objects.filter(country=country).order_by("name"), request.user)\n'
     '    loc_name = {loc.id: loc.name for loc in locations}'),

    # stock_list: база остатков
    ('    base = StockMovement.objects.filter(country=country)\n'
     '    if warehouse_id:\n'
     '        base = base.filter(warehouse_id=warehouse_id)',
     '    base = StockMovement.objects.filter(country=country)\n'
     '    base = scope_by_location(base, request.user, field="warehouse")  # филиалы пользователя\n'
     '    if warehouse_id:\n'
     '        base = base.filter(warehouse_id=warehouse_id)'),

    # stock_movements: база журнала
    ('    qs = (\n'
     '        StockMovement.objects\n'
     '        .filter(country=country)\n'
     '        .select_related("warehouse", "product", "preparation", "created_by")\n'
     '    )\n',
     '    qs = (\n'
     '        StockMovement.objects\n'
     '        .filter(country=country)\n'
     '        .select_related("warehouse", "product", "preparation", "created_by")\n'
     '    )\n'
     '    qs = scope_by_location(qs, request.user, field="warehouse")  # филиалы пользователя\n'),

    # stock_movements: выпадашка складов
    ('        "warehouses": Location.objects.filter(country=country).order_by("name"),\n'
     '        "movement_types": StockMovement.MOVEMENT_TYPE_CHOICES,',
     '        "warehouses": scope_locations(Location.objects.filter(country=country).order_by("name"), request.user),\n'
     '        "movement_types": StockMovement.MOVEMENT_TYPE_CHOICES,'),

    # stock_product_detail: остаток по складам
    ('    balances = (\n'
     '        StockMovement.objects\n'
     '        .filter(country=country, item_type=StockMovement.ITEM_TYPE_PRODUCT, product=product)\n'
     '        .values("warehouse_id").annotate(qty=Sum("quantity_delta"))\n'
     '    )',
     '    balances = (\n'
     '        scope_by_location(\n'
     '            StockMovement.objects\n'
     '            .filter(country=country, item_type=StockMovement.ITEM_TYPE_PRODUCT, product=product),\n'
     '            request.user, field="warehouse",\n'
     '        )\n'
     '        .values("warehouse_id").annotate(qty=Sum("quantity_delta"))\n'
     '    )'),

    # stock_product_detail: последние движения
    ('    for m in (\n'
     '        StockMovement.objects\n'
     '        .filter(country=country, item_type=StockMovement.ITEM_TYPE_PRODUCT, product=product)\n'
     '        .select_related("warehouse", "created_by")\n'
     '        .order_by("-movement_datetime", "-id")[:100]\n'
     '    ):',
     '    for m in (\n'
     '        scope_by_location(\n'
     '            StockMovement.objects\n'
     '            .filter(country=country, item_type=StockMovement.ITEM_TYPE_PRODUCT, product=product),\n'
     '            request.user, field="warehouse",\n'
     '        )\n'
     '        .select_related("warehouse", "created_by")\n'
     '        .order_by("-movement_datetime", "-id")[:100]\n'
     '    ):'),
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


def main():
    ensure_scope_locations()
    p = pathlib.Path(S)
    if not p.exists():
        print(f"! не найден {S} — запусти из корня проекта"); sys.exit(1)
    s = p.read_text(encoding="utf-8")
    changed = 0
    for anchor, repl in EDITS:
        if repl in s:
            continue
        n = s.count(anchor)
        if n != 1:
            print(f"! якорь найден {n} раз (ожидалось 1), не меняю:\n---\n{anchor[:110]}\n---")
            sys.exit(1)
        s = s.replace(anchor, repl, 1)
        changed += 1
    if changed:
        ast.parse(s)
        p.write_text(s, encoding="utf-8")
        print(f"✓ {S}: применено изменений: {changed}")
    else:
        print(f"~ {S}: уже применено")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e); sys.exit(1)
