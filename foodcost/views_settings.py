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

        if action == "update_order_limits":
            # Лимиты заказа уровня страны. 0 = без ограничения.
            country.min_order_amount = clean_decimal(
                request.POST.get("min_order_amount")
            )
            country.cash_max_amount = clean_decimal(
                request.POST.get("cash_max_amount")
            )
            country.save(update_fields=["min_order_amount", "cash_max_amount"])

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

            
        if action == "create_cancel_reason":
            OrderCancelReason.objects.create(
                country=country,
                name=request.POST.get("name", "").strip(),
            )

            
        if action == "delete_cancel_reason":
            item = get_object_or_404(
                OrderCancelReason,
                id=request.POST.get("item_id"),
                country=country,
            )
            item.delete()

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

        return redirect(f"/c/{country.slug}/settings/")

    payment_methods = PaymentMethod.objects.filter(country=country).order_by("name")
    order_sources = OrderSource.objects.filter(country=country).order_by("name")
    delivery_providers = DeliveryProvider.objects.filter(country=country).order_by("name")
    cancel_reasons = OrderCancelReason.objects.filter(
        country=country
    ).order_by("name")

    return render(request, "foodcost/settings.html", {
        "country": country,
        "payment_methods": payment_methods,
        "order_sources": order_sources,
        "delivery_providers": delivery_providers,
        "cancel_reasons": cancel_reasons,
    })


@login_required(login_url="/login/")
def dish_categories_page(request, country_slug):
    """Отдельная страница управления категориями блюд (раньше была блоком
    на странице настроек). Доступ — тот же, что у настроек."""
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_SETTINGS
    )
    if access_error:
        return access_error

    if request.method == "POST":
        action = request.POST.get("action")

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

            item.name = request.POST.get("name", "").strip()
            item.public_name = request.POST.get("public_name", "").strip()
            item.slug = request.POST.get("slug", "").strip()

            try:
                item.site_sort_order = int(
                    request.POST.get("site_sort_order") or 0
                )
            except (TypeError, ValueError):
                item.site_sort_order = 0

            item.is_visible_on_site = bool(
                request.POST.get("is_visible_on_site")
            )

            item.photo_url = (request.POST.get("photo_url") or "").strip()

            uploaded_photo = request.FILES.get("photo")
            if uploaded_photo:
                if item.photo:
                    item.photo.delete(save=False)
                item.photo = uploaded_photo
            elif request.POST.get("photo_clear"):
                if item.photo:
                    item.photo.delete(save=False)
                item.photo = None

            item.save()

        if action == "delete_category":
            item = get_object_or_404(
                DishCategory,
                id=request.POST.get("item_id"),
                country=country,
            )
            item.delete()

        return redirect(f"/c/{country.slug}/dish-categories/")

    categories = DishCategory.objects.filter(country=country).order_by("name")

    return render(request, "foodcost/dish_categories.html", {
        "country": country,
        "categories": categories,
    })