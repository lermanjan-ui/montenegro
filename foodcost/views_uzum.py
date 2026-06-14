from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from decimal import Decimal

from .models import UserProfile, Location, DeliveryZone
from .views import (
    get_country,
    require_section_access,
    clean_decimal,
    _parse_optional_decimal,
)


@login_required(login_url="/login/")
def uzum_settings(request, country_slug):
    """Страница «Локации»: филиалы (адрес/часы/координаты/доставка/видимость/Uzum)
    и зоны доставки. Перенесено со страницы «Пользователи»."""
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_SETTINGS)
    if access_error:
        return access_error

    error = None

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "save_locations":
            enabled = set(request.POST.getlist("uzum_enabled"))
            active = set(request.POST.getlist("is_active"))
            visible = set(request.POST.getlist("is_visible_on_site"))
            for loc in Location.objects.filter(country=country):
                sid = str(loc.id)
                loc.uzum_enabled = sid in enabled
                loc.is_active = sid in active
                loc.is_visible_on_site = sid in visible
                loc.save(update_fields=[
                    "uzum_enabled", "is_active", "is_visible_on_site",
                ])
            return redirect(f"/c/{country.slug}/uzum/")

        if action == "create_location":
            location_name = (request.POST.get("location_name") or "").strip()
            if not location_name:
                error = "Укажи название филиала"
            else:
                tg_raw = (request.POST.get("telegram_thread_id") or "").strip()
                try:
                    tg_thread_id = int(tg_raw) if tg_raw else None
                except (TypeError, ValueError):
                    tg_thread_id = None
                try:
                    sort_order = int(request.POST.get("site_sort_order") or 0)
                except (TypeError, ValueError):
                    sort_order = 0
                if sort_order < 0:
                    sort_order = 0
                Location.objects.create(
                    country=country,
                    name=location_name,
                    telegram_thread_id=tg_thread_id,
                    public_name=(request.POST.get("public_name") or "").strip(),
                    address=(request.POST.get("address") or "").strip(),
                    phone=(request.POST.get("phone") or "").strip(),
                    latitude=_parse_optional_decimal(request.POST.get("latitude")),
                    longitude=_parse_optional_decimal(request.POST.get("longitude")),
                    working_hours=(request.POST.get("working_hours") or "").strip(),
                    site_sort_order=sort_order,
                    is_active=bool(request.POST.get("is_active")),
                    is_visible_on_site=bool(request.POST.get("is_visible_on_site")),
                    supports_pickup=bool(request.POST.get("supports_pickup")),
                    supports_delivery=bool(request.POST.get("supports_delivery")),
                    uzum_enabled=bool(request.POST.get("uzum_enabled")),
                )
                return redirect(f"/c/{country.slug}/uzum/")

        if action == "update_location":
            item = get_object_or_404(
                Location, id=request.POST.get("location_id"), country=country,
            )
            new_name = (request.POST.get("location_name") or "").strip()
            if not new_name:
                error = "Название филиала не может быть пустым"
            else:
                item.name = new_name
                tg_raw = (request.POST.get("telegram_thread_id") or "").strip()
                try:
                    item.telegram_thread_id = int(tg_raw) if tg_raw else None
                except (TypeError, ValueError):
                    item.telegram_thread_id = None
                item.public_name = (request.POST.get("public_name") or "").strip()
                item.address = (request.POST.get("address") or "").strip()
                item.phone = (request.POST.get("phone") or "").strip()
                item.latitude = _parse_optional_decimal(request.POST.get("latitude"))
                item.longitude = _parse_optional_decimal(request.POST.get("longitude"))
                item.working_hours = (request.POST.get("working_hours") or "").strip()
                try:
                    sort_order = int(request.POST.get("site_sort_order") or 0)
                except (TypeError, ValueError):
                    sort_order = 0
                if sort_order < 0:
                    sort_order = 0
                item.site_sort_order = sort_order
                item.is_active = bool(request.POST.get("is_active"))
                item.is_visible_on_site = bool(request.POST.get("is_visible_on_site"))
                item.supports_pickup = bool(request.POST.get("supports_pickup"))
                item.supports_delivery = bool(request.POST.get("supports_delivery"))
                item.uzum_enabled = bool(request.POST.get("uzum_enabled"))
                item.save()
                return redirect(f"/c/{country.slug}/uzum/")

        if action == "delete_location":
            item = get_object_or_404(
                Location, id=request.POST.get("location_id"), country=country,
            )
            if UserProfile.objects.filter(location=item).exists():
                error = (
                    "Нельзя удалить филиал: к нему привязаны пользователи. "
                    "Сначала отвяжите их на странице «Пользователи»."
                )
            else:
                item.delete()
                return redirect(f"/c/{country.slug}/uzum/")

        if action == "create_delivery_zone":
            zone_name = (request.POST.get("zone_name") or "").strip()
            zone_location = Location.objects.filter(
                id=request.POST.get("zone_location_id"), country=country,
            ).first()
            if not zone_name:
                error = "Укажи название зоны"
            elif zone_location is None:
                error = "Выбери филиал для зоны"
            else:
                try:
                    sort_order = int(request.POST.get("zone_sort_order") or 0)
                except (TypeError, ValueError):
                    sort_order = 0
                DeliveryZone.objects.create(
                    country=country,
                    location=zone_location,
                    name=zone_name,
                    zone_kind=(
                        request.POST.get("zone_kind")
                        if request.POST.get("zone_kind") in (
                            DeliveryZone.KIND_REGULAR, DeliveryZone.KIND_LUNCH
                        )
                        else DeliveryZone.KIND_REGULAR
                    ),
                    center_latitude=_parse_optional_decimal(request.POST.get("zone_center_latitude")),
                    center_longitude=_parse_optional_decimal(request.POST.get("zone_center_longitude")),
                    radius_km=_parse_optional_decimal(request.POST.get("zone_radius_km")),
                    delivery_price=Decimal(clean_decimal(request.POST.get("zone_delivery_price"))),
                    free_delivery_threshold=Decimal(clean_decimal(request.POST.get("zone_free_delivery_threshold"))),
                    estimated_time=(request.POST.get("zone_estimated_time") or "35–45 мин"),
                    site_sort_order=sort_order,
                    is_active=bool(request.POST.get("zone_is_active")),
                )
                return redirect(f"/c/{country.slug}/uzum/")

        if action == "update_delivery_zone":
            zone = get_object_or_404(
                DeliveryZone, id=request.POST.get("zone_id"), country=country,
            )
            new_name = (request.POST.get("zone_name") or "").strip()
            zone_location = Location.objects.filter(
                id=request.POST.get("zone_location_id"), country=country,
            ).first()
            if not new_name:
                error = "Название зоны не может быть пустым"
            elif zone_location is None:
                error = "Выбери филиал для зоны"
            else:
                try:
                    sort_order = int(request.POST.get("zone_sort_order") or 0)
                except (TypeError, ValueError):
                    sort_order = 0
                zone.name = new_name
                zone.location = zone_location
                _zk = request.POST.get("zone_kind")
                if _zk in (DeliveryZone.KIND_REGULAR, DeliveryZone.KIND_LUNCH):
                    zone.zone_kind = _zk
                zone.center_latitude = _parse_optional_decimal(request.POST.get("zone_center_latitude"))
                zone.center_longitude = _parse_optional_decimal(request.POST.get("zone_center_longitude"))
                zone.radius_km = _parse_optional_decimal(request.POST.get("zone_radius_km"))
                zone.delivery_price = Decimal(clean_decimal(request.POST.get("zone_delivery_price")))
                zone.free_delivery_threshold = Decimal(clean_decimal(request.POST.get("zone_free_delivery_threshold")))
                zone.estimated_time = (request.POST.get("zone_estimated_time") or "35–45 мин")
                zone.site_sort_order = sort_order
                zone.is_active = bool(request.POST.get("zone_is_active"))
                zone.save()
                return redirect(f"/c/{country.slug}/uzum/")

        if action == "delete_delivery_zone":
            zone = get_object_or_404(
                DeliveryZone, id=request.POST.get("zone_id"), country=country,
            )
            zone.delete()
            return redirect(f"/c/{country.slug}/uzum/")

    locations = Location.objects.filter(country=country).order_by(
        "site_sort_order", "name"
    )
    delivery_zones = (
        DeliveryZone.objects
        .filter(country=country)
        .select_related("location")
        .order_by("location__name", "site_sort_order", "name")
    )

    return render(request, "foodcost/uzum_settings.html", {
        "country": country,
        "locations": locations,
        "delivery_zones": delivery_zones,
        "error": error,
    })
