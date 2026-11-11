"""Вьюхи «Настройка кассы».

Переиспользуют хелперы и права существующего кабинета:
  - get_country(slug, user)      — арендатор по слагу + проверка доступа
  - require_section_access(...)  — проверка секции прав (берём SECTION_SETTINGS)
`country` обязательно кладём в контекст — его использует меню в base.html.
Новых прав/секций не заводим, UserProfile не трогаем.
"""

import random
import string
from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.views.decorators.http import require_POST

from django.utils.translation import gettext as _
from foodcost.views import get_country
from foodcost.models import UserProfile, DishCategory, Location, PaymentMethod

from .access import (
    pos_require, POS_SETTINGS, POS_CAPS, CAP_LABELS,
    explicit_caps, set_pos_caps, pos_caps_of, _is_admin_role,
)

from .models import (
    KitchenScreen, KitchenTimeThreshold, ServiceTipSettings,
    LoyaltyProgram, LoyaltyTier,
    FiscalizationSettings, PaymentMethodLocationConfig,
    FloorZone, FloorTable,
)


def _gen_code():
    return "".join(random.choices(string.digits, k=4))


def _int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _dec(value, default="0"):
    try:
        raw = str(value).replace(" ", "").replace(",", ".").strip()
        return Decimal(raw or default)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _bool(request, name):
    return bool(request.POST.get(name))


@login_required(login_url="/login/")
def pos_root(request, country_slug):
    """Корень /pos/ — ведёт на первую готовую страницу (хаба нет, навигация
    из левого меню)."""
    country = get_country(country_slug, request.user)
    return redirect(f"/c/{country.slug}/pos/kitchen/")


@login_required(login_url="/login/")
def coming_soon(request, country_slug, title):
    """Заглушка для ещё не сделанных разделов «Настройки кассы»."""
    country = get_country(country_slug, request.user)
    access_error = pos_require(request.user, POS_SETTINGS)
    if access_error:
        return access_error
    return render(request, "pos/soon.html", {"country": country, "title": title})


