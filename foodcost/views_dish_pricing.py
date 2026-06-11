from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect

from .models import UserProfile, Dish, Location, DishAvailability, OrderSource
from .views import get_country, require_section_access

YANDEX_COMMISSION = Decimal("35")          # %, фиксированная для расчёта «Выручка с Яндекса»
UZUM_COMMISSION_DEFAULT = Decimal("28")    # %, дефолт если у источника Uzum комиссия не задана


def _dec_or_none(raw):
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return None


def _pct(part, whole):
    if not whole:
        return None
    return (part / whole * Decimal(100)).quantize(Decimal("0.1"))


@login_required(login_url="/login/")
def dish_pricing(request, country_slug):
    """Цены, маржа и доступность блюд по точкам и Uzum + расчёт выручки."""
    country = get_country(country_slug, request.user)
    access_error = require_section_access(request.user, UserProfile.SECTION_SETTINGS)
    if access_error:
        return access_error

    locations = list(
        Location.objects.filter(country=country).order_by("site_sort_order", "name")
    )

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "set_uzum_source":
            OrderSource.objects.filter(country=country).update(is_uzum=False)
            sid = request.POST.get("uzum_source_id")
            if sid:
                OrderSource.objects.filter(country=country, id=sid).update(is_uzum=True)
            return redirect(f"/c/{country.slug}/dish-pricing/")

        if action == "save_dish":
            dish = Dish.objects.filter(
                id=request.POST.get("dish_id"), country=country
            ).first()
            if dish:
                sp = _dec_or_none(request.POST.get("selling_price"))
                if sp is not None:
                    dish.selling_price = sp
                dish.uzum_price = _dec_or_none(request.POST.get("uzum_price"))
                visible = bool(request.POST.get("is_visible_on_site"))
                dish.is_visible_on_site = visible
                dish.uzum_excluded = bool(request.POST.get("uzum_excluded"))
                dish.save(update_fields=[
                    "selling_price", "uzum_price", "is_visible_on_site", "uzum_excluded"
                ])
                try:
                    dish.recalculate_cache()
                except Exception:
                    pass

                avail_ids = set(request.POST.getlist("avail"))
                uzum_ok_ids = set(request.POST.getlist("uzum_ok"))
                # Правило: нет на сайте → недоступно везде (все точки + все выдачи/Uzum).
                if not visible:
                    avail_ids = set()
                    uzum_ok_ids = set()
                for loc in locations:
                    da, _ = DishAvailability.objects.get_or_create(
                        country=country, dish=dish, location=loc
                    )
                    da.is_available = str(loc.id) in avail_ids
                    da.uzum_stop = str(loc.id) not in uzum_ok_ids
                    da.save(update_fields=["is_available", "uzum_stop"])
            return redirect(f"/c/{country.slug}/dish-pricing/")

    # источник Uzum и его комиссия (дефолт 28%, если у источника не задана)
    uzum_source = (
        OrderSource.objects.filter(country=country, is_uzum=True).first()
        or OrderSource.objects.filter(country=country, name__icontains="uzum").first()
    )
    uzum_comm = (uzum_source.commission_percent if uzum_source else None) or UZUM_COMMISSION_DEFAULT

    # карта доступности по (блюдо, точка)
    av = {
        (a.dish_id, a.location_id): a
        for a in DishAvailability.objects.filter(country=country)
    }

    dishes = (
        Dish.objects.filter(country=country, is_archived=False)
        .select_related("category")
        .order_by("name")
    )

    rows = []
    for d in dishes:
        cost = d.cached_total_cost or Decimal(0)
        price = d.selling_price or Decimal(0)
        uzum_price = d.uzum_price if d.uzum_price is not None else price
        yandex_price = d.yandex_price if d.yandex_price is not None else uzum_price

        margin_abs = price - cost
        margin_pct = _pct(margin_abs, price)

        uzum_net = (uzum_price * (Decimal(100) - uzum_comm) / Decimal(100)).quantize(Decimal("1"))
        uzum_margin_abs = uzum_net - cost
        uzum_margin_pct = _pct(uzum_margin_abs, uzum_net)

        yandex_net = (yandex_price * (Decimal(100) - YANDEX_COMMISSION) / Decimal(100)).quantize(Decimal("1"))
        yandex_margin_abs = yandex_net - cost

        loc_cells = []
        for loc in locations:
            a = av.get((d.id, loc.id))
            loc_cells.append({
                "loc": loc,
                "available": a.is_available if a else True,
                "uzum_ok": (not a.uzum_stop if a else True),
                "uzum_enabled": loc.uzum_enabled,
            })

        rows.append({
            "dish": d,
            "cost": cost,
            "price": price,
            "margin_abs": margin_abs,
            "margin_pct": margin_pct,
            "uzum_price": uzum_price,
            "uzum_net": uzum_net,
            "uzum_margin_abs": uzum_margin_abs,
            "uzum_margin_pct": uzum_margin_pct,
            "yandex_price": yandex_price,
            "yandex_net": yandex_net,
            "yandex_margin_abs": yandex_margin_abs,
            "loc_cells": loc_cells,
        })

    return render(request, "foodcost/dish_pricing.html", {
        "country": country,
        "locations": locations,
        "rows": rows,
        "order_sources": OrderSource.objects.filter(country=country).order_by("name"),
        "uzum_source": uzum_source,
        "uzum_comm": uzum_comm,
        "yandex_comm": YANDEX_COMMISSION,
    })
