from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404

from .models import (
    UserProfile,
    PaymentMethod,
    OrderSource,
    DeliveryProvider,
    PromoCode,
)

from .views import (
    get_country,
    require_section_access,
)


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
            )

        if action == "delete_promo_code":
            item = get_object_or_404(
                PromoCode,
                id=request.POST.get("item_id"),
                country=country,
            )
            item.delete()

        return redirect(f"/c/{country.slug}/settings/")

    payment_methods = PaymentMethod.objects.filter(country=country).order_by("name")
    order_sources = OrderSource.objects.filter(country=country).order_by("name")
    delivery_providers = DeliveryProvider.objects.filter(country=country).order_by("name")
    promo_codes = PromoCode.objects.filter(country=country).order_by("code")

    return render(request, "foodcost/settings.html", {
        "country": country,
        "payment_methods": payment_methods,
        "order_sources": order_sources,
        "delivery_providers": delivery_providers,
        "promo_codes": promo_codes,
    })