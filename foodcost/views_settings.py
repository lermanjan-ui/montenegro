from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from decimal import Decimal

from .models import (
    UserProfile,
    PaymentMethod,
    OrderSource,
    DeliveryProvider,
    PromoCode,
    DishCategory,
    OrderCancelReason,
    Dish,
)

from .views import (
    get_country,
    require_section_access,
)


def clean_decimal(value):
    if value is None or value == "":
        return Decimal("0")

    try:
        return Decimal(str(value).replace(",", "."))
    except Exception:
        return Decimal("0")

@login_required(login_url="/login/")
def settings_page(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_SETTINGS
    )

    if access_error:
        return access_error

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create_payment_method":
            PaymentMethod.objects.create(
                country=country,
                name=request.POST.get("name", "").strip(),
                is_cash=bool(request.POST.get("is_cash")),
            )

        if action == "delete_payment_method":
            item = get_object_or_404(
                PaymentMethod,
                id=request.POST.get("item_id"),
                country=country,
            )
            item.delete()

        if action == "create_order_source":
            OrderSource.objects.create(
                country=country,
                name=request.POST.get("name", "").strip(),

                commission_percent=clean_decimal(
                    request.POST.get("commission_percent")
                ),
                
                default_payment_method=PaymentMethod.objects.filter(
                    id=request.POST.get("default_payment_method_id"),
                    country=country
                ).first(),
            )

        if action == "delete_order_source":
            item = get_object_or_404(
                OrderSource,
                id=request.POST.get("item_id"),
                country=country,
            )
            item.delete()

        if action == "create_delivery_provider":
            DeliveryProvider.objects.create(
                country=country,
                name=request.POST.get("name", "").strip(),
            )

        if action == "delete_delivery_provider":
            item = get_object_or_404(
                DeliveryProvider,
                id=request.POST.get("item_id"),
                country=country,
            )
            item.delete()

        if action == "create_promo_code":
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

        if action == "update_promo_code":
            item = get_object_or_404(
                PromoCode,
                id=request.POST.get("item_id"),
                country=country,
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
            item.save()
            # Блюда, на которые действует скидка. Пусто = на весь заказ.
            dish_ids = request.POST.getlist("eligible_dishes")
            valid_dishes = Dish.objects.filter(id__in=dish_ids, country=country)
            item.eligible_dishes.set(valid_dishes)
            
        if action == "create_cancel_reason":
            OrderCancelReason.objects.create(
                country=country,
                name=request.POST.get("name", "").strip(),
            )

        if action == "delete_promo_code":
            item = get_object_or_404(
                PromoCode,
                id=request.POST.get("item_id"),
                country=country,
            )
            item.delete()
            
        if action == "delete_cancel_reason":
            item = get_object_or_404(
                OrderCancelReason,
                id=request.POST.get("item_id"),
                country=country,
            )
            item.delete()

        if action == "create_category":
            name = request.POST.get("name", "").strip()

            if name:
                category = DishCategory.objects.create(
                    country=country,
                    name=name,
                    is_visible_on_site=bool(
                        request.POST.get("is_visible_on_site")
                    ),
                    photo_url=(request.POST.get("photo_url") or "").strip(),
                )

                # Optional photo upload on creation.
                uploaded_photo = request.FILES.get("photo")
                if uploaded_photo:
                    category.photo = uploaded_photo
                    category.save(update_fields=["photo"])

        if action == "update_category":

            item = get_object_or_404(
                DishCategory,
                id=request.POST.get("item_id"),
                country=country,
            )

            item.name = request.POST.get(
                "name",
                ""
            ).strip()

            # ===== Public website fields =====
            item.public_name = request.POST.get(
                "public_name",
                ""
            ).strip()

            item.slug = request.POST.get(
                "slug",
                ""
            ).strip()

            try:
                item.site_sort_order = int(
                    request.POST.get("site_sort_order") or 0
                )
            except (TypeError, ValueError):
                item.site_sort_order = 0

            item.is_visible_on_site = bool(
                request.POST.get("is_visible_on_site")
            )

            # 🔗 External photo URL — has PRIORITY over uploaded photo
            # in the public API. Stored independently so the operator
            # can keep both (e.g. keep an upload as backup).
            item.photo_url = (request.POST.get("photo_url") or "").strip()

            # Photo handling — order matters:
            #   1. New upload always wins over photo_clear (saner UX if both
            #      end up posted together).
            #   2. Otherwise photo_clear deletes the file from storage and
            #      detaches the field.
            #   3. If neither is present, the existing photo is preserved.
            uploaded_photo = request.FILES.get("photo")
            if uploaded_photo:
                # If a photo already exists, delete its file on disk to avoid
                # orphaned uploads accumulating in MEDIA_ROOT.
                if item.photo:
                    item.photo.delete(save=False)
                item.photo = uploaded_photo
            elif request.POST.get("photo_clear"):
                if item.photo:
                    item.photo.delete(save=False)
                item.photo = None

            item.save()


        if action == "update_order_source":

            item = get_object_or_404(
                OrderSource,
                id=request.POST.get("item_id"),
                country=country
            )

            item.name = request.POST.get(
                "name",
                ""
            ).strip()

            item.commission_percent = clean_decimal(
                request.POST.get("commission_percent")
            )
            
            payment_method_id = request.POST.get(
                "default_payment_method_id"
            )

            if payment_method_id:
                item.default_payment_method = PaymentMethod.objects.filter(
                    id=payment_method_id,
                    country=country
                ).first()
            else:
                item.default_payment_method = None

            item.save()

      

        if action == "delete_category":
            item = get_object_or_404(
                DishCategory,
                id=request.POST.get("item_id"),
                country=country,
            )
            item.delete()

        return redirect(f"/c/{country.slug}/settings/")

    payment_methods = PaymentMethod.objects.filter(country=country).order_by("name")
    order_sources = OrderSource.objects.filter(country=country).order_by("name")
    delivery_providers = DeliveryProvider.objects.filter(country=country).order_by("name")
    promo_codes = PromoCode.objects.filter(country=country).order_by("code").prefetch_related("eligible_dishes")
    # id выбранных блюд для отметки чекбоксов в форме настройки промокода.
    for pc in promo_codes:
        pc.eligible_ids = set(pc.eligible_dishes.values_list("id", flat=True))
    cancel_reasons = OrderCancelReason.objects.filter(
        country=country
    ).order_by("name")
    categories = DishCategory.objects.filter(country=country).order_by("name")
    dishes = Dish.objects.filter(country=country, is_archived=False).order_by("name")

    return render(request, "foodcost/settings.html", {
        "country": country,
        "payment_methods": payment_methods,
        "order_sources": order_sources,
        "delivery_providers": delivery_providers,
        "promo_codes": promo_codes,
        "cancel_reasons": cancel_reasons,
        "categories": categories,
        "dishes": dishes,
    })