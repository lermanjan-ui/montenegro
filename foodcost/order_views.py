from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse
from django.utils import timezone
from django.db.models import Sum, Count
from django.shortcuts import get_object_or_404

from .models import (
    UserProfile,
    Order,
    OrderItem,
    Customer,
    CustomerAddress,
    Dish,
    Location,
    PaymentMethod,
    OrderSource,
    DeliveryProvider,
    PromoCode,
    OrderCancelReason,
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
def order_list(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_ORDERS
    )

    if access_error:
        return access_error

    today = timezone.localdate()

    orders = (
        Order.objects
        .filter(
            country=country,
            order_date__date=today
        )
        .select_related(
            "location",
            "payment_method",
            "source",
        )
        .order_by("-created_at")
    )

    total_orders = orders.count()
    
    active_orders = orders.filter(is_cancelled=False)
    cancelled_orders = orders.filter(is_cancelled=True)

    total_revenue = sum(

        order.total_amount

        for order in active_orders

    )

    total_cash = sum(
        order.total_amount
        for order in active_orders
        if order.payment_method
        and order.payment_method.is_cash
    )
    
    locations_summary = []

    locations = Location.objects.filter(
        country=country,
        is_active=True
    ).order_by("name")

    for location in locations:
        location_orders = orders.filter(location=location)

        locations_summary.append({
            "name": location.name,
            "orders_count": location_orders.count(),
            "revenue": sum(order.total_amount for order in location_orders),
        })
    
    total_delivery = sum(
        order.delivery_amount
        for order in active_orders
    )

    return render(request, "foodcost/order_list.html", {
        "country": country,
        "orders": orders,
        "total_orders": total_orders,
        "total_revenue": total_revenue,
        "total_cash": total_cash,
        "total_delivery": total_delivery,
        "locations_summary": locations_summary,
        "cancelled_orders_count": cancelled_orders.count(),
    })
    
    
@login_required(login_url="/login/")
def order_create(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_ORDERS
    )

    if access_error:
        return access_error

    if request.method == "POST":

        customer_phone = request.POST.get("customer_phone", "").strip()

        customer, created = Customer.objects.get_or_create(
            country=country,
            phone=customer_phone,
            defaults={
                "name": request.POST.get("customer_name", "").strip(),
                "telegram": request.POST.get("customer_telegram", "").strip(),
            }
        )

        customer.name = request.POST.get("customer_name", "").strip()
        customer.telegram = request.POST.get("customer_telegram", "").strip()
        customer.save()

        location = None
        source = None
        delivery_provider = None
        payment_method = None
        promo_code = None

        if request.POST.get("location_id"):
            location = get_object_or_404(
                Location,
                id=request.POST.get("location_id"),
                country=country
            )

        if request.POST.get("source_id"):
            source = get_object_or_404(
                OrderSource,
                id=request.POST.get("source_id"),
                country=country
            )

        if request.POST.get("delivery_provider_id"):
            delivery_provider = get_object_or_404(
                DeliveryProvider,
                id=request.POST.get("delivery_provider_id"),
                country=country
            )

        if request.POST.get("payment_method_id"):
            payment_method = get_object_or_404(
                PaymentMethod,
                id=request.POST.get("payment_method_id"),
                country=country
            )

        if request.POST.get("promo_code_id"):
            promo_code = get_object_or_404(
                PromoCode,
                id=request.POST.get("promo_code_id"),
                country=country
            )

        delivery_amount = clean_decimal(
            request.POST.get("delivery_amount")
        )

        subtotal_amount = Decimal("0")
        
        is_cancelled = bool(request.POST.get("is_cancelled"))

        cancel_reason = None

        cancel_reason_id = request.POST.get("cancel_reason_id")

        if cancel_reason_id:
            cancel_reason = get_object_or_404(
                OrderCancelReason,
                id=cancel_reason_id,
                country=country,
            )




        order = Order.objects.create(
            country=country,
            location=location,
            customer=customer,
            source=source,
            delivery_provider=delivery_provider,
            payment_method=payment_method,
            promo_code=promo_code,
            created_by=request.user,
            order_date=timezone.now(),

            customer_name=request.POST.get("customer_name", "").strip(),
            customer_phone=customer_phone,
            customer_telegram=request.POST.get("customer_telegram", "").strip(),

            delivery_address=request.POST.get("delivery_address", "").strip(),

            cashier_comment=request.POST.get("cashier_comment", "").strip(),

            subtotal_amount=0,
            discount_amount=0,
            delivery_amount=delivery_amount,
            total_amount=0,
            
            is_cancelled=is_cancelled,
            cancel_reason=cancel_reason,
        )

        dish_ids = request.POST.getlist("dish_id")
        quantities = request.POST.getlist("quantity")

        for index, dish_id in enumerate(dish_ids):

            if not dish_id:
                continue

            dish = get_object_or_404(
                Dish,
                id=dish_id,
                country=country
            )

            quantity = clean_decimal(
                quantities[index]
            )

            total_price = dish.selling_price * quantity

            subtotal_amount += total_price

            OrderItem.objects.create(
                order=order,
                dish=dish,
                quantity=quantity,

                price_snapshot=dish.selling_price,
                cost_snapshot=dish.calculate_cost(),

                total_price=total_price
            )

        discount_amount = Decimal("0")

        if promo_code:
            discount_amount = (
                subtotal_amount
                * promo_code.percent
                / Decimal("100")
            )

        total_amount = (
            subtotal_amount
            - discount_amount
            + delivery_amount
        )

        order.subtotal_amount = subtotal_amount
        order.discount_amount = discount_amount
        order.total_amount = total_amount
        order.save()

        delivery_address = request.POST.get(
            "delivery_address",
            ""
        ).strip()

        existing_address = CustomerAddress.objects.filter(
            customer=customer,
            address=delivery_address
        ).first()

        if not existing_address:

            is_first_address = not customer.addresses.exists()

            CustomerAddress.objects.create(
                customer=customer,
                location=location,
                address=delivery_address,
                is_default=is_first_address,
            )

        return redirect(
            f"/c/{country.slug}/orders/"
        )

    dishes = Dish.objects.filter(
        country=country
    ).order_by("name")

    locations = Location.objects.filter(
        country=country,
        is_active=True
    ).order_by("name")

    payment_methods = PaymentMethod.objects.filter(
        country=country,
        is_active=True
    ).order_by("name")

    order_sources = OrderSource.objects.filter(
        country=country,
        is_active=True
    ).order_by("name")

    delivery_providers = DeliveryProvider.objects.filter(
        country=country,
        is_active=True
    ).order_by("name")

    promo_codes = PromoCode.objects.filter(
        country=country,
        is_active=True
    ).order_by("code")
    
    cancel_reasons = OrderCancelReason.objects.filter(
        country=country,
        is_active=True
    ).order_by("name")

    return render(request, "foodcost/order_create.html", {
        "country": country,
        "dishes": dishes,
        "locations": locations,
        "payment_methods": payment_methods,
        "order_sources": order_sources,
        "delivery_providers": delivery_providers,
        "promo_codes": promo_codes,
        "cancel_reasons": cancel_reasons,
    })
    
    
@login_required(login_url="/login/")
def order_detail(request, country_slug, order_id):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_ORDERS
    )

    if access_error:
        return access_error

    order = get_object_or_404(
        Order.objects.prefetch_related("items__dish"),
        id=order_id,
        country=country
    )

    if request.method == "POST":

        customer_phone = request.POST.get(
            "customer_phone",
            ""
        ).strip()

        customer, created = Customer.objects.get_or_create(
            country=country,
            phone=customer_phone,
            defaults={
                "name": request.POST.get("customer_name", "").strip(),
                "telegram": request.POST.get("customer_telegram", "").strip(),
            }
        )

        customer.name = request.POST.get(
            "customer_name",
            ""
        ).strip()

        customer.telegram = request.POST.get(
            "customer_telegram",
            ""
        ).strip()

        customer.save()

        location = None
        source = None
        delivery_provider = None
        payment_method = None
        promo_code = None

        if request.POST.get("location_id"):
            location = get_object_or_404(
                Location,
                id=request.POST.get("location_id"),
                country=country
            )

        if request.POST.get("source_id"):
            source = get_object_or_404(
                OrderSource,
                id=request.POST.get("source_id"),
                country=country
            )

        if request.POST.get("delivery_provider_id"):
            delivery_provider = get_object_or_404(
                DeliveryProvider,
                id=request.POST.get("delivery_provider_id"),
                country=country
            )

        if request.POST.get("payment_method_id"):
            payment_method = get_object_or_404(
                PaymentMethod,
                id=request.POST.get("payment_method_id"),
                country=country
            )

        if request.POST.get("promo_code_id"):
            promo_code = get_object_or_404(
                PromoCode,
                id=request.POST.get("promo_code_id"),
                country=country
            )

        order.location = location
        order.customer = customer
        order.source = source
        order.delivery_provider = delivery_provider
        order.payment_method = payment_method
        order.promo_code = promo_code

        order.customer_name = request.POST.get(
            "customer_name",
            ""
        ).strip()

        order.customer_phone = customer_phone

        order.customer_telegram = request.POST.get(
            "customer_telegram",
            ""
        ).strip()

        order.delivery_address = request.POST.get(
            "delivery_address",
            ""
        ).strip()

        order.cashier_comment = request.POST.get(
            "cashier_comment",
            ""
        ).strip()

        delivery_amount = clean_decimal(
            request.POST.get("delivery_amount")
        )

        order.delivery_amount = delivery_amount
        
        order.is_cancelled = bool(
            request.POST.get("is_cancelled")
        )

        cancel_reason_id = request.POST.get("cancel_reason_id")

        if cancel_reason_id:
            order.cancel_reason = get_object_or_404(
                OrderCancelReason,
                id=cancel_reason_id,
                country=country
            )
        else:
            order.cancel_reason = None

        order.items.all().delete()

        subtotal_amount = Decimal("0")

        dish_ids = request.POST.getlist("dish_id")
        quantities = request.POST.getlist("quantity")

        for index, dish_id in enumerate(dish_ids):

            if not dish_id:
                continue

            dish = get_object_or_404(
                Dish,
                id=dish_id,
                country=country
            )

            quantity = clean_decimal(
                quantities[index]
            )

            total_price = dish.selling_price * quantity

            subtotal_amount += total_price

            OrderItem.objects.create(
                order=order,
                dish=dish,
                quantity=quantity,
                price_snapshot=dish.selling_price,
                cost_snapshot=dish.calculate_cost(),
                total_price=total_price
            )

        discount_amount = Decimal("0")

        if promo_code:
            discount_amount = (
                subtotal_amount
                * promo_code.percent
                / Decimal("100")
            )

        total_amount = (
            subtotal_amount
            - discount_amount
            + delivery_amount
        )

        order.subtotal_amount = subtotal_amount
        order.discount_amount = discount_amount
        order.total_amount = total_amount

        order.save()
        
        delivery_address = request.POST.get(
            "delivery_address",
            ""
        ).strip()

        existing_address = CustomerAddress.objects.filter(
            customer=customer,
            address=delivery_address
        ).first()

        if not existing_address:

            is_first_address = not customer.addresses.exists()

            CustomerAddress.objects.create(
                customer=customer,
                location=location,
                address=delivery_address,
                is_default=is_first_address,
            )

        return redirect(
            f"/c/{country.slug}/orders/{order.id}/"
        )

    dishes = Dish.objects.filter(
        country=country
    ).order_by("name")

    locations = Location.objects.filter(
        country=country,
        is_active=True
    ).order_by("name")

    payment_methods = PaymentMethod.objects.filter(
        country=country,
        is_active=True
    ).order_by("name")

    order_sources = OrderSource.objects.filter(
        country=country,
        is_active=True
    ).order_by("name")

    delivery_providers = DeliveryProvider.objects.filter(
        country=country,
        is_active=True
    ).order_by("name")

    promo_codes = PromoCode.objects.filter(
        country=country,
        is_active=True
    ).order_by("code")
    
    cancel_reasons = OrderCancelReason.objects.filter(
        country=country,
        is_active=True
    ).order_by("name")

    return render(request, "foodcost/order_detail.html", {
        "country": country,
        "order": order,

        "dishes": dishes,
        "locations": locations,
        "payment_methods": payment_methods,
        "order_sources": order_sources,
        "delivery_providers": delivery_providers,
        "promo_codes": promo_codes,
        "cancel_reasons": cancel_reasons,
    })
    
    
