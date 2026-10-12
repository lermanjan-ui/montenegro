# -*- coding: utf-8 -*-
"""
Фаза 2 (СПИСАНИЯ): ограничение по филиалам.
  • writeoff_views.writeoff_list      — список списаний + выпадашка складов
    сужаются до филиалов пользователя;
  • writeoff_views.writeoff_analytics — аналитика считается только по филиалам
    пользователя (фильтр базового queryset → все агрегаты автоматически).
Создание не трогаем: склад повару и так подставляется из профиля
(_resolve_writeoff_location), а выпадашка складов теперь сужена.

Требует Фазу 1 (branch_scope.py). Идемпотентно, только чтение.
Запуск из корня проекта: python apply_branch_phase2_writeoffs.py
"""
import ast
import pathlib
import sys

W = "foodcost/writeoff_views.py"

EDITS = [
    ("from .views import (\n    get_country,\n    require_section_access,\n)",
     "from .views import (\n    get_country,\n    require_section_access,\n)\n"
     "from .branch_scope import scope_by_location, user_location_ids"),

    # список списаний — фильтр по филиалам
    ('    writeoffs = WriteOff.objects.filter(\n'
     '        country=country,\n'
     '        writeoff_date__gte=date_from,\n'
     '    ).order_by("-writeoff_date", "-created_at")\n\n'
     '    products = Product.objects.filter(country=country).order_by("name")',
     '    writeoffs = WriteOff.objects.filter(\n'
     '        country=country,\n'
     '        writeoff_date__gte=date_from,\n'
     '    ).order_by("-writeoff_date", "-created_at")\n'
     '    writeoffs = scope_by_location(writeoffs, request.user)  # филиалы пользователя\n\n'
     '    products = Product.objects.filter(country=country).order_by("name")'),

    # выпадашка складов — только филиалы пользователя
    ('    locations = Location.objects.filter(country=country).order_by("name")\n'
     '    is_kitchen = request.user.profile.is_kitchen_staff()',
     '    locations = Location.objects.filter(country=country).order_by("name")\n'
     '    _loc_ids = user_location_ids(request.user)\n'
     '    if _loc_ids is not None:\n'
     '        locations = locations.filter(id__in=_loc_ids)\n'
     '    is_kitchen = request.user.profile.is_kitchen_staff()'),

    # аналитика — базовый queryset по филиалам (все агрегаты наследуют)
    ('    writeoffs = WriteOff.objects.filter(\n'
     '        country=country,\n'
     '        writeoff_date__gte=date_from,\n'
     '        writeoff_date__lte=date_to,\n'
     '    )\n\n'
     '    if item_type:',
     '    writeoffs = WriteOff.objects.filter(\n'
     '        country=country,\n'
     '        writeoff_date__gte=date_from,\n'
     '        writeoff_date__lte=date_to,\n'
     '    )\n'
     '    writeoffs = scope_by_location(writeoffs, request.user)  # филиалы пользователя\n\n'
     '    if item_type:'),
]


def main():
    if not pathlib.Path("foodcost/branch_scope.py").exists():
        print("! нет foodcost/branch_scope.py — сначала примени Фазу 1"); sys.exit(1)
    p = pathlib.Path(W)
    if not p.exists():
        print(f"! не найден {W} — запусти из корня проекта"); sys.exit(1)
    s = p.read_text(encoding="utf-8")
    changed = 0
    for anchor, repl in EDITS:
        if repl in s:
            continue
        n = s.count(anchor)
        if n != 1:
            print(f"! якорь найден {n} раз (ожидалось 1) — не меняю:\n---\n{anchor[:90]}\n---"); sys.exit(1)
        s = s.replace(anchor, repl, 1)
        changed += 1
    if changed:
        ast.parse(s)
        p.write_text(s, encoding="utf-8")
        print(f"✓ {W}: списания ограничены по филиалам (изменений: {changed})")
    else:
        print(f"~ {W}: уже применено")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e); sys.exit(1)
