# -*- coding: utf-8 -*-
"""
Фаза 2 (ЗАКАЗЫ/КАССА, аналитика): ограничение по филиалам — order_analytics.

Сужаем всё, что вычисляется из заказов, до филиалов пользователя
(user_location_ids по Order.location). Производные (ТОП блюд, методы оплаты,
источники, доставка, касса/банк, средний чек) считаются из уже суженного
снимка active — отдельно их трогать не нужно.

Точечно по источникам данных:
  • orders_qs            — основной снимок заказов периода → свои филиалы;
  • writeoffs_cost       — WriteOff сужается по location (есть такое поле);
                           ЗАОДНО чинит отмеченный в коде баг: раньше списания
                           мазались на каждый филиал общей суммой по стране;
  • _expense()           — FinancialExpense сужается по location ТОЛЬКО если у
                           модели есть поле location (иначе остаётся как есть);
  • prior_customer_ids   — «повторные» клиенты считаются в разрезе филиала;
  • hour_rows / day_rows — графики по часам/дням → свои филиалы;
  • сводка по филиалам    — только филиалы пользователя.

Супер-админ / без филиалов видят всё. Миграций нет. Идемпотентно.
Требует Фазу 1 (branch_scope.py). Можно применять независимо от патча ядра
заказов — импорт и helper'ы скрипт обеспечивает сам.
Запуск из корня проекта: python apply_branch_phase2_analytics.py
"""
import ast
import pathlib
import sys

O = "foodcost/order_views.py"
SCOPE = "foodcost/branch_scope.py"

HELPERS = {
    "scope_locations": '''

def scope_locations(location_qs, user):
    """Сузить справочник складов/филиалов (Location) до филиалов пользователя."""
    ids = user_location_ids(user)
    return location_qs if ids is None else location_qs.filter(id__in=list(ids))
''',
    "scope_if_field": '''

def scope_if_field(qs, user, field="location"):
    """Как scope_by_location, но тихо пропускает фильтр, если у модели нет
    такого поля (например, расход без привязки к филиалу)."""
    try:
        qs.model._meta.get_field(field)
    except Exception:
        return qs
    return scope_by_location(qs, user, field=field)
''',
}

IMPORT_ANCHOR = "logger = logging.getLogger(__name__)"
IMPORT_REPL = (
    "from .branch_scope import scope_by_location, scope_locations, scope_if_field, user_location_ids\n"
    "\n"
    "logger = logging.getLogger(__name__)"
)

