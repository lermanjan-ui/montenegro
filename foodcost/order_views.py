from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponseForbidden
from django.utils import timezone
from django.db.models import Sum, Count, F
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
    WriteOff,
    FinancialExpense,
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

    active_orders = orders.filter(is_cancelled=False)

    cancelled_orders = orders.filter(is_cancelled=True)

    total_orders = active_orders.count()

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
def order_all_list(request, country_slug):

    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_ORDERS
    )

    if access_error:
        return access_error
        
    if not request.user.is_superuser:
        return HttpResponseForbidden(
            "Только главный админ может смотреть все заказы"
        )

    orders = (
        Order.objects
        .filter(country=country)
        .select_related(
            "location",
            "payment_method",
            "source",
        )
        .order_by("-order_date")
    )

    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    location_id = request.GET.get("location_id")
    status = request.GET.get("status")
    search = request.GET.get("search")

    if date_from:
        orders = orders.filter(
            order_date__date__gte=date_from
        )

    if date_to:
        orders = orders.filter(
            order_date__date__lte=date_to
        )

    if location_id:
        orders = orders.filter(
            location_id=location_id
        )

    if status == "active":
        orders = orders.filter(
            is_cancelled=False
        )

    elif status == "cancelled":
        orders = orders.filter(
            is_cancelled=True
        )

    if search:

        orders = orders.filter(
            customer_phone__icontains=search
        ) | orders.filter(
            customer_name__icontains=search
        ) | orders.filter(
            id__icontains=search
        )

    total_orders = orders.count()

    active_orders = orders.filter(
        is_cancelled=False
    )

    total_revenue = sum(
        order.total_amount
        for order in active_orders
    )

    total_delivery = sum(
        order.delivery_amount
        for order in active_orders
    )

    locations = Location.objects.filter(
        country=country,
        is_active=True
    ).order_by("name")

    return render(
        request,
        "foodcost/order_all_list.html",
        {
            "country": country,
            "orders": orders[:300],

            "total_orders": total_orders,
            "total_revenue": total_revenue,
            "total_delivery": total_delivery,

            "locations": locations,

            "date_from": date_from,
            "date_to": date_to,
            "location_id": location_id,
            "status": status,
            "search": search,
        }
    )
    
    
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
            
        if source and source.commission_percent > 0:

            delivery_provider = None

            if source.default_payment_method:
                payment_method = source.default_payment_method

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
        
        if is_cancelled:
            payment_method = None
            delivery_provider = None

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

            # Courier-facing fields (Part 9/10). Optional — empty if the
            # cashier didn't fill them. Public website orders fill them via
            # the order_create API and they appear here on edit.
            delivery_landmark=request.POST.get("delivery_landmark", "").strip(),
            courier_comment=request.POST.get("courier_comment", "").strip(),
            leave_at_door=bool(request.POST.get("leave_at_door")),

            subtotal_amount=0,
            discount_amount=0,
            delivery_amount=delivery_amount,
            total_amount=0,
            
            is_cancelled=is_cancelled,
            cancel_reason=cancel_reason,
        )

        dish_ids = request.POST.getlist("dish_id")
        quantities = request.POST.getlist("quantity")
        prices = request.POST.getlist("price")

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

            custom_price = clean_decimal(prices[index])

            if custom_price <= 0:
                custom_price = dish.selling_price

            total_price = custom_price * quantity

            subtotal_amount += total_price

            OrderItem.objects.create(
                order=order,
                dish=dish,
                quantity=quantity,

                price_snapshot=custom_price,
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

        food_total = subtotal_amount - discount_amount

        free_customer_delivery = bool(
            request.POST.get("free_customer_delivery")
        )

        customer_delivery_amount = Decimal("0")

        if (
            not free_customer_delivery
            and source
            and source.name.lower() == "сайт"
            and food_total > 0
            and food_total < Decimal("150000")
        ):
            customer_delivery_amount = Decimal("15000")

        if is_cancelled:
            customer_delivery_amount = Decimal("0")

        total_amount = food_total + customer_delivery_amount
        
        commission_amount = Decimal("0")

        if source:
            commission_base = food_total

            commission_amount = (
                commission_base
                * source.commission_percent
                / Decimal("100")
            )

        net_revenue = total_amount - commission_amount

        order.subtotal_amount = subtotal_amount
        order.discount_amount = discount_amount
        order.total_amount = total_amount
        order.commission_amount = commission_amount
        order.net_revenue = net_revenue
        order.customer_delivery_amount = customer_delivery_amount
        order.free_customer_delivery = free_customer_delivery
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

    # Cashier picker MUST exclude archived dishes — they should never be
    # added to new orders. Old orders that already contain an archived dish
    # render via OrderItem.dish FK and remain unaffected.
    dishes = Dish.objects.filter(
        country=country,
        is_archived=False,
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
        
        action = request.POST.get("action")

        if action == "delete_order":
            if not request.user.is_superuser:
                return HttpResponseForbidden("Удалять заказы может только главный админ")

            order.delete()

            return redirect(f"/c/{country.slug}/orders/")

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
            
        if source and source.commission_percent > 0:

            delivery_provider = None

            if source.default_payment_method:
                payment_method = source.default_payment_method

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

        # Courier-facing fields (Part 9/10) — also editable from this page.
        order.delivery_landmark = request.POST.get(
            "delivery_landmark",
            ""
        ).strip()

        order.courier_comment = request.POST.get(
            "courier_comment",
            ""
        ).strip()

        order.leave_at_door = bool(request.POST.get("leave_at_door"))

        delivery_amount = clean_decimal(
            request.POST.get("delivery_amount")
        )

        order.delivery_amount = delivery_amount
        
        order.is_cancelled = bool(
            request.POST.get("is_cancelled")
        )
        
        if order.is_cancelled:
            order.payment_method = None
            order.delivery_provider = None

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
        prices = request.POST.getlist("price")

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

            custom_price = clean_decimal(prices[index])

            if custom_price <= 0:
                custom_price = dish.selling_price

            total_price = custom_price * quantity

            subtotal_amount += total_price

            OrderItem.objects.create(
                order=order,
                dish=dish,
                quantity=quantity,
                price_snapshot=custom_price,
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

        food_total = subtotal_amount - discount_amount

        free_customer_delivery = bool(
            request.POST.get("free_customer_delivery")
        )

        customer_delivery_amount = Decimal("0")

        if (
            not free_customer_delivery
            and source
            and source.name.lower() == "сайт"
            and food_total > 0
            and food_total < Decimal("150000")
        ):
            customer_delivery_amount = Decimal("15000")

        if order.is_cancelled:
            customer_delivery_amount = Decimal("0")

        total_amount = food_total + customer_delivery_amount
        
        commission_amount = Decimal("0")

        if source:
            commission_base = food_total

            commission_amount = (
                commission_base
                * source.commission_percent
                / Decimal("100")
            )

        net_revenue = total_amount - commission_amount

        order.subtotal_amount = subtotal_amount
        order.discount_amount = discount_amount
        order.total_amount = total_amount
        order.commission_amount = commission_amount
        order.net_revenue = net_revenue

        if request.user.is_superuser:

            order_date = request.POST.get("order_date")

            if order_date:

                try:
                    parsed_date = timezone.datetime.fromisoformat(
                        order_date
                    )

                    current_time = order.order_date.time()

                    order.order_date = timezone.make_aware(
                        timezone.datetime.combine(
                            parsed_date.date(),
                            current_time
                        )
                    )

                except:
                    pass
                    
        order.customer_delivery_amount = customer_delivery_amount
        order.free_customer_delivery = free_customer_delivery
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

    # Cashier picker MUST exclude archived dishes — they should never be
    # added to new orders. Old orders that already contain an archived dish
    # render via OrderItem.dish FK and remain unaffected.
    dishes = Dish.objects.filter(
        country=country,
        is_archived=False,
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
    period = request.GET.get("period", "today")

    date_from = today
    date_to = today

    if period == "yesterday":
        date_from = today - timezone.timedelta(days=1)
        date_to = date_from

    elif period == "7days":
        date_from = today - timezone.timedelta(days=6)

    elif period == "30days":
        date_from = today - timezone.timedelta(days=29)

    elif period == "month":
        date_from = today.replace(day=1)

    custom_from = request.GET.get("date_from")
    custom_to = request.GET.get("date_to")

    if custom_from:
        try:
            date_from = timezone.datetime.strptime(custom_from, "%Y-%m-%d").date()
        except:
            pass

    if custom_to:
        try:
            date_to = timezone.datetime.strptime(custom_to, "%Y-%m-%d").date()
        except:
            pass

    orders = (
        Order.objects
        .filter(
            country=country,
            order_date__date__gte=date_from,
            order_date__date__lte=date_to,
        )
        .select_related(
            "location",
            "payment_method",
            "source",
            "cancel_reason",
            "customer",
        )
        .order_by("-created_at")
    )

    active_orders = orders.filter(is_cancelled=False)
    cancelled_orders = orders.filter(is_cancelled=True)

    total_orders = orders.count()
    active_orders_count = active_orders.count()
    cancelled_orders_count = cancelled_orders.count()

    gross_revenue = sum(order.total_amount for order in active_orders)
    subtotal_revenue = sum(order.subtotal_amount for order in active_orders)
    discount_loss = sum(order.discount_amount for order in active_orders)

    commission_total = Decimal("0")

    for order in active_orders:
        if order.source:
            commission_total += (
                order.total_amount
                * order.source.commission_percent
                / Decimal("100")
            )

    net_revenue = gross_revenue - commission_total

    customer_delivery_total = sum(
        order.customer_delivery_amount
        for order in active_orders
    )

    delivery_total = sum(
        order.delivery_amount
        for order in active_orders
    )

    company_delivery_cost = delivery_total - customer_delivery_total

    total_cost = Decimal("0")

    order_items = OrderItem.objects.filter(
        order__in=active_orders
    ).select_related("dish")

    for item in order_items:
        total_cost += item.cost_snapshot * item.quantity

    writeoffs_cost = (
        WriteOff.objects
        .filter(
            country=country,
            writeoff_date__gte=date_from,
            writeoff_date__lte=date_to,
        )
        .aggregate(total=Sum("cost"))["total"]
        or Decimal("0")
    )

    labor_cost = Decimal("0")
    tax_cost = Decimal("0")

    rent_cost = (
        FinancialExpense.objects
        .filter(
            country=country,
            expense_type=FinancialExpense.EXPENSE_RENT,
            expense_date__gte=date_from,
            expense_date__lte=date_to,
        )
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    utilities_cost = (
        FinancialExpense.objects
        .filter(
            country=country,
            expense_type=FinancialExpense.EXPENSE_UTILITIES,
            expense_date__gte=date_from,
            expense_date__lte=date_to,
        )
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    marketing_cost = (
        FinancialExpense.objects
        .filter(
            country=country,
            expense_type=FinancialExpense.EXPENSE_MARKETING,
            expense_date__gte=date_from,
            expense_date__lte=date_to,
        )
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    other_expenses = (
        FinancialExpense.objects
        .filter(
            country=country,
            expense_type=FinancialExpense.EXPENSE_OTHER,
            expense_date__gte=date_from,
            expense_date__lte=date_to,
        )
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    operating_expenses = (
        labor_cost
        + rent_cost
        + utilities_cost
        + marketing_cost
        + tax_cost
        + other_expenses
    )

    profit_before_fixed = (
        net_revenue
        - total_cost
        - company_delivery_cost
        - writeoffs_cost
    )

    ebitda = profit_before_fixed - operating_expenses

    average_check = Decimal("0")

    if active_orders_count > 0:
        average_check = gross_revenue / active_orders_count

    cancel_percent = 0

    if total_orders > 0:
        cancel_percent = cancelled_orders_count / total_orders * 100

    cash_total = Decimal("0")
    bank_total = Decimal("0")

    for order in active_orders:

        order_food_total = (
            order.subtotal_amount
            - order.discount_amount
        )

        order_commission = Decimal("0")

        if order.source:
            order_commission = (
                order_food_total
                * order.source.commission_percent
                / Decimal("100")
            )

        if order.payment_method and order.payment_method.is_cash:
            cash_total += order.total_amount
        else:
            if order.source and order.source.commission_percent > 0:
                bank_total += order.total_amount - order_commission
            else:
                bank_total += order.total_amount

    customer_ids = set(
        active_orders
        .exclude(customer_id__isnull=True)
        .values_list("customer_id", flat=True)
    )

    new_customers_count = 0
    returning_customers_count = 0

    for customer_id in customer_ids:
        previous_orders = Order.objects.filter(
            country=country,
            customer_id=customer_id,
            is_cancelled=False,
            order_date__date__lt=date_from,
        ).exists()

        if previous_orders:
            returning_customers_count += 1
        else:
            new_customers_count += 1

    latest_orders = orders[:10]

    locations_summary = []

    locations = Location.objects.filter(
        country=country,
        is_active=True
    ).order_by("name")

    for location in locations:

        location_orders = orders.filter(location=location)
        location_active_orders = location_orders.filter(is_cancelled=False)
        location_cancelled_orders = location_orders.filter(is_cancelled=True)

        location_revenue = sum(order.total_amount for order in location_active_orders)
        location_commission = Decimal("0")

        for order in location_active_orders:
            if order.source:
                location_commission += (
                    order.total_amount
                    * order.source.commission_percent
                    / Decimal("100")
                )

        location_net_revenue = location_revenue - location_commission

        location_customer_delivery = sum(
            order.customer_delivery_amount
            for order in location_active_orders
        )

        location_delivery_total = sum(
            order.delivery_amount
            for order in location_active_orders
        )

        location_company_delivery = (
            location_delivery_total
            - location_customer_delivery
        )

        location_discount_loss = sum(
            order.discount_amount
            for order in location_active_orders
        )

        location_cost = Decimal("0")

        location_items = OrderItem.objects.filter(
            order__in=location_active_orders
        )

        for item in location_items:
            location_cost += item.cost_snapshot * item.quantity

        location_writeoffs = (
            WriteOff.objects
            .filter(
                country=country,
                writeoff_date__gte=date_from,
                writeoff_date__lte=date_to,
            )
            .aggregate(total=Sum("cost"))["total"]
            or Decimal("0")
        )

        location_profit_before_fixed = (
            location_net_revenue
            - location_cost
            - location_company_delivery
            - location_writeoffs
        )

        location_average_check = Decimal("0")

        if location_active_orders.count() > 0:
            location_average_check = (
                location_revenue / location_active_orders.count()
            )

        location_cash = Decimal("0")
        location_bank = Decimal("0")

        for order in location_active_orders:

            order_food_total = (
                order.subtotal_amount
                - order.discount_amount
            )

            order_commission = Decimal("0")

            if order.source:
                order_commission = (
                    order_food_total
                    * order.source.commission_percent
                    / Decimal("100")
                )

            if order.payment_method and order.payment_method.is_cash:
                location_cash += order.total_amount
            else:
                if order.source and order.source.commission_percent > 0:
                    location_bank += order.total_amount - order_commission
                else:
                    location_bank += order.total_amount

        location_customer_ids = set(
            location_active_orders
            .exclude(customer_id__isnull=True)
            .values_list("customer_id", flat=True)
        )

        location_new_customers = 0
        location_returning_customers = 0

        for customer_id in location_customer_ids:
            previous_orders = Order.objects.filter(
                country=country,
                customer_id=customer_id,
                is_cancelled=False,
                order_date__date__lt=date_from,
            ).exists()

            if previous_orders:
                location_returning_customers += 1
            else:
                location_new_customers += 1

        locations_summary.append({
            "name": location.name,

            "orders_count": location_orders.count(),
            "active_orders_count": location_active_orders.count(),
            "cancelled_orders_count": location_cancelled_orders.count(),

            "revenue": location_revenue,
            "net_revenue": location_net_revenue,
            "commission": location_commission,

            "customer_delivery": location_customer_delivery,
            "delivery_total": location_delivery_total,
            "company_delivery": location_company_delivery,

            "discount_loss": location_discount_loss,
            "food_cost": location_cost,
            "writeoffs_cost": location_writeoffs,

            "profit_before_fixed": location_profit_before_fixed,
            "average_check": location_average_check,

            "cash_total": location_cash,
            "bank_total": location_bank,

            "new_customers": location_new_customers,
            "returning_customers": location_returning_customers,
        })

    top_dishes = (
        OrderItem.objects
        .filter(order__in=active_orders)
        .values("dish__name")
        .annotate(
            total_qty=Sum("quantity"),
            total_sum=Sum("total_price"),
        )
        .order_by("-total_qty")[:10]
    )

    payment_summary = []

    payment_methods = PaymentMethod.objects.filter(
        country=country,
        is_active=True
    ).order_by("name")

    for method in payment_methods:

        method_orders = active_orders.filter(payment_method=method)

        payment_summary.append({
            "name": method.name,
            "orders_count": method_orders.count(),
            "revenue": sum(order.total_amount for order in method_orders),
        })

    source_summary = []

    order_sources = OrderSource.objects.filter(
        country=country,
        is_active=True
    ).order_by("name")

    for source in order_sources:

        source_orders = active_orders.filter(source=source)

        source_revenue = sum(order.total_amount for order in source_orders)
        source_commission = Decimal("0")

        for order in source_orders:
            source_commission += (
                order.total_amount
                * source.commission_percent
                / Decimal("100")
            )

        source_summary.append({
            "name": source.name,
            "orders_count": source_orders.count(),
            "revenue": source_revenue,
            "commission": source_commission,
            "net_revenue": source_revenue - source_commission,
        })

    delivery_summary = []

    delivery_providers = DeliveryProvider.objects.filter(
        country=country,
        is_active=True
    ).order_by("name")

    for provider in delivery_providers:

        provider_orders = active_orders.filter(delivery_provider=provider)

        provider_delivery_total = sum(
            order.delivery_amount
            for order in provider_orders
        )

        provider_customer_delivery = sum(
            order.customer_delivery_amount
            for order in provider_orders
        )

        delivery_summary.append({
            "name": provider.name,
            "orders_count": provider_orders.count(),
            "delivery_sum": provider_delivery_total,
            "customer_delivery": provider_customer_delivery,
            "company_delivery": provider_delivery_total - provider_customer_delivery,
            "revenue": sum(order.total_amount for order in provider_orders),
        })

    cancel_reasons_stats = (
        cancelled_orders
        .values("cancel_reason__name")
        .annotate(total=Count("id"))
        .order_by("-total")
    )

    hourly_stats = []

    max_hour_revenue = Decimal("0")

    for hour in range(24):

        hour_orders = active_orders.filter(created_at__hour=hour)

        hour_revenue = sum(order.total_amount for order in hour_orders)

        if hour_revenue > max_hour_revenue:
            max_hour_revenue = hour_revenue

        hourly_stats.append({
            "hour": f"{hour:02d}:00",
            "orders": hour_orders.count(),
            "revenue": hour_revenue,
        })

    for item in hourly_stats:

        percent = 0

        if max_hour_revenue > 0:
            percent = item["revenue"] / max_hour_revenue * 100

        item["percent"] = percent

    daily_stats = []

    current_day = date_from
    max_daily_revenue = Decimal("0")

    while current_day <= date_to:

        day_orders = active_orders.filter(order_date__date=current_day)
        day_cancelled_orders = cancelled_orders.filter(order_date__date=current_day)

        day_revenue = sum(order.total_amount for order in day_orders)

        if day_revenue > max_daily_revenue:
            max_daily_revenue = day_revenue

        daily_stats.append({
            "date": current_day,
            "orders": day_orders.count(),
            "cancelled": day_cancelled_orders.count(),
            "revenue": day_revenue,
        })

        current_day = current_day + timezone.timedelta(days=1)

    daily_count = len(daily_stats)

    for index, item in enumerate(daily_stats):

        percent = Decimal("0")

        if max_daily_revenue > 0:
            percent = item["revenue"] / max_daily_revenue * 100

        item["percent"] = percent
        item["svg_x"] = 50 if daily_count == 1 else index / (daily_count - 1) * 100
        item["svg_y"] = max(8, 100 - float(percent))

    return render(
        request,
        "foodcost/order_analytics.html",
        {
            "country": country,
            "today": today,
            "period": period,

            "date_from": date_from,
            "date_to": date_to,

            "orders": orders,
            "latest_orders": latest_orders,

            "total_orders": total_orders,
            "active_orders_count": active_orders_count,
            "cancelled_orders_count": cancelled_orders_count,
            "cancel_percent": cancel_percent,

            "total_revenue": gross_revenue,
            "gross_revenue": gross_revenue,
            "subtotal_revenue": subtotal_revenue,
            "net_revenue": net_revenue,

            "commission_total": commission_total,
            "discount_loss": discount_loss,

            "customer_delivery_total": customer_delivery_total,
            "total_delivery": delivery_total,
            "company_delivery_cost": company_delivery_cost,

            "total_cost": total_cost,
            "writeoffs_cost": writeoffs_cost,

            "labor_cost": labor_cost,
            "rent_cost": rent_cost,
            "utilities_cost": utilities_cost,
            "marketing_cost": marketing_cost,
            "tax_cost": tax_cost,
            "other_expenses": other_expenses,
            "operating_expenses": operating_expenses,

            "profit": profit_before_fixed,
            "profit_before_fixed": profit_before_fixed,
            "ebitda": ebitda,

            "average_check": average_check,

            "cash_total": cash_total,
            "bank_total": bank_total,

            "new_customers_count": new_customers_count,
            "returning_customers_count": returning_customers_count,

            "locations_summary": locations_summary,
            "top_dishes": top_dishes,
            "payment_summary": payment_summary,
            "source_summary": source_summary,
            "delivery_summary": delivery_summary,
            "cancel_reasons_stats": cancel_reasons_stats,
            "hourly_stats": hourly_stats,
            "daily_stats": daily_stats,
        }
    )



@login_required(login_url="/login/")
def customer_list(request, country_slug):

    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_CUSTOMERS
    )

    if access_error:
        return access_error

    customers = (
        Customer.objects
        .filter(country=country)
        .annotate(
            total_spent=Sum("orders__total_amount")
        )
        .order_by("name")
    )

    search = request.GET.get("search", "").strip()

    if search:
        customers = customers.filter(
            phone__icontains=search
        ) | customers.filter(
            name__icontains=search
        )

    return render(request, "foodcost/customer_list.html", {
        "country": country,
        "customers": customers,
        "search": search,
    })