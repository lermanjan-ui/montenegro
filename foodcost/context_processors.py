from .models import Country, UserProfile


def user_has_section(user, section):
    if not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    if not hasattr(user, "profile"):
        return False

    return user.profile.can_access_section(section)


def _can_view_techcards(user):
    """Кто может смотреть техкарты: те же, кто видит карточку блюда
    (логика как в views.user_can_view_dish_page) — включая кухонный персонал."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, "profile", None)
    if profile is None:
        return False
    return (
        profile.can_access_section(UserProfile.SECTION_DISHES)
        or profile.can_access_section(UserProfile.SECTION_ORDERS)
        or profile.can_access_section(UserProfile.SECTION_ALL_ORDERS)
        or profile.can_access_section(UserProfile.SECTION_SHIFT_HANDOVER)
        or profile.is_kitchen_staff()
        or profile.can_access_section(UserProfile.SECTION_TECHCARDS)
    )


def menu_context(request):
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
    }