@login_required(login_url="/login/")
def customer_lookup(request, country_slug):
    country = get_country(country_slug, request.user)

    phone = request.GET.get("phone", "").strip()

    customer = Customer.objects.filter(
        country=country,
        phone=phone
    ).first()

    if not customer:
        return JsonResponse({
            "found": False
        })

    addresses = []

    for address in customer.addresses.select_related("location").all():
        addresses.append({
            "id": address.id,
            "address": address.address,
            "location_id": address.location_id,
            "location_name": address.location.name if address.location else "",
            "is_default": address.is_default,
        })

    last_order = customer.orders.order_by("-created_at").first()

    last_order_date = ""

    if last_order:
        last_order_date = last_order.created_at.strftime("%d.%m.%Y")

    return JsonResponse({
        "found": True,
        "name": customer.name,
        "telegram": customer.telegram,
        "comment": customer.comment,
        "is_problematic": customer.is_problematic,
        "is_regular": customer.is_regular,
        "orders_count": customer.orders.count(),
        "customer_id": customer.id,
        "last_order_date": last_order_date,
        "addresses": addresses,
    })
    
@login_required(login_url="/login/")
def customer_detail(request, country_slug, customer_id):

    country = get_country(country_slug, request.user)

    customer = get_object_or_404(
        Customer,
        id=customer_id,
        country=country
    )

    orders = (
        customer.orders
        .select_related(
            "location",
            "payment_method"
        )
        .order_by("-created_at")
    )

    total_orders = orders.count()

    total_amount = (
        orders.aggregate(
            total=Sum("total_amount")
        )["total"] or 0
    )

    average_check = 0

    if total_orders > 0:
        average_check = total_amount / total_orders

    context = {
        "country": country,
        "customer": customer,
        "orders": orders,
        "total_orders": total_orders,
        "total_amount": total_amount,
        "average_check": average_check,
    }

    return render(
        request,
        "foodcost/customer_detail.html",
        context
    )
    
    
    
