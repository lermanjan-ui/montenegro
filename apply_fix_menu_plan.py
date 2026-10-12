# -*- coding: utf-8 -*-
"""
Фикс test_tenant_guard.MenuContextTests.test_menu_hides_unpaid_module.
Причина: menu_context гейтит пункты меню только по правам пользователя и НЕ
пересекает с модулями тарифного плана. Middleware (tenant_guard) пересекает —
меню должно так же.

Правка: пункты МОДУЛЬНЫХ разделов (из tenant_guard._SEGMENT_SECTION) в меню
скрываются, если модуль не входит в план подписки. Служебные разделы
(settings/users/…), супер-админ и компании без подписки — без ограничений
(tenant_has_module возвращает True). Логика один-в-один с middleware.

Трогается только foodcost/context_processors.py (функция menu_context).
Идемпотентно. Запуск из корня проекта: python apply_fix_menu_plan.py
"""
import ast
import pathlib
import re
import sys

C = "foodcost/context_processors.py"

OLD = '''def menu_context(request):
    if not request.user.is_authenticated:
        return {}

    if request.user.is_superuser:
        countries_count = Country.objects.count()
    elif hasattr(request.user, "profile"):
        countries_count = request.user.profile.countries.count()
    else:
        countries_count = 0

    is_employee = hasattr(request.user, "employee_profile")

    return {
        "can_switch_country": countries_count > 1,
        "is_employee": is_employee,

        "can_menu_dishes": user_has_section(request.user, UserProfile.SECTION_DISHES),
        "can_view_techcards": _can_view_techcards(request.user),
        "can_menu_products": user_has_section(request.user, UserProfile.SECTION_PRODUCTS),
        "can_menu_preparations": user_has_section(request.user, UserProfile.SECTION_PREPARATIONS),
        "can_menu_employees": user_has_section(request.user, UserProfile.SECTION_EMPLOYEES),
        "can_menu_packaging": user_has_section(request.user, UserProfile.SECTION_PACKAGING),
        "can_menu_utilities": user_has_section(request.user, UserProfile.SECTION_UTILITIES),
        "can_menu_users": user_has_section(request.user, UserProfile.SECTION_USERS),
        "can_menu_writeoffs": user_has_section(request.user, UserProfile.SECTION_WRITE_OFFS),
        "can_menu_writeoff_analytics": user_has_section(request.user, UserProfile.SECTION_WRITE_OFF_ANALYTICS),
        "can_menu_shift_handover": user_has_section(request.user, UserProfile.SECTION_SHIFT_HANDOVER),
        "can_menu_orders": user_has_section(request.user, UserProfile.SECTION_ORDERS),
        "can_edit_orders": request.user.is_superuser or (
            hasattr(request.user, "profile") and request.user.profile.can_edit()
        ),
        "can_menu_settings": user_has_section(request.user, UserProfile.SECTION_SETTINGS),

        "can_menu_customers": user_has_section(request.user, UserProfile.SECTION_CUSTOMERS),
        "can_menu_order_analytics": user_has_section(request.user, UserProfile.SECTION_ORDER_ANALYTICS),
        "can_menu_all_orders": user_has_section(request.user, UserProfile.SECTION_ALL_ORDERS),
        "can_menu_shift_handover_admin": user_has_section(request.user, UserProfile.SECTION_SHIFT_HANDOVER_ADMIN),
        "can_menu_purchases": user_has_section(request.user, UserProfile.SECTION_PURCHASES),
        "can_menu_suppliers": user_has_section(request.user, UserProfile.SECTION_SUPPLIERS),
        "can_menu_inventory": user_has_section(request.user, UserProfile.SECTION_INVENTORY),
        "can_menu_stock": user_has_section(request.user, UserProfile.SECTION_STOCK),
        "can_menu_transfers": user_has_section(request.user, UserProfile.SECTION_TRANSFERS),
        "can_menu_schedule": user_has_section(request.user, UserProfile.SECTION_SCHEDULE),
        "can_menu_shifts": user_has_section(request.user, UserProfile.SECTION_SHIFTS),
        "can_menu_finance": user_has_section(request.user, UserProfile.SECTION_FINANCE),
    }'''

