from .models import Country


def menu_context(request):
    if not request.user.is_authenticated:
        return {
            "can_switch_country": False,
            "can_manage_users": False,
        }

    if request.user.is_superuser:
        countries_count = Country.objects.count()
        can_manage_users = True

    elif hasattr(request.user, "profile"):
        countries_count = request.user.profile.countries.count()
        can_manage_users = request.user.profile.is_super_admin()

    else:
        countries_count = 0
        can_manage_users = False

    return {
        "can_switch_country": countries_count > 1,
        "can_manage_users": can_manage_users,
    }