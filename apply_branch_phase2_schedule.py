# -*- coding: utf-8 -*-
"""
Фаза 2 (смены, хвост): ГРАФИК СМЕН (schedule_page) — ограничение по филиалам.
Сужает по филиалам пользователя: список филиалов, сотрудников в попапе,
смены в недельном и месячном видах. Супер-админ и без филиалов — всё.

Требует Фазу 1 (branch_scope.py). Идемпотентно и безопасно (только чтение).
Запуск из корня проекта: python apply_branch_phase2_schedule.py
"""
import ast
import pathlib
import sys

EMP = "foodcost/views_employees.py"

IMPORT_LINE = "from .branch_scope import scope_by_location, user_location_ids"
IMPORT_ANCHOR = "from .views import get_country, require_section_access\n"

EDITS = [
    # A — список филиалов для фильтра графика
    ('    locations = Location.objects.filter(country=country, is_active=True).order_by("name")\n\n    common = {',
     '    locations = Location.objects.filter(country=country, is_active=True).order_by("name")\n'
     '    _loc_ids = user_location_ids(request.user)\n'
     '    if _loc_ids is not None:\n'
     '        locations = locations.filter(id__in=_loc_ids)\n\n    common = {'),

    # B — сотрудники в попапе добавления смены
    ('    popup_employees = list(\n'
     '        Employee.objects.filter(country=country, is_active=True).order_by("position", "name")\n'
     '    )',
     '    _pe_qs = Employee.objects.filter(country=country, is_active=True).order_by("position", "name")\n'
     '    _pe_qs = scope_by_location(_pe_qs, request.user)\n'
     '    popup_employees = list(_pe_qs)'),

    # C — недельный вид: сотрудники
    ('        emp_qs = Employee.objects.filter(country=country, is_active=True).select_related("location")\n'
     '        if location_id:\n'
     '            emp_qs = emp_qs.filter(location_id=location_id)',
     '        emp_qs = Employee.objects.filter(country=country, is_active=True).select_related("location")\n'
     '        if location_id:\n'
     '            emp_qs = emp_qs.filter(location_id=location_id)\n'
     '        emp_qs = scope_by_location(emp_qs, request.user)'),

    # D — недельный вид: смены
    ('        shift_qs = EmployeeShift.objects.filter(\n'
     '            country=country, shift_date__gte=monday, shift_date__lte=week_end\n'
     '        ).exclude(status=EmployeeShift.STATUS_CANCELLED).select_related("location", "employee")\n'
     '        if location_id:\n'
     '            shift_qs = shift_qs.filter(location_id=location_id)',
     '        shift_qs = EmployeeShift.objects.filter(\n'
     '            country=country, shift_date__gte=monday, shift_date__lte=week_end\n'
     '        ).exclude(status=EmployeeShift.STATUS_CANCELLED).select_related("location", "employee")\n'
     '        if location_id:\n'
     '            shift_qs = shift_qs.filter(location_id=location_id)\n'
     '        shift_qs = scope_by_location(shift_qs, request.user)'),

    # E — месячный вид: смены
    ('    shift_qs = EmployeeShift.objects.filter(\n'
     '        country=country, shift_date__gte=grid_start, shift_date__lte=grid_end\n'
     '    ).exclude(status=EmployeeShift.STATUS_CANCELLED).select_related("location", "employee").order_by("start_time", "id")\n'
     '    if location_id:\n'
     '        shift_qs = shift_qs.filter(location_id=location_id)',
     '    shift_qs = EmployeeShift.objects.filter(\n'
     '        country=country, shift_date__gte=grid_start, shift_date__lte=grid_end\n'
     '    ).exclude(status=EmployeeShift.STATUS_CANCELLED).select_related("location", "employee").order_by("start_time", "id")\n'
     '    if location_id:\n'
     '        shift_qs = shift_qs.filter(location_id=location_id)\n'
     '    shift_qs = scope_by_location(shift_qs, request.user)'),
]


def main():
    if not pathlib.Path("foodcost/branch_scope.py").exists():
        print("! нет foodcost/branch_scope.py — сначала примени Фазу 1"); sys.exit(1)
    p = pathlib.Path(EMP)
    if not p.exists():
        print(f"! не найден {EMP} — запусти из корня проекта"); sys.exit(1)
    s = p.read_text(encoding="utf-8")

    # гарантируем импорт (Фаза 2 смен его уже добавляла, но на всякий случай)
    if IMPORT_LINE not in s:
        if s.count(IMPORT_ANCHOR) != 1:
            print("! не нашёл куда вставить импорт — покажи Клоду"); sys.exit(1)
        s = s.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + IMPORT_LINE + "\n", 1)

    changed = 0
    for anchor, repl in EDITS:
        if repl in s:
            continue
        n = s.count(anchor)
        if n != 1:
            print(f"! якорь найден {n} раз (ожидалось 1) — не меняю:\n---\n{anchor[:100]}\n---"); sys.exit(1)
        s = s.replace(anchor, repl, 1)
        changed += 1

    ast.parse(s)
    p.write_text(s, encoding="utf-8")
    print(f"✓ {EMP}: график смен ограничен по филиалам (изменений: {changed})"
          if changed else f"~ {EMP}: уже применено")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e); sys.exit(1)