@login_required(login_url="/login/")
def kitchen_settings(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = pos_require(request.user, POS_SETTINGS)
    if access_error:
        return access_error

    redirect_url = f"/c/{country.slug}/pos/kitchen/"

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "add_screen":
            name = (request.POST.get("name") or "").strip()
            if name:
                loc_id = request.POST.get("location") or None
                screen = KitchenScreen.objects.create(
                    tenant=country,
                    name=name,
                    location_id=loc_id or None,
                    pairing_code=_gen_code(),
                )
                cat_ids = request.POST.getlist("categories")
                if cat_ids:
                    screen.categories.set(
                        DishCategory.objects.filter(country=country, id__in=cat_ids)
                    )

        elif action == "delete_screen":
            get_object_or_404(
                KitchenScreen, id=request.POST.get("screen_id"), tenant=country
            ).delete()

        elif action == "new_code":
            screen = get_object_or_404(
                KitchenScreen, id=request.POST.get("screen_id"), tenant=country
            )
            screen.pairing_code = _gen_code()
            screen.is_connected = False
            screen.save(update_fields=["pairing_code", "is_connected"])

        elif action == "add_threshold":
            KitchenTimeThreshold.objects.create(
                tenant=country,
                screen_id=(request.POST.get("screen") or None) or None,
                category_id=(request.POST.get("category") or None) or None,
                warn_minutes=_int(request.POST.get("warn"), 8),
                late_minutes=_int(request.POST.get("late"), 15),
            )

        elif action == "save_threshold":
            thr = get_object_or_404(
                KitchenTimeThreshold, id=request.POST.get("threshold_id"), tenant=country
            )
            thr.warn_minutes = _int(request.POST.get("warn"), thr.warn_minutes)
            thr.late_minutes = _int(request.POST.get("late"), thr.late_minutes)
            thr.save(update_fields=["warn_minutes", "late_minutes"])

        elif action == "delete_threshold":
            get_object_or_404(
                KitchenTimeThreshold, id=request.POST.get("threshold_id"), tenant=country
            ).delete()

        return redirect(redirect_url)

    screens = (
        KitchenScreen.objects.filter(tenant=country)
        .prefetch_related("categories")
        .order_by("sort_order", "id")
    )
    thresholds = (
        KitchenTimeThreshold.objects.filter(tenant=country)
        .select_related("screen", "category", "dish")
        .order_by("id")
    )
    categories = DishCategory.objects.filter(country=country).order_by("name")
    locations = Location.objects.filter(country=country).order_by("name")

    return render(request, "pos/kitchen.html", {
        "country": country,
        "screens": screens,
        "thresholds": thresholds,
        "categories": categories,
        "locations": locations,
    })


@login_required(login_url="/login/")
def service_tips(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = pos_require(request.user, POS_SETTINGS)
    if access_error:
        return access_error

    # v1 — одна запись «для всех точек» (location=null). Переопределения по
    # точкам добавим позже.
    cfg, _created = ServiceTipSettings.objects.get_or_create(
        tenant=country, location=None,
    )

    if request.method == "POST":
        cfg.service_enabled = _bool(request, "service_enabled")
        cfg.service_percent = _dec(request.POST.get("service_percent"), "0")
        cfg.service_dine_in = _bool(request, "service_dine_in")
        cfg.service_delivery = _bool(request, "service_delivery")
        cfg.service_pickup = _bool(request, "service_pickup")
        cfg.service_auto_add = _bool(request, "service_auto_add")
        cfg.service_allow_remove = _bool(request, "service_allow_remove")

        cfg.tips_enabled = _bool(request, "tips_enabled")
        presets = []
        for part in (request.POST.get("tips_presets") or "").split(","):
            part = part.strip()
            if part:
                presets.append(_int(part))
        cfg.tips_presets = [p for p in presets if p > 0]
        cfg.tips_allow_custom = _bool(request, "tips_allow_custom")
        recipient = request.POST.get("tips_recipient")
        valid = {c[0] for c in ServiceTipSettings.TIPS_RECIPIENT_CHOICES}
        if recipient in valid:
            cfg.tips_recipient = recipient
        cfg.save()
        return redirect(f"/c/{country.slug}/pos/service/")

    return render(request, "pos/service.html", {
        "country": country,
        "cfg": cfg,
        "presets_str": ", ".join(str(p) for p in (cfg.tips_presets or [])),
        "recipient_choices": ServiceTipSettings.TIPS_RECIPIENT_CHOICES,
    })


@login_required(login_url="/login/")
def loyalty(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = pos_require(request.user, POS_SETTINGS)
    if access_error:
        return access_error

    program, _created = LoyaltyProgram.objects.get_or_create(tenant=country)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "save_program":
            program.enabled = _bool(request, "enabled")
            program.identify_by_phone = _bool(request, "identify_by_phone")
            program.identify_by_qr = _bool(request, "identify_by_qr")
            program.require_sms_on_redeem = _bool(request, "require_sms_on_redeem")
            program.earn_percent = _dec(request.POST.get("earn_percent"), "0")
            program.earn_exclude_discounted = _bool(request, "earn_exclude_discounted")
            program.redeem_max_percent = _dec(request.POST.get("redeem_max_percent"), "0")
            program.combine_with_discounts = _bool(request, "combine_with_discounts")
            program.points_expiry_months = _int(request.POST.get("points_expiry_months"), 0)
            program.tiers_enabled = _bool(request, "tiers_enabled")
            program.save()

        elif action == "add_tier":
            name = (request.POST.get("name") or "").strip()
            if name:
                LoyaltyTier.objects.create(
                    program=program, name=name,
                    min_turnover=_dec(request.POST.get("min_turnover"), "0"),
                    earn_percent=_dec(request.POST.get("tier_earn"), "0"),
                    sort_order=_int(request.POST.get("sort_order"), 0),
                )

        elif action == "save_tier":
            tier = get_object_or_404(
                LoyaltyTier, id=request.POST.get("tier_id"), program=program
            )
            name = (request.POST.get("name") or "").strip()
            if name:
                tier.name = name
            tier.min_turnover = _dec(request.POST.get("min_turnover"), str(tier.min_turnover))
            tier.earn_percent = _dec(request.POST.get("tier_earn"), str(tier.earn_percent))
            tier.save()

        elif action == "delete_tier":
            get_object_or_404(
                LoyaltyTier, id=request.POST.get("tier_id"), program=program
            ).delete()

        return redirect(f"/c/{country.slug}/pos/loyalty/")

    return render(request, "pos/loyalty.html", {
        "country": country,
        "program": program,
        "tiers": program.tiers.all(),
    })


@login_required(login_url="/login/")
def payments(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = pos_require(request.user, POS_SETTINGS)
    if access_error:
        return access_error

    fiscal, _created = FiscalizationSettings.objects.get_or_create(
        tenant=country, location=None,
    )
    methods = list(PaymentMethod.objects.filter(country=country).order_by("id"))
    configs = {}
    for m in methods:
        cfg, _created = PaymentMethodLocationConfig.objects.get_or_create(
            payment_method=m, location=None,
        )
        configs[m.id] = cfg

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "save_fiscal":
            mode = request.POST.get("mode")
            if mode in {c[0] for c in FiscalizationSettings.MODE_CHOICES}:
                fiscal.mode = mode
            vat = request.POST.get("vat_mode")
            if vat in {c[0] for c in FiscalizationSettings.VAT_CHOICES}:
                fiscal.vat_mode = vat
            fiscal.save()

        elif action == "save_methods":
            valid_fz = {c[0] for c in PaymentMethodLocationConfig.FISCAL_CHOICES}
            for m in methods:
                cfg = configs[m.id]
                cfg.is_visible = bool(request.POST.get(f"visible_{m.id}"))
                fz = request.POST.get(f"fiscal_{m.id}")
                if fz in valid_fz:
                    cfg.fiscalization = fz
                cfg.save()

        return redirect(f"/c/{country.slug}/pos/payments/")

    rows = [{"m": m, "cfg": configs[m.id]} for m in methods]
    return render(request, "pos/payments.html", {
        "country": country,
        "fiscal": fiscal,
        "rows": rows,
        "mode_choices": FiscalizationSettings.MODE_CHOICES,
        "vat_choices": FiscalizationSettings.VAT_CHOICES,
        "fiscal_choices": PaymentMethodLocationConfig.FISCAL_CHOICES,
    })


@login_required(login_url="/login/")
def floor_plan(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = pos_require(request.user, POS_SETTINGS)
    if access_error:
        return access_error

    valid_shapes = {c[0] for c in FloorTable.SHAPE_CHOICES}

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "add_zone":
            name = (request.POST.get("name") or "").strip()
            if name:
                FloorZone.objects.create(
                    tenant=country, name=name,
                    location_id=(request.POST.get("location") or None) or None,
                )

        elif action == "save_zone":
            zone = get_object_or_404(FloorZone, id=request.POST.get("zone_id"), tenant=country)
            name = (request.POST.get("name") or "").strip()
            if name:
                zone.name = name
            zone.location_id = (request.POST.get("location") or None) or None
            zone.save()

        elif action == "delete_zone":
            get_object_or_404(FloorZone, id=request.POST.get("zone_id"), tenant=country).delete()

        elif action == "add_table":
            zone = get_object_or_404(FloorZone, id=request.POST.get("zone_id"), tenant=country)
            number = (request.POST.get("number") or "").strip()
            if number:
                shape = request.POST.get("shape")
                cnt = zone.tables.count()
                px = 12 + (cnt % 6) * 14          # разброс по сетке, чтобы не слипались
                py = min(15 + (cnt // 6) * 18, 88)
                FloorTable.objects.create(
                    zone=zone, number=number,
                    seats=_int(request.POST.get("seats"), 2),
                    shape=shape if shape in valid_shapes else FloorTable.SHAPE_SQUARE,
                    pos_x=px, pos_y=py,
                )

        elif action == "delete_table":
            get_object_or_404(
                FloorTable, id=request.POST.get("table_id"), zone__tenant=country
            ).delete()

        return redirect(f"/c/{country.slug}/pos/floor/")

    zones = (
        FloorZone.objects.filter(tenant=country)
        .prefetch_related("tables").order_by("sort_order", "id")
    )
    locations = Location.objects.filter(country=country).order_by("name")
    return render(request, "pos/floor.html", {
        "country": country,
        "zones": zones,
        "locations": locations,
        "shape_choices": FloorTable.SHAPE_CHOICES,
    })


@login_required(login_url="/login/")
def access_roles(request, country_slug):
    """Доступы кассы: назначение POS-ролей сотрудникам (официант/повар/
    менеджер) поверх существующего UserProfile.allowed_sections."""
    country = get_country(country_slug, request.user)
    access_error = pos_require(request.user, POS_SETTINGS)
    if access_error:
        return access_error

    profiles = (
        UserProfile.objects
        .filter(countries=country)
        .select_related("user")
        .order_by("user__username")
    )

    if request.method == "POST" and request.POST.get("action") == "save":
        uid = request.POST.get("user_id")
        prof = UserProfile.objects.filter(user_id=_int_safe(uid), countries=country).first()
        if prof is not None:
            caps = [c for c in request.POST.getlist("caps") if c in POS_CAPS]
            set_pos_caps(prof, caps)
        return redirect(f"/c/{country.slug}/pos/access/")

    rows = []
    for p in profiles:
        is_admin = p.user.is_superuser or _is_admin_role(getattr(p, "role", None))
        rows.append({
            "profile": p,
            "user": p.user,
            "is_admin": is_admin,
            "explicit": explicit_caps(p),
            "effective": pos_caps_of(p.user),
        })

    return render(request, "pos/access.html", {
        "country": country,
        "rows": rows,
        "caps": [(c, CAP_LABELS[c]) for c in POS_CAPS],
    })


def _int_safe(v):
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return 0


@login_required(login_url="/login/")
@require_POST
def floor_table_move(request, country_slug):
    """Сохранить позицию стола на плане (drag-drop). pos_x/pos_y в процентах."""
    country = get_country(country_slug, request.user)
    access_error = pos_require(request.user, POS_SETTINGS)
    if access_error:
        return access_error
    table = get_object_or_404(
        FloorTable, id=_int_safe(request.POST.get("table_id")), zone__tenant=country,
    )

    def _clamp(v):
        try:
            v = float(str(v).replace(",", "."))
        except (TypeError, ValueError):
            return None
        return max(0.0, min(100.0, v))

    px = _clamp(request.POST.get("x"))
    py = _clamp(request.POST.get("y"))
    if px is None or py is None:
        return JsonResponse({"error": _("Некорректные координаты.")}, status=400)
    table.pos_x = round(px, 2)
    table.pos_y = round(py, 2)
    table.save(update_fields=["pos_x", "pos_y"])
    return JsonResponse({"ok": True, "table_id": table.id, "x": float(table.pos_x), "y": float(table.pos_y)})
