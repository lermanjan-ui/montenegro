# -*- coding: utf-8 -*-
"""
Фаза 2 (ЗАКАЗЫ/КАССА, ядро): ограничение по филиалам — order_views.py.
«Филиал» = Order.location. Правило: видимость = назначенные филиалы
пользователя (user_location_ids): кассир одного филиала видит свой;
кассир «на все»/без ограничения и супер-админ — все.

Что меняется (НЕ трогаем клиентов и аналитику — они отдельными заходами):
  • order_list      — заказы за сегодня + сводка по филиалам + KPI → свои филиалы;
  • orders_count    — счётчик новых заказов (звук) → тот же срез;
  • order_all_list  — список и фильтр-локации → свои филиалы (супер-админ — всё);
  • order_create    — выпадашка локаций сужена; на сервере запрет оформить
                      заказ на чужой филиал;
  • order_detail    — доступ к чужому заказу закрыт (redirect); выпадашка
                      локаций сужена; тот же запрет чужого филиала при сохранении;
  • lunch_orders_list — список обедов и фильтр-локации → свои филиалы.

Требует Фазу 1 (branch_scope.py) и helper scope_locations (есть в складских
патчах; скрипт допишет, если нет). Миграций нет. Идемпотентно.
Запуск из корня проекта: python apply_branch_phase2_orders.py
"""
import ast
import pathlib
import sys

O = "foodcost/order_views.py"
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

