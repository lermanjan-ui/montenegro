from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect

from .models import UserProfile, Location, Dish
from .views import get_country, require_section_access


def _dec_or_none(raw):
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


@login_required(login_url="/login/")
def uzum_settings(request, country_slug):
    """Настройки интеграции Uzum: какие точки отдаём в Uzum, цена/стоп блюд."""
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_SETTINGS)
    if access_error:
        return access_error

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "save_locations":
            enabled_ids = set(request.POST.getlist("uzum_enabled"))
            for loc in Location.objects.filter(country=country):
                want = str(loc.id) in enabled_ids
                if loc.uzum_enabled != want:
                    loc.uzum_enabled = want
                    loc.save(update_fields=["uzum_enabled"])

        elif action == "save_dish":
            dish = Dish.objects.filter(
                id=request.POST.get("dish_id"), country=country
            ).first()
            if dish:
                dish.uzum_price = _dec_or_none(request.POST.get("uzum_price"))
                dish.uzum_stop = bool(request.POST.get("uzum_stop"))
                dish.mxik_code = (request.POST.get("mxik_code") or "").strip()[:32]
                dish.package_code = (request.POST.get("package_code") or "").strip()[:32]
                dish.save(update_fields=[
                    "uzum_price", "uzum_stop", "mxik_code", "package_code",
                ])

        return redirect(f"/c/{country.slug}/uzum/")

    locations = Location.objects.filter(country=country).order_by("name")
    dishes = (
        Dish.objects.filter(country=country, is_archived=False)
        .select_related("category")
        .order_by("name")
    )

    return render(request, "foodcost/uzum_settings.html", {
        "country": country,
        "locations": locations,
        "dishes": dishes,
    })