@login_required(login_url="/login/")
def order_analytics(request, country_slug):

    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_ORDERS
    )

    if access_error:
        return access_error

    today = timezone.localdate()

    orders = (
        Order.objects
        .filter(
            country=country,
            order_date__date=today
        )
        .select_related(
            "location",
            "payment_method",
            "source",
        )
    )

    active_orders = orders.filter(
        is_cancelled=False
    )

    cancelled_orders = orders.filter(
        is_cancelled=True
    )

    total_orders = active_orders.count()

    cancelled_orders_count = cancelled_orders.count()

    total_revenue = sum(
        order.total_amount
        for order in active_orders
    )

    total_delivery = sum(
        order.delivery_amount
        for order in active_orders
    )

    average_check = 0

    if total_orders > 0:
        average_check = (
            total_revenue / total_orders
        )

    top_dishes = (
        OrderItem.objects
        .filter(
            order__in=active_orders
        )
        .values(
            "dish__name"
        )
        .annotate(
            total_qty=Sum("quantity"),
            total_orders=Count("id"),
        )
        .order_by("-total_qty")[:10]
    )

    hourly_stats = []

    for hour in range(24):

        hour_orders = active_orders.filter(
            created_at__hour=hour
        )

        revenue = sum(
            order.total_amount
            for order in hour_orders
        )

        hourly_stats.append({
            "hour": f"{hour:02d}:00",
            "orders": hour_orders.count(),
            "revenue": revenue,
        })

    return render(
        request,
        "foodcost/order_analytics.html",
        {
            "country": country,
            "total_orders": total_orders,
            "cancelled_orders_count": cancelled_orders_count,
            "total_revenue": total_revenue,
            "total_delivery": total_delivery,
            "average_check": average_check,
            "top_dishes": top_dishes,
            "hourly_stats": hourly_stats,
        }
    )