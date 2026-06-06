from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponseForbidden

from .models import UserProfile, Promotion, Dish, PromoCode
from .views import get_country, require_section_access, user_can_edit


def _dec(value):
    """'12 000' / '12000,5' -> Decimal; пусто -> None."""
    if value is None or str(value).strip() == "":
        return None
    try:
        cleaned = str(value).replace(" ", "").replace("\u00a0", "").replace(",", ".")
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _ids(post, key):
    """Список целых id из multi-select."""
    out = []
    for raw in post.getlist(key):
        raw = str(raw).strip()
        if raw.isdigit():
            out.append(int(raw))
    return out


def _save_promotion(request, country, promo):
    """Сохранить акцию из POST. promo=None -> создать новую."""
    p = request.POST

    if promo is None:
        promo = Promotion(country=country)

    promo.type = p.get("type") or Promotion.TYPE_PERCENT_OFF
    promo.label = (p.get("label") or "").strip()
    promo.style = p.get("style") or "gray"
    promo.is_active = p.get("is_active") == "1"
    promo.date_from = p.get("date_from") or None
    promo.date_to = p.get("date_to") or None
    promo.time_from = p.get("time_from") or None
    promo.time_to = p.get("time_to") or None
    promo.stackable = p.get("stackable") == "1"
    promo.priority = _int(p.get("priority")) or 0
    promo.scope_type = p.get("scope_type") or Promotion.SCOPE_ALL

    promo.percent = _dec(p.get("percent"))
    promo.amount = _dec(p.get("amount"))
    promo.buy_quantity = _int(p.get("buy_quantity"))
    promo.pay_quantity = _int(p.get("pay_quantity"))
    promo.threshold_amount = _dec(p.get("threshold_amount"))

    gift_dish_id = _int(p.get("gift_dish"))
    promo.gift_dish_id = gift_dish_id if gift_dish_id else None
    promo.gift_quantity = _int(p.get("gift_quantity")) or 1

    promo.save()

    # M2M — только после save (нужен pk)
    scope_qs = Dish.objects.filter(country=country, id__in=_ids(p, "scope_dishes"))
    promo.scope_dishes.set(scope_qs)

    required_qs = Dish.objects.filter(country=country, id__in=_ids(p, "required_dishes"))
    promo.required_dishes.set(required_qs)

    exclude_qs = (
        Promotion.objects
        .filter(country=country, id__in=_ids(p, "excludes"))
        .exclude(id=promo.id)
    )
    promo.excludes.set(exclude_qs)

    return promo


@login_required(login_url="/login/")
def promotions_page(request, country_slug):
    """Управление маркетинговыми акциями (создание/редактирование/удаление)."""
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_SETTINGS)
    if access_error:
        return access_error

    if request.method == "POST":
        if not user_can_edit(request.user):
            return HttpResponseForbidden("Нет прав на редактирование")

        action = request.POST.get("action")

        if action == "create_promotion":
            _save_promotion(request, country, promo=None)

        elif action == "update_promotion":
            promo = get_object_or_404(
                Promotion, id=request.POST.get("item_id"), country=country,
            )
            _save_promotion(request, country, promo=promo)

        elif action == "delete_promotion":
            Promotion.objects.filter(
                id=request.POST.get("item_id"), country=country,
            ).delete()

        elif action == "toggle_promotion":
            promo = get_object_or_404(
                Promotion, id=request.POST.get("item_id"), country=country,
            )
            promo.is_active = not promo.is_active
            promo.save(update_fields=["is_active"])

        elif action == "create_promo_code":
            PromoCode.objects.create(
                country=country,
                code=request.POST.get("code", "").strip(),
                percent=request.POST.get("percent") or 0,
                is_active=bool(request.POST.get("is_active")),
                valid_from=(request.POST.get("valid_from") or None),
                valid_until=(request.POST.get("valid_until") or None),
                utm_source=(request.POST.get("utm_source") or "").strip(),
                utm_medium=(request.POST.get("utm_medium") or "").strip(),
                utm_campaign=(request.POST.get("utm_campaign") or "").strip(),
                utm_content=(request.POST.get("utm_content") or "").strip(),
                utm_term=(request.POST.get("utm_term") or "").strip(),
            )

        elif action == "update_promo_code":
            item = get_object_or_404(
                PromoCode, id=request.POST.get("item_id"), country=country,
            )
            item.percent = request.POST.get("percent") or 0
            item.is_active = bool(request.POST.get("is_active"))
            item.valid_from = request.POST.get("valid_from") or None
            item.valid_until = request.POST.get("valid_until") or None
            item.utm_source = (request.POST.get("utm_source") or "").strip()
            item.utm_medium = (request.POST.get("utm_medium") or "").strip()
            item.utm_campaign = (request.POST.get("utm_campaign") or "").strip()
            item.utm_content = (request.POST.get("utm_content") or "").strip()
            item.utm_term = (request.POST.get("utm_term") or "").strip()
            item.scope = request.POST.get("scope") or PromoCode.SCOPE_AUTO
            item.save()
            dish_ids = request.POST.getlist("eligible_dishes")
            item.eligible_dishes.set(
                Dish.objects.filter(id__in=dish_ids, country=country)
            )

        elif action == "delete_promo_code":
            PromoCode.objects.filter(
                id=request.POST.get("item_id"), country=country,
            ).delete()

        return redirect(f"/c/{country.slug}/promotions/")

    promotions = (
        Promotion.objects
        .filter(country=country)
        .select_related("gift_dish")
        .prefetch_related("scope_dishes", "required_dishes", "excludes")
        .order_by("-priority", "id")
    )

    dishes = (
        Dish.objects
        .filter(country=country, is_archived=False)
        .only("id", "name")
        .order_by("name")
    )

    promo_codes = (
        PromoCode.objects
        .filter(country=country)
        .order_by("code")
        .prefetch_related("eligible_dishes")
    )
    for pc in promo_codes:
        pc.eligible_ids = set(pc.eligible_dishes.values_list("id", flat=True))

    return render(request, "foodcost/promotions.html", {
        "country": country,
        "promotions": promotions,
        "dishes": dishes,
        "promo_codes": promo_codes,
        "can_edit": user_can_edit(request.user),
        "type_choices": Promotion.TYPE_CHOICES,
        "style_choices": Promotion.STYLE_CHOICES,
        "scope_choices": Promotion.SCOPE_CHOICES,
    })
