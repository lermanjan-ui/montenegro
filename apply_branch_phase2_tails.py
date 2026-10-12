# -*- coding: utf-8 -*-
"""
Фаза 2 — два хвоста:
  (а) customer_list: суммы в строках клиентов (orders_count / total_spent /
      последний заказ / сайт / tilda / обеды) считаются ТОЛЬКО по заказам
      филиалов пользователя. Супер-админ / без филиалов — по всем (как было).
  (б) перемещения: селект «Откуда» в карточке сужается до филиалов пользователя
      (отдельный список from_warehouses), «Куда» остаётся полным (хаб-правило).

Требует применённую Фазу 2 (order_views.py и views_transfers.py уже
пропатчены, helper'ы scope_* есть в branch_scope.py). Миграций нет. Идемпотентно.
Запуск из корня проекта: python apply_branch_phase2_tails.py
"""
import ast
import pathlib
import sys

O = "foodcost/order_views.py"
T = "foodcost/views_transfers.py"
TPL = "foodcost/templates/foodcost/transfer_detail.html"


# ---- (а) customer_list: аннотации сумм по филиалу -------------------------
O_ANNOTATE_OLD = '''    customers = (
        Customer.objects
        .filter(country=country)
        .annotate(
            orders_count=Count("orders", distinct=True),
            total_spent=Coalesce(
                Sum("orders__total_amount"),
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
    )'''

O_ANNOTATE_NEW = '''    # Суммы по клиенту — только по заказам филиалов пользователя (как и
    # видимость ниже). None = все филиалы (супер-админ) → без фильтра, как было.
    _ids = user_location_ids(request.user)
    _bq = Q(orders__location_id__in=_ids) if _ids is not None else None

    if _bq is None:
        _a_orders = Count("orders", distinct=True)
        _a_spent = Coalesce(Sum("orders__total_amount"), Value(Decimal("0")), output_field=money_field)
        _a_last = Max("orders__order_date")
        _a_site = Count("orders", filter=Q(orders__is_legacy_import=False), distinct=True)
        _a_tilda = Count("orders", filter=Q(orders__is_legacy_import=True), distinct=True)
        _a_lunch = Count("orders__lunch_combos", distinct=True)
    else:
        _a_orders = Count("orders", filter=_bq, distinct=True)
        _a_spent = Coalesce(Sum("orders__total_amount", filter=_bq), Value(Decimal("0")), output_field=money_field)
        _a_last = Max("orders__order_date", filter=_bq)
        _a_site = Count("orders", filter=Q(orders__is_legacy_import=False) & _bq, distinct=True)
        _a_tilda = Count("orders", filter=Q(orders__is_legacy_import=True) & _bq, distinct=True)
        _a_lunch = Count("orders__lunch_combos", filter=_bq, distinct=True)

    customers = (
        Customer.objects
        .filter(country=country)
        .annotate(
            orders_count=_a_orders,
            total_spent=_a_spent,
            last_order_date=_a_last,
            site_orders=_a_site,
            tilda_orders=_a_tilda,
            lunch_orders=_a_lunch,
        )
    )'''


# ---- (б) views_transfers.py ----------------------------------------------
T_IMPORT_OLD = "from .branch_scope import scope_any_location, user_location_ids"
T_IMPORT_NEW = "from .branch_scope import scope_any_location, scope_locations, user_location_ids"

T_CTX_OLD = ('        "warehouses": Location.objects.filter(country=country).order_by("name"),\n'
             '        "products": Product.objects.filter(country=country).order_by("name"),')
T_CTX_NEW = ('        "warehouses": Location.objects.filter(country=country).order_by("name"),\n'
             '        "from_warehouses": scope_locations(Location.objects.filter(country=country).order_by("name"), request.user),\n'
             '        "products": Product.objects.filter(country=country).order_by("name"),')


# ---- (б) transfer_detail.html: селект «Откуда» ----------------------------
TPL_OLD = ('<select class="fc-select" name="from_warehouse_id" id="hdr_from">'
           '<option value="">— выбрать —</option>'
           '{% for w in warehouses %}'
           '<option value="{{ w.id }}" {% if transfer.from_warehouse_id == w.id %}selected{% endif %}>{{ w.name }}</option>'
           '{% endfor %}</select>')
TPL_NEW = ('<select class="fc-select" name="from_warehouse_id" id="hdr_from">'
           '<option value="">— выбрать —</option>'
           '{% for w in from_warehouses %}'
           '<option value="{{ w.id }}" {% if transfer.from_warehouse_id == w.id %}selected{% endif %}>{{ w.name }}</option>'
           '{% endfor %}</select>')


def patch_py(path, edits):
    p = pathlib.Path(path)
    if not p.exists():
        print(f"! не найден {path} — запусти из корня проекта"); sys.exit(1)
    s = p.read_text(encoding="utf-8")
    changed = 0
    for old, new in edits:
        if new in s:
            continue
        n = s.count(old)
        if n != 1:
            print(f"! {path}: якорь найден {n} раз (ожидалось 1):\n---\n{old[:110]}\n---"); sys.exit(1)
        s = s.replace(old, new, 1)
        changed += 1
    if changed:
        ast.parse(s)
        p.write_text(s, encoding="utf-8")
        print(f"✓ {path}: применено правок: {changed}")
    else:
        print(f"~ {path}: уже применено")


def patch_text(path, old, new):
    p = pathlib.Path(path)
    if not p.exists():
        print(f"! не найден шаблон {path} — проверь путь к templates"); sys.exit(1)
    s = p.read_text(encoding="utf-8")
    if new in s:
        print(f"~ {path}: уже применено"); return
    n = s.count(old)
    if n != 1:
        print(f"! {path}: якорь найден {n} раз (ожидалось 1) — селект «Откуда» не тронут"); sys.exit(1)
    p.write_text(s.replace(old, new, 1), encoding="utf-8")
    print(f"✓ {path}: селект «Откуда» сужен (from_warehouses)")


def main():
    patch_py(O, [(O_ANNOTATE_OLD, O_ANNOTATE_NEW)])
    patch_py(T, [(T_IMPORT_OLD, T_IMPORT_NEW), (T_CTX_OLD, T_CTX_NEW)])
    patch_text(TPL, TPL_OLD, TPL_NEW)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e); sys.exit(1)
