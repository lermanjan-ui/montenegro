# -*- coding: utf-8 -*-
"""
Фаза 2 (СМЕНЫ): ограничение по филиалам сотрудника.
  • views_employees.shifts_journal — журнал смен: список + выпадашки
    филиалов/сотрудников сужаются до филиалов пользователя;
  • shift_views.shift_handover_list / shift_handover_admin — передача смены:
    список ограничивается филиалами пользователя.
Старт смены у повара (employee_me) уже самоограничен (только свои смены).
Фильтрация только на чтение — записи не трогаем. Супер-админ и пользователи
без филиалов видят всё (back-compat).

Требует Фазу 1 (foodcost/branch_scope.py). Идемпотентно.
Запуск из корня проекта: python apply_branch_phase2_shifts.py
"""
import ast
import pathlib
import sys

EMP = "foodcost/views_employees.py"
SH = "foodcost/shift_views.py"

EDITS = {
    EMP: [
        ("from .views import get_country, require_section_access\n",
         "from .views import get_country, require_section_access\n"
         "from .branch_scope import scope_by_location, user_location_ids\n"),

        ("    if emp_id:\n        qs = qs.filter(employee_id=emp_id)\n\n    rows = []",
         "    if emp_id:\n        qs = qs.filter(employee_id=emp_id)\n\n"
         "    qs = scope_by_location(qs, request.user)  # филиалы пользователя\n\n    rows = []"),

        ('    return render(request, "foodcost/shifts.html", {',
         "    _loc_ids = user_location_ids(request.user)\n"
         "    _locs = Location.objects.filter(country=country, is_active=True).order_by(\"name\")\n"
         "    _emps = Employee.objects.filter(country=country, is_active=True).order_by(\"name\")\n"
         "    if _loc_ids is not None:\n"
         "        _locs = _locs.filter(id__in=_loc_ids)\n"
         "        _emps = _emps.filter(location_id__in=_loc_ids)\n\n"
         '    return render(request, "foodcost/shifts.html", {'),

        ('        "locations": Location.objects.filter(country=country, is_active=True).order_by("name"),\n'
         '        "employees": Employee.objects.filter(country=country, is_active=True).order_by("name"),\n'
         "    })",
         '        "locations": _locs,\n'
         '        "employees": _emps,\n'
         "    })"),
    ],
    SH: [
        ("from .views import (\n    get_country,\n    require_section_access,\n)",
         "from .views import (\n    get_country,\n    require_section_access,\n)\n"
         "from .branch_scope import scope_by_location, user_location_ids"),

        ("    handovers = ShiftHandover.objects.filter(\n        country=country\n    )\n\n    now = timezone.now()",
         "    handovers = ShiftHandover.objects.filter(\n        country=country\n    )\n\n"
         "    handovers = scope_by_location(handovers, request.user)  # филиалы пользователя\n\n"
         "    now = timezone.now()"),

        ("    handovers = ShiftHandover.objects.filter(\n\n        country=country\n\n    )\n\n    if location_id:",
         "    handovers = ShiftHandover.objects.filter(\n\n        country=country\n\n    )\n\n"
         "    handovers = scope_by_location(handovers, request.user)  # филиалы пользователя\n\n"
         "    if location_id:"),

        ("    locations = Location.objects.filter(\n\n        country=country\n\n    ).order_by(\"name\")",
         "    locations = Location.objects.filter(\n\n        country=country\n\n    ).order_by(\"name\")\n\n"
         "    _loc_ids = user_location_ids(request.user)\n"
         "    if _loc_ids is not None:\n"
         "        locations = locations.filter(id__in=_loc_ids)"),
    ],
}


def patch(path, edits):
    p = pathlib.Path(path)
    if not p.exists():
        print(f"! не найден {path} — запусти из корня проекта"); sys.exit(1)
    s = p.read_text(encoding="utf-8")
    changed = 0
    for anchor, repl in edits:
        if repl in s:
            continue  # уже применено
        n = s.count(anchor)
        if n != 1:
            print(f"! {path}: якорь найден {n} раз (ожидалось 1) — не меняю, покажи Клоду\n---\n{anchor[:120]}\n---")
            sys.exit(1)
        s = s.replace(anchor, repl, 1)
        changed += 1
    if changed:
        ast.parse(s)
        p.write_text(s, encoding="utf-8")
        print(f"✓ {path}: применено изменений — {changed}")
    else:
        print(f"~ {path}: уже применено")


def main():
    # проверим, что Фаза 1 на месте
    if not pathlib.Path("foodcost/branch_scope.py").exists():
        print("! нет foodcost/branch_scope.py — сначала примени Фазу 1 (apply_branch_phase1.py)")
        sys.exit(1)
    patch(EMP, EDITS[EMP])
    patch(SH, EDITS[SH])
    print("готово — журнал смен и передача смены ограничены по филиалам")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e)
        sys.exit(1)