NEW = '''def menu_context(request):
    if not request.user.is_authenticated:
        return {}

    user = request.user

    if user.is_superuser:
        countries_count = Country.objects.count()
    elif hasattr(user, "profile"):
        countries_count = user.profile.countries.count()
    else:
        countries_count = 0

    is_employee = hasattr(user, "employee_profile")

    # Тариф «вглубь» в меню: модульный раздел скрыт, если модуль не входит в
    # план подписки — та же логика, что в middleware tenant_guard. Служебные
    # разделы (не в _SEGMENT_SECTION), супер-админ и компании без подписки
    # (tenant_has_module → True) ограничений не имеют.
    from .tenant_guard import current_country, _SEGMENT_SECTION
    from .module_access import tenant_has_module

    _gated = set(_SEGMENT_SECTION.values())
    _country = None if user.is_superuser else current_country(request)

    def _plan_allows(section):
        if user.is_superuser or section not in _gated or _country is None:
            return True
        return tenant_has_module(_country, section)

    def can_menu(section):
        return user_has_section(user, section) and _plan_allows(section)

    return {
        "can_switch_country": countries_count > 1,
        "is_employee": is_employee,

        "can_menu_dishes": can_menu(UserProfile.SECTION_DISHES),
        "can_view_techcards": _can_view_techcards(user),
        "can_menu_products": can_menu(UserProfile.SECTION_PRODUCTS),
        "can_menu_preparations": can_menu(UserProfile.SECTION_PREPARATIONS),
        "can_menu_employees": can_menu(UserProfile.SECTION_EMPLOYEES),
        "can_menu_packaging": can_menu(UserProfile.SECTION_PACKAGING),
        "can_menu_utilities": can_menu(UserProfile.SECTION_UTILITIES),
        "can_menu_users": can_menu(UserProfile.SECTION_USERS),
        "can_menu_writeoffs": can_menu(UserProfile.SECTION_WRITE_OFFS),
        "can_menu_writeoff_analytics": can_menu(UserProfile.SECTION_WRITE_OFF_ANALYTICS),
        "can_menu_shift_handover": can_menu(UserProfile.SECTION_SHIFT_HANDOVER),
        "can_menu_orders": can_menu(UserProfile.SECTION_ORDERS),
        "can_edit_orders": user.is_superuser or (
            hasattr(user, "profile") and user.profile.can_edit()
        ),
        "can_menu_settings": can_menu(UserProfile.SECTION_SETTINGS),

        "can_menu_customers": can_menu(UserProfile.SECTION_CUSTOMERS),
        "can_menu_order_analytics": can_menu(UserProfile.SECTION_ORDER_ANALYTICS),
        "can_menu_all_orders": can_menu(UserProfile.SECTION_ALL_ORDERS),
        "can_menu_shift_handover_admin": can_menu(UserProfile.SECTION_SHIFT_HANDOVER_ADMIN),
        "can_menu_purchases": can_menu(UserProfile.SECTION_PURCHASES),
        "can_menu_suppliers": can_menu(UserProfile.SECTION_SUPPLIERS),
        "can_menu_inventory": can_menu(UserProfile.SECTION_INVENTORY),
        "can_menu_stock": can_menu(UserProfile.SECTION_STOCK),
        "can_menu_transfers": can_menu(UserProfile.SECTION_TRANSFERS),
        "can_menu_schedule": can_menu(UserProfile.SECTION_SCHEDULE),
        "can_menu_shifts": can_menu(UserProfile.SECTION_SHIFTS),
        "can_menu_finance": can_menu(UserProfile.SECTION_FINANCE),
    }'''


def main():
    p = pathlib.Path(C)
    if not p.exists():
        print(f"! не найден {C} — запусти из корня проекта"); sys.exit(1)
    s = p.read_text(encoding="utf-8")
    if "def can_menu(section):" in s:
        print(f"~ {C}: уже применено"); return
    # Функция menu_context: от сигнатуры до закрывающей dict-скобки «    }»
    # (нечувствительно к внутренним пробелам — поэтому regex, а не точный якорь).
    pat = re.compile(r"def menu_context\(request\):.*?\n    \}", re.DOTALL)
    found = pat.findall(s)
    if len(found) != 1:
        print(f"! menu_context найден {len(found)} раз (ожидалось 1) — не меняю"); sys.exit(1)
    s = pat.sub(lambda _m: NEW, s, count=1)
    ast.parse(s)
    p.write_text(s, encoding="utf-8")
    print(f"✓ {C}: меню теперь учитывает тариф (скрывает неоплаченные модули)")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e); sys.exit(1)
