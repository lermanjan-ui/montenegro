from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect

from .models import UserProfile, Location
from .views import get_country, require_section_access


@login_required(login_url="/login/")
def uzum_settings(request, country_slug):
    """Страница «Локации»: видимость филиалов (активна / на сайте) и отдача в Uzum.

    Управление филиалами (создание/адрес/удаление) и доступ пользователей —
    по-прежнему на странице «Пользователи». Здесь — быстрые переключатели
    видимости и интеграции Uzum по всем точкам в одном месте.
    """
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_SETTINGS)
    if access_error:
        return access_error

    if request.method == "POST" and request.POST.get("action") == "save_locations":
        enabled = set(request.POST.getlist("uzum_enabled"))
        active = set(request.POST.getlist("is_active"))
        visible = set(request.POST.getlist("is_visible_on_site"))
        for loc in Location.objects.filter(country=country):
            sid = str(loc.id)
            loc.uzum_enabled = sid in enabled
            loc.is_active = sid in active
            loc.is_visible_on_site = sid in visible
            loc.save(update_fields=["uzum_enabled", "is_active", "is_visible_on_site"])
        return redirect(f"/c/{country.slug}/uzum/")

    locations = Location.objects.filter(country=country).order_by("site_sort_order", "name")

    return render(request, "foodcost/uzum_settings.html", {
        "country": country,
        "locations": locations,
    })
