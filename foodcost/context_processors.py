from .models import Country, UserProfile


def user_has_section(user, section):
    if not user.is_authenticated:
        return False

    if user.is_superuser:
        return True

    if not hasattr(user, "profile"):
        return False

    return user.profile.can_access_section(section)


def menu_context(request):
    if not request.user.is_authenticated:
        return {}

    if request.user.is_superuser:
        countries_count = Country.objects.count()
    elif hasattr(request.user, "profile"):
        countries_count = request.user.profile.countries.count()
    else:
        countries_count = 0

    return {
        "can_switch_country": countries_count > 1,

        "can_menu_dishes": user_has_section(request.user, UserProfile.SECTION_DISHES),
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
        "can_menu_settings": user_has_section(request.user, UserProfile.SECTION_SETTINGS),
    }