# (anchor, replacement, expected_count)
EDITS = [
    (IMPORT_OLD, IMPORT_NEW, 1),

    # --- order_list: заказы за сегодня ---
    ('    orders = (\n'
     '        Order.objects\n'
     '        .filter(\n'
     '            country=country,\n'
     '            order_date__date=today\n'
     '        )\n'
     '        .select_related(\n'
     '            "location",\n'
     '            "payment_method",\n'
     '            "source",\n'
     '        )\n'
     '        .order_by("-created_at")\n'
     '    )\n',
     '    orders = (\n'
     '        Order.objects\n'
     '        .filter(\n'
     '            country=country,\n'
     '            order_date__date=today\n'
     '        )\n'
     '        .select_related(\n'
     '            "location",\n'
     '            "payment_method",\n'
     '            "source",\n'
     '        )\n'
     '        .order_by("-created_at")\n'
     '    )\n'
     '    orders = scope_by_location(orders, request.user, field="location")  # филиалы пользователя\n',
     1),

    # --- order_list: сводка по филиалам ---
    ('    locations_summary = []\n\n'
     '    locations = Location.objects.filter(\n'
     '        country=country,\n'
     '        is_active=True\n'
     '    ).order_by("name")',
     '    locations_summary = []\n\n'
     '    locations = scope_locations(Location.objects.filter(\n'
     '        country=country,\n'
     '        is_active=True\n'
     '    ).order_by("name"), request.user)',
     1),

    # --- orders_count ---
    ('    today = timezone.localdate()\n'
     '    qs = Order.objects.filter(country=country, order_date__date=today)',
     '    today = timezone.localdate()\n'
     '    qs = Order.objects.filter(country=country, order_date__date=today)\n'
     '    qs = scope_by_location(qs, request.user, field="location")  # филиалы пользователя',
     1),

    # --- order_all_list: базовый queryset ---
    ('    orders = (\n'
     '        Order.objects\n'
     '        .filter(country=country)\n'
     '        .select_related(\n'
     '            "location",\n'
     '            "payment_method",\n'
     '            "source",\n'
     '        )\n'
     '        .order_by("-order_date")\n'
     '    )\n',
     '    orders = (\n'
     '        Order.objects\n'
     '        .filter(country=country)\n'
     '        .select_related(\n'
     '            "location",\n'
     '            "payment_method",\n'
     '            "source",\n'
     '        )\n'
     '        .order_by("-order_date")\n'
     '    )\n'
     '    orders = scope_by_location(orders, request.user, field="location")  # филиалы пользователя\n',
     1),

    # --- order_all_list: фильтр-локации ---
    ('    locations = Location.objects.filter(\n'
     '        country=country,\n'
     '        is_active=True\n'
     '    ).order_by("name")\n\n'
     '    return render(\n'
     '        request,\n'
     '        "foodcost/order_all_list.html",',
     '    locations = scope_locations(Location.objects.filter(\n'
     '        country=country,\n'
     '        is_active=True\n'
     '    ).order_by("name"), request.user)\n\n'
     '    return render(\n'
     '        request,\n'
     '        "foodcost/order_all_list.html",',
     1),

    # --- order_create И order_detail: запрет чужого филиала при сохранении (2 места) ---
    ('        if request.POST.get("location_id"):\n'
     '            location = get_object_or_404(\n'
     '                Location,\n'
     '                id=request.POST.get("location_id"),\n'
     '                country=country\n'
     '            )\n',
     '        if request.POST.get("location_id"):\n'
     '            location = get_object_or_404(\n'
     '                Location,\n'
     '                id=request.POST.get("location_id"),\n'
     '                country=country\n'
     '            )\n'
     '            _ids = user_location_ids(request.user)\n'
     '            if _ids is not None and location.id not in _ids:\n'
     '                return HttpResponseForbidden("Нельзя оформлять заказ на чужой филиал")\n',
     2),

    # --- order_create: выпадашка локаций ---
    ('    return render(request, "foodcost/order_create.html", {\n'
     '        "country": country,\n'
     '        "dishes": dishes,\n'
     '        "locations": locations,',
     '    return render(request, "foodcost/order_create.html", {\n'
     '        "country": country,\n'
     '        "dishes": dishes,\n'
     '        "locations": scope_locations(locations, request.user),',
     1),

    # --- order_detail: доступ к чужому заказу закрыт ---
    ('    order = get_object_or_404(\n'
     '        Order.objects.prefetch_related("items__dish"),\n'
     '        id=order_id,\n'
     '        country=country\n'
     '    )\n',
     '    order = get_object_or_404(\n'
     '        Order.objects.prefetch_related("items__dish"),\n'
     '        id=order_id,\n'
     '        country=country\n'
     '    )\n'
     '    _ids = user_location_ids(request.user)  # None = супер-админ / все филиалы\n'
     '    if _ids is not None and order.location_id not in _ids and order.created_by_id != request.user.id:\n'
     '        return redirect(f"/c/{country.slug}/orders/")\n',
     1),

    # --- order_detail: выпадашка локаций ---
    ('    return render(request, "foodcost/order_detail.html", {\n'
     '        "country": country,\n'
     '        "order": order,\n\n'
     '        "dishes": dishes,\n'
     '        "locations": locations,',
     '    return render(request, "foodcost/order_detail.html", {\n'
     '        "country": country,\n'
     '        "order": order,\n\n'
     '        "dishes": dishes,\n'
     '        "locations": scope_locations(locations, request.user),',
     1),

    # --- lunch_orders_list: базовый queryset ---
    ('    orders = (\n'
     '        Order.objects\n'
     '        .filter(country=country, lunch_combos__isnull=False)\n'
     '        .select_related("location", "payment_method", "source")\n'
     '        .prefetch_related("lunch_combos")\n'
     '        .order_by("-order_date")\n'
     '        .distinct()\n'
     '    )\n',
     '    orders = (\n'
     '        Order.objects\n'
     '        .filter(country=country, lunch_combos__isnull=False)\n'
     '        .select_related("location", "payment_method", "source")\n'
     '        .prefetch_related("lunch_combos")\n'
     '        .order_by("-order_date")\n'
     '        .distinct()\n'
     '    )\n'
     '    orders = scope_by_location(orders, request.user, field="location")  # филиалы пользователя\n',
     1),

    # --- lunch_orders_list: фильтр-локации ---
    ('    locations = Location.objects.filter(\n'
     '        country=country,\n'
     '        is_active=True\n'
     '    ).order_by("name")\n\n'
     '    return render(\n'
     '        request,\n'
     '        "foodcost/lunch_orders_list.html",',
     '    locations = scope_locations(Location.objects.filter(\n'
     '        country=country,\n'
     '        is_active=True\n'
     '    ).order_by("name"), request.user)\n\n'
     '    return render(\n'
     '        request,\n'
     '        "foodcost/lunch_orders_list.html",',
     1),
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
    p = pathlib.Path(O)
    if not p.exists():
        print(f"! не найден {O} — запусти из корня проекта"); sys.exit(1)
    s = p.read_text(encoding="utf-8")
    changed = 0
    for anchor, repl, expected in EDITS:
        if repl in s:
            continue  # уже применено (repl включает anchor + добавленные строки)
        n = s.count(anchor)
        if n != expected:
            print(f"! якорь найден {n} раз (ожидалось {expected}), не меняю:\n---\n{anchor[:110]}\n---")
            sys.exit(1)
        s = s.replace(anchor, repl)  # все вхождения (для expected>1)
        changed += expected
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