EDITS = [
    (IMPORT_ANCHOR, IMPORT_REPL),

    # orders_qs — основной снимок
    ('    orders_qs = (\n'
     '        Order.objects\n'
     '        .filter(\n'
     '            country=country,\n'
     '            order_date__date__gte=date_from,\n'
     '            order_date__date__lte=date_to,\n'
     '        )\n'
     '        .select_related(\n'
     '            "location",\n'
     '            "payment_method",\n'
     '            "source",\n'
     '            "cancel_reason",\n'
     '            "customer",\n'
     '        )\n'
     '        .order_by("-order_date")\n'
     '    )\n',
     '    orders_qs = (\n'
     '        Order.objects\n'
     '        .filter(\n'
     '            country=country,\n'
     '            order_date__date__gte=date_from,\n'
     '            order_date__date__lte=date_to,\n'
     '        )\n'
     '        .select_related(\n'
     '            "location",\n'
     '            "payment_method",\n'
     '            "source",\n'
     '            "cancel_reason",\n'
     '            "customer",\n'
     '        )\n'
     '        .order_by("-order_date")\n'
     '    )\n'
     '    orders_qs = scope_by_location(orders_qs, request.user, field="location")  # филиалы пользователя\n'),

    # writeoffs_cost — WriteOff по location (+ фикс бага общей суммы)
    ('    writeoffs_cost = (\n'
     '        WriteOff.objects\n'
     '        .filter(\n'
     '            country=country,\n'
     '            writeoff_date__gte=date_from,\n'
     '            writeoff_date__lte=date_to,\n'
     '        )\n'
     '        .aggregate(total=Sum("cost"))["total"]\n'
     '        or ZERO\n'
     '    )',
     '    writeoffs_cost = (\n'
     '        scope_by_location(\n'
     '            WriteOff.objects.filter(\n'
     '                country=country,\n'
     '                writeoff_date__gte=date_from,\n'
     '                writeoff_date__lte=date_to,\n'
     '            ),\n'
     '            request.user, field="location",\n'
     '        )\n'
     '        .aggregate(total=Sum("cost"))["total"]\n'
     '        or ZERO\n'
     '    )'),

    # _expense() — FinancialExpense по location, только если поле есть
    ('    def _expense(expense_type):\n'
     '        return (\n'
     '            FinancialExpense.objects\n'
     '            .filter(\n'
     '                country=country,\n'
     '                expense_type=expense_type,\n'
     '                expense_date__gte=date_from,\n'
     '                expense_date__lte=date_to,\n'
     '            )\n'
     '            .aggregate(total=Sum("amount"))["total"]\n'
     '            or ZERO\n'
     '        )',
     '    def _expense(expense_type):\n'
     '        return (\n'
     '            scope_if_field(\n'
     '                FinancialExpense.objects.filter(\n'
     '                    country=country,\n'
     '                    expense_type=expense_type,\n'
     '                    expense_date__gte=date_from,\n'
     '                    expense_date__lte=date_to,\n'
     '                ),\n'
     '                request.user, field="location",\n'
     '            )\n'
     '            .aggregate(total=Sum("amount"))["total"]\n'
     '            or ZERO\n'
     '        )'),

    # prior_customer_ids — «повторные» в разрезе филиала
    ('    prior_customer_ids = set(\n'
     '        Order.objects\n'
     '        .filter(\n'
     '            country=country,\n'
     '            is_cancelled=False,\n'
     '            order_date__date__lt=date_from,\n'
     '            customer_id__in=active_customer_ids,\n'
     '        )\n'
     '        .values_list("customer_id", flat=True)\n'
     '        .distinct()\n'
     '    )',
     '    prior_customer_ids = set(\n'
     '        scope_by_location(\n'
     '            Order.objects.filter(\n'
     '                country=country,\n'
     '                is_cancelled=False,\n'
     '                order_date__date__lt=date_from,\n'
     '                customer_id__in=active_customer_ids,\n'
     '            ),\n'
     '            request.user, field="location",\n'
     '        )\n'
     '        .values_list("customer_id", flat=True)\n'
     '        .distinct()\n'
     '    )'),

    # сводка по филиалам — только филиалы пользователя
    ('    locations = (\n'
     '        Location.objects\n'
     '        .filter(country=country, is_active=True)\n'
     '        .order_by("name")\n'
     '    )',
     '    locations = scope_locations(\n'
     '        Location.objects\n'
     '        .filter(country=country, is_active=True)\n'
     '        .order_by("name"),\n'
     '        request.user,\n'
     '    )'),

    # hour_rows — по часам
    ('    hour_rows = (\n'
     '        Order.objects\n'
     '        .filter(\n'
     '            country=country,\n'
     '            order_date__date__gte=date_from,\n'
     '            order_date__date__lte=date_to,\n'
     '            is_cancelled=False,\n'
     '        )\n'
     '        .annotate(h=ExtractHour("order_date"))',
     '    hour_rows = (\n'
     '        scope_by_location(\n'
     '            Order.objects.filter(\n'
     '                country=country,\n'
     '                order_date__date__gte=date_from,\n'
     '                order_date__date__lte=date_to,\n'
     '                is_cancelled=False,\n'
     '            ),\n'
     '            request.user, field="location",\n'
     '        )\n'
     '        .annotate(h=ExtractHour("order_date"))'),

    # day_rows — по дням
    ('    day_rows = (\n'
     '        Order.objects\n'
     '        .filter(\n'
     '            country=country,\n'
     '            order_date__date__gte=date_from,\n'
     '            order_date__date__lte=date_to,\n'
     '        )\n'
     '        .annotate(d=TruncDate("order_date"))',
     '    day_rows = (\n'
     '        scope_by_location(\n'
     '            Order.objects.filter(\n'
     '                country=country,\n'
     '                order_date__date__gte=date_from,\n'
     '                order_date__date__lte=date_to,\n'
     '            ),\n'
     '            request.user, field="location",\n'
     '        )\n'
     '        .annotate(d=TruncDate("order_date"))'),
]


def ensure_helpers():
    p = pathlib.Path(SCOPE)
    if not p.exists():
        print(f"! нет {SCOPE} — сначала примени Фазу 1"); sys.exit(1)
    s = p.read_text(encoding="utf-8")
    added = []
    for name, code in HELPERS.items():
        if f"def {name}(" not in s:
            s = s.rstrip() + "\n" + code
            added.append(name)
    if added:
        ast.parse(s)
        p.write_text(s, encoding="utf-8")
        print(f"✓ {SCOPE}: добавлены helper'ы: {', '.join(added)}")


def main():
    ensure_helpers()
    p = pathlib.Path(O)
    if not p.exists():
        print(f"! не найден {O} — запусти из корня проекта"); sys.exit(1)
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
        print(f"✓ {O}: применено правок: {changed}")
    else:
        print(f"~ {O}: уже применено")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e); sys.exit(1)
