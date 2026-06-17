from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect

from .models import UserProfile, Dish, Location, DishAvailability, OrderSource, DishCategory
from .views import get_country, require_section_access

YANDEX_COMMISSION = Decimal("35")  # %, фиксированная для расчёта «Выручка с Яндекса»


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


def _disc_pct(raw):
    """Скидка-процент из формы → Decimal 0..100 (некорректное/пусто → 0)."""
    raw = (raw or "").strip().replace(",", ".")
    if not raw:
        return Decimal("0")
    try:
        val = Decimal(raw)
    except (InvalidOperation, ValueError):
        return Decimal("0")
    if val < 0:
        return Decimal("0")
    if val > 100:
        return Decimal("100")
    return val


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

    def _back():
        # Возврат на страницу с сохранением выбранной категории и активной вкладки,
        # чтобы после сохранения не сбрасывало на первую вкладку «Сайт».
        params = []
        cat = (request.POST.get("category_id") or "").strip()
        if cat:
            params.append(f"category_id={cat}")
        tab = (request.POST.get("tab") or "").strip()
        if tab in ("site", "uzum", "yandex"):
            params.append(f"tab={tab}")
        base = f"/c/{country.slug}/dish-pricing/"
        return base + ("?" + "&".join(params) if params else "")

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "set_uzum_source":
            OrderSource.objects.filter(country=country).update(is_uzum=False)
            sid = request.POST.get("uzum_source_id")
            if sid:
                OrderSource.objects.filter(country=country, id=sid).update(is_uzum=True)
            return redirect(_back())

        if action == "save_dish":
            dish = Dish.objects.filter(
                id=request.POST.get("dish_id"), country=country
            ).first()
            if dish:
                sp = _dec_or_none(request.POST.get("selling_price"))
                if sp is not None:
                    dish.selling_price = sp
                dish.uzum_price = _dec_or_none(request.POST.get("uzum_price"))
                dish.yandex_price = _dec_or_none(request.POST.get("yandex_price"))
                dish.is_visible_on_site = bool(request.POST.get("is_visible_on_site"))
                dish.site_discount_percent = _disc_pct(
                    request.POST.get("site_discount_percent")
                )
                dish.uzum_discount_percent = _disc_pct(
                    request.POST.get("uzum_discount_percent")
                )
                dish.yandex_discount_percent = _disc_pct(
                    request.POST.get("yandex_discount_percent")
                )
                dish.save(update_fields=[
                    "selling_price", "uzum_price", "yandex_price",
                    "is_visible_on_site",
                    "site_discount_percent", "uzum_discount_percent",
                    "yandex_discount_percent",
                ])
                try:
                    dish.recalculate_cache()
                except Exception:
                    pass

                avail_ids = set(request.POST.getlist("avail"))
                uzum_ok_ids = set(request.POST.getlist("uzum_ok"))
                for loc in locations:
                    da, _ = DishAvailability.objects.get_or_create(
                        country=country, dish=dish, location=loc
                    )
                    da.is_available = str(loc.id) in avail_ids
                    da.uzum_stop = str(loc.id) not in uzum_ok_ids
                    da.save(update_fields=["is_available", "uzum_stop"])
            return redirect(_back())

        if action == "apply_discount_all":
            # Единая скидка на ВСЕ блюда. Пустое поле канала → этот канал не трогаем.
            updates = {}
            if (request.POST.get("all_site_discount") or "").strip():
                updates["site_discount_percent"] = _disc_pct(
                    request.POST.get("all_site_discount")
                )
            if (request.POST.get("all_uzum_discount") or "").strip():
                updates["uzum_discount_percent"] = _disc_pct(
                    request.POST.get("all_uzum_discount")
                )
            if (request.POST.get("all_yandex_discount") or "").strip():
                updates["yandex_discount_percent"] = _disc_pct(
                    request.POST.get("all_yandex_discount")
                )
            if updates:
                Dish.objects.filter(
                    country=country, is_archived=False
                ).update(**updates)
            return redirect(_back())

    # источник Uzum и его комиссия
    uzum_source = (
        OrderSource.objects.filter(country=country, is_uzum=True).first()
        or OrderSource.objects.filter(country=country, name__icontains="uzum").first()
    )
    uzum_comm = (uzum_source.commission_percent if uzum_source else Decimal(0)) or Decimal(0)

    # карта доступности по (блюдо, точка)
    av = {
        (a.dish_id, a.location_id): a
        for a in DishAvailability.objects.filter(country=country)
    }

    selected_category_id = (request.GET.get("category_id") or "").strip()
    categories = DishCategory.objects.filter(country=country).order_by("name")

    dishes = (
        Dish.objects.filter(country=country, is_archived=False)
        .select_related("category")
        .order_by("name")
    )
    if selected_category_id:
        dishes = dishes.filter(category_id=selected_category_id)

    rows = []
    for d in dishes:
        cost = d.cached_total_cost or Decimal(0)
        price = d.selling_price or Decimal(0)
        uzum_price = d.uzum_price if d.uzum_price is not None else price

        site_disc = d.site_discount_percent or Decimal(0)
        uzum_disc = d.uzum_discount_percent or Decimal(0)
        yandex_disc = d.yandex_discount_percent or Decimal(0)

        # Своя цена Яндекса: если задана — берём её, иначе подставляем основную
        # цену блюда (selling_price). Скидка Яндекса считается от этой цены.
        yandex_price = d.yandex_price if d.yandex_price is not None else price

        # Цены с учётом скидки канала (только для расчёта маржи на этой странице).
        site_price_disc = (
            price * (Decimal(100) - site_disc) / Decimal(100)
        ).quantize(Decimal("1"))
        uzum_price_disc = (
            uzum_price * (Decimal(100) - uzum_disc) / Decimal(100)
        ).quantize(Decimal("1"))
        yandex_price_disc = (
            yandex_price * (Decimal(100) - yandex_disc) / Decimal(100)
        ).quantize(Decimal("1"))

        # Сайт: без комиссии, маржа от цены со скидкой.
        margin_abs = site_price_disc - cost
        margin_pct = _pct(margin_abs, site_price_disc)

        # Uzum: нетто после комиссии, считается от цены со скидкой.
        uzum_net = (
            uzum_price_disc * (Decimal(100) - uzum_comm) / Decimal(100)
        ).quantize(Decimal("1"))
        uzum_margin_abs = uzum_net - cost
        uzum_margin_pct = _pct(uzum_margin_abs, uzum_net)

        # Яндекс: нетто после 35%, от цены со скидкой.
        yandex_net = (
            yandex_price_disc * (Decimal(100) - YANDEX_COMMISSION) / Decimal(100)
        ).quantize(Decimal("1"))
        yandex_margin_abs = yandex_net - cost
        yandex_margin_pct = _pct(yandex_margin_abs, yandex_net)

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
            "site_disc": site_disc,
            "site_price_disc": site_price_disc,
            "margin_abs": margin_abs,
            "margin_pct": margin_pct,
            "uzum_price": uzum_price,
            "uzum_disc": uzum_disc,
            "uzum_price_disc": uzum_price_disc,
            "uzum_net": uzum_net,
            "uzum_margin_abs": uzum_margin_abs,
            "uzum_margin_pct": uzum_margin_pct,
            "yandex_disc": yandex_disc,
            "yandex_price": yandex_price,
            "yandex_price_disc": yandex_price_disc,
            "yandex_net": yandex_net,
            "yandex_margin_abs": yandex_margin_abs,
            "yandex_margin_pct": yandex_margin_pct,
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
        "categories": categories,
        "selected_category_id": selected_category_id,
    })
