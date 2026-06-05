from decimal import Decimal

import logging

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.http import JsonResponse, HttpResponseForbidden
from datetime import timedelta
from django.utils import timezone
from django.core.paginator import Paginator
from django.db.models import (
    Sum, Count, F, Q, Max, Min, Value,
    Subquery, OuterRef, IntegerField, DecimalField,
)
from django.db.models.functions import Coalesce, ExtractHour, TruncDate
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
    user_can_edit,
)


logger = logging.getLogger(__name__)


def clean_decimal(value):
    if value is None or value == "":
        return Decimal("0")

    try:
        return Decimal(str(value).replace(",", "."))
    except Exception:
        return Decimal("0")


# ---------------------------------------------------------------------------
#  Customers — вспомогательные функции (используются только страницами
#  клиентов: customer_list / customer_detail).
# ---------------------------------------------------------------------------
def _fmt_money(value):
    """1234567 -> '1 234 567' (без копеек, узкий пробел как разделитель)."""
    try:
        return f"{Decimal(value):,.0f}".replace(",", "\u00a0")
    except Exception:
        return "0"


def _build_qs(get_params, drop=None, **changes):
    """Собирает querystring из текущих GET-параметров.

    - всегда сбрасывает page
    - drop="key" — убрать один параметр (для chip "удалить фильтр")
    - changes: key=value добавить/заменить, key=None удалить
    Возвращает строку вида '?a=1&b=2' или '?' если пусто.
    """
    p = get_params.copy()
    p.pop("page", None)
    if drop:
        p.pop(drop, None)
    for key, val in changes.items():
        if val is None:
            p.pop(key, None)
        else:
            p[key] = val
    enc = p.urlencode()
    return "?" + enc if enc else "?"


def _sort_link(get_params, col, current_sort, default_desc=True):
    """Метаданные для кликабельного заголовка-сортировки.

    Возвращает {"url": "?...", "arrow": "↑"/"↓"/""}. Клик переключает
    направление; неактивная колонка стартует с default-направления.
    """
    asc, desc = col, "-" + col
    if current_sort == desc:
        nxt, arrow = asc, "↓"
    elif current_sort == asc:
        nxt, arrow = desc, "↑"
    else:
        nxt, arrow = (desc if default_desc else asc), ""
    return {"url": _build_qs(get_params, sort=nxt), "arrow": arrow}


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
def orders_count(request, country_slug):
    """Лёгкий JSON-эндпоинт для звукового уведомления о новых заказах.

    Возвращает {"count": N, "latest_id": X} по заказам за сегодня (та же
    выборка, что order_list: страна + order_date сегодня). Страница заказов
    опрашивает его раз в ~15 сек; если latest_id вырос — пришёл новый заказ
    и фронт играет бип. Лёгкий — без select_related и сериализации.
    """
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_ORDERS,
    )
    if access_error:
        return access_error

    today = timezone.localdate()
    qs = Order.objects.filter(country=country, order_date__date=today)

    agg = qs.aggregate(n=Count("id"), last=Max("id"))
    return JsonResponse({
        "count": agg["n"] or 0,
        "latest_id": agg["last"] or 0,
    })


@login_required(login_url="/login/")
def order_all_list(request, country_slug):

    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_ALL_ORDERS
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

        # Уведомление о новом заказе в Telegram (тред филиала). Сбой Telegram
        # не должен ломать создание заказа — всё внутри try/except.
        try:
            from .shift_views import send_new_order_to_telegram
            send_new_order_to_telegram(order)
        except Exception:
            pass

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

    # Запоминаем, был ли заказ отменён ДО этой правки — чтобы поймать именно
    # переход «не отменён → отменён» и отправить компенсирующий Meta Refund
    # (см. блок после order.save()). Берём значение здесь, пока POST ещё не
    # перезаписал order.is_cancelled.
    was_cancelled_before = bool(order.is_cancelled)

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

        # --- Meta CAPI Refund (компенсирующее событие) ---
        # Если заказ ТОЛЬКО ЧТО стал отменённым (переход False→True), а
        # Purchase по нему уже был отправлен в Meta (meta_capi_sent), и Refund
        # ещё не слали (meta_refund_sent) — отправляем Refund, чтобы Meta
        # перестала считать его конверсией и не оптимизировалась на похожих.
        # Любой сбой Meta НЕ должен ломать сохранение заказа — ловим всё.
        if (
            order.is_cancelled
            and not was_cancelled_before
            and getattr(order, "meta_capi_sent", False)
            and not getattr(order, "meta_refund_sent", False)
        ):
            try:
                from .meta_capi import send_meta_capi_refund
                send_meta_capi_refund(order)
                order.meta_refund_sent = True
                order.save(update_fields=["meta_refund_sent"])
            except Exception as e:
                # Не валим сохранение заказа из-за Meta. Латч не ставим —
                # при следующей правке отменённого заказа попробуем снова.
                logger.warning(
                    "[meta-capi] order %s: Refund send failed: %s",
                    order.public_order_number, e,
                )

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

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_CUSTOMERS,
    )
    if access_error:
        return access_error

    customer = get_object_or_404(
        Customer,
        id=customer_id,
        country=country,
    )

    can_edit = user_can_edit(request.user)

    # ---- сохранение статусов (только роли с правом редактирования) ----
    if request.method == "POST":
        if not can_edit:
            return HttpResponseForbidden("Недостаточно прав для изменения клиента")

        customer.is_regular = bool(request.POST.get("is_regular"))
        customer.is_problematic = bool(request.POST.get("is_problematic"))
        customer.delivery_blocked = bool(request.POST.get("delivery_blocked"))
        customer.delivery_block_reason = request.POST.get(
            "delivery_block_reason", ""
        ).strip()
        customer.comment = request.POST.get("comment", "").strip()
        customer.save(update_fields=[
            "is_regular",
            "is_problematic",
            "delivery_blocked",
            "delivery_block_reason",
            "comment",
            "updated_at",
        ])
        return redirect(f"/c/{country.slug}/customers/{customer.id}/?saved=1")

    orders = (
        customer.orders
        .select_related("location", "payment_method")
        .order_by("-order_date")
    )

    total_orders = orders.count()
    total_amount = orders.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")
    average_check = (total_amount / total_orders) if total_orders else Decimal("0")

    order_rows = [{
        "id": o.id,
        "order_date": o.order_date,
        "sum_display": _fmt_money(o.total_amount),
        "location_name": o.location.name if o.location else "—",
    } for o in orders]

    return render(request, "foodcost/customer_detail.html", {
        "country": country,
        "customer": customer,
        "order_rows": order_rows,
        "total_orders": total_orders,
        "total_amount_display": _fmt_money(total_amount),
        "average_check_display": _fmt_money(average_check),
        "addresses_count": customer.addresses.count(),
        "can_edit": can_edit,
        "saved": request.GET.get("saved") == "1",
    })
    
    
    
@login_required(login_url="/login/")
def order_analytics(request, country_slug):

    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_ORDER_ANALYTICS
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
        except Exception:
            pass

    if custom_to:
        try:
            date_to = timezone.datetime.strptime(custom_to, "%Y-%m-%d").date()
        except Exception:
            pass

    # =========================================================================
    #  ОПТИМИЗАЦИЯ ПРОИЗВОДИТЕЛЬНОСТИ
    #  Раньше эта вьюха ходила в БД внутри циклов (по клиентам, филиалам,
    #  часам, дням, методам оплаты и т.д.) — на длинных периодах это тысячи
    #  запросов и таймаут. Теперь: заказы периода читаем ОДИН раз в память,
    #  все сводки считаем по этому единому снимку. Логика и ключи контекста
    #  сохранены 1:1 — цифры идентичны прежней странице.
    # =========================================================================
    ZERO = Decimal("0")
    HUNDRED = Decimal("100")

    orders_qs = (
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
        .order_by("-order_date")
    )

    # Единственный проход по заказам периода. list() кэширует результат в
    # самом queryset, поэтому передача orders_qs в шаблон не делает повторных
    # запросов (и .count в шаблоне берётся из кэша).
    all_orders = list(orders_qs)
    active = [o for o in all_orders if not o.is_cancelled]
    cancelled = [o for o in all_orders if o.is_cancelled]

    total_orders = len(all_orders)
    active_orders_count = len(active)
    cancelled_orders_count = len(cancelled)

    def _commission(order):
        if order.source:
            return order.total_amount * order.source.commission_percent / HUNDRED
        return ZERO

    gross_revenue = sum((o.total_amount for o in active), ZERO)
    subtotal_revenue = sum((o.subtotal_amount for o in active), ZERO)
    discount_loss = sum((o.discount_amount for o in active), ZERO)

    commission_total = sum((_commission(o) for o in active), ZERO)
    net_revenue = gross_revenue - commission_total

    customer_delivery_total = sum((o.customer_delivery_amount for o in active), ZERO)
    delivery_total = sum((o.delivery_amount for o in active), ZERO)
    company_delivery_cost = delivery_total - customer_delivery_total

    # ---- себестоимость проданного: все позиции активных заказов ОДНИМ
    #      запросом; группируем по заказу/филиалу/блюду в памяти. Используем
    #      cost_snapshot (себестоимость на момент продажи) — как и раньше. ----
    active_ids = [o.id for o in active]
    order_location = {o.id: o.location_id for o in active}

    items = list(
        OrderItem.objects
        .filter(order_id__in=active_ids)
        .values("order_id", "dish__name", "quantity", "cost_snapshot", "total_price")
    )

    total_cost = ZERO
    location_cost_map = {}
    dish_agg = {}

    for it in items:
        qty = it["quantity"] or ZERO
        line_cost = (it["cost_snapshot"] or ZERO) * qty
        total_cost += line_cost

        loc_id = order_location.get(it["order_id"])
        location_cost_map[loc_id] = location_cost_map.get(loc_id, ZERO) + line_cost

        name = it["dish__name"]
        d = dish_agg.get(name)
        if d is None:
            d = {"dish__name": name, "total_qty": ZERO, "total_sum": ZERO}
            dish_agg[name] = d
        d["total_qty"] += qty
        d["total_sum"] += (it["total_price"] or ZERO)

    top_dishes = sorted(
        dish_agg.values(),
        key=lambda d: d["total_qty"],
        reverse=True,
    )[:10]

    writeoffs_cost = (
        WriteOff.objects
        .filter(
            country=country,
            writeoff_date__gte=date_from,
            writeoff_date__lte=date_to,
        )
        .aggregate(total=Sum("cost"))["total"]
        or ZERO
    )

    labor_cost = ZERO
    tax_cost = ZERO

    def _expense(expense_type):
        return (
            FinancialExpense.objects
            .filter(
                country=country,
                expense_type=expense_type,
                expense_date__gte=date_from,
                expense_date__lte=date_to,
            )
            .aggregate(total=Sum("amount"))["total"]
            or ZERO
        )

    rent_cost = _expense(FinancialExpense.EXPENSE_RENT)
    utilities_cost = _expense(FinancialExpense.EXPENSE_UTILITIES)
    marketing_cost = _expense(FinancialExpense.EXPENSE_MARKETING)
    other_expenses = _expense(FinancialExpense.EXPENSE_OTHER)

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

    average_check = ZERO
    if active_orders_count > 0:
        average_check = gross_revenue / active_orders_count

    cancel_percent = 0
    if total_orders > 0:
        cancel_percent = cancelled_orders_count / total_orders * 100

    # ---- касса / банк ----
    cash_total = ZERO
    bank_total = ZERO

    for o in active:
        order_food_total = o.subtotal_amount - o.discount_amount
        order_commission = ZERO
        if o.source:
            order_commission = order_food_total * o.source.commission_percent / HUNDRED

        if o.payment_method and o.payment_method.is_cash:
            cash_total += o.total_amount
        else:
            if o.source and o.source.commission_percent > 0:
                bank_total += o.total_amount - order_commission
            else:
                bank_total += o.total_amount

    # ---- новые / повторные клиенты: ОДИН запрос вместо N+1 ----
    # "повторный" = у клиента есть НЕотменённый заказ ДО date_from (глобально
    # по стране — ровно как раньше, не в разрезе филиала).
    active_customer_ids = {o.customer_id for o in active if o.customer_id is not None}

    prior_customer_ids = set(
        Order.objects
        .filter(
            country=country,
            is_cancelled=False,
            order_date__date__lt=date_from,
            customer_id__in=active_customer_ids,
        )
        .values_list("customer_id", flat=True)
        .distinct()
    )

    returning_customers_count = len(active_customer_ids & prior_customer_ids)
    new_customers_count = len(active_customer_ids) - returning_customers_count

    latest_orders = all_orders[:10]

    # ---- сводка по филиалам (в памяти) ----
    locations_summary = []

    locations = (
        Location.objects
        .filter(country=country, is_active=True)
        .order_by("name")
    )

    for location in locations:
        loc_all = [o for o in all_orders if o.location_id == location.id]
        loc_active = [o for o in active if o.location_id == location.id]
        loc_cancelled = [o for o in cancelled if o.location_id == location.id]

        location_revenue = sum((o.total_amount for o in loc_active), ZERO)
        location_commission = sum((_commission(o) for o in loc_active), ZERO)
        location_net_revenue = location_revenue - location_commission

        location_customer_delivery = sum(
            (o.customer_delivery_amount for o in loc_active), ZERO
        )
        location_delivery_total = sum((o.delivery_amount for o in loc_active), ZERO)
        location_company_delivery = location_delivery_total - location_customer_delivery
        location_discount_loss = sum((o.discount_amount for o in loc_active), ZERO)

        location_cost = location_cost_map.get(location.id, ZERO)

        # NB: списания берём ОБЩИЕ по стране и подставляем на каждый филиал —
        # поведение сохранено 1:1 со старой версией (в исходном коде запрос
        # WriteOff не фильтровался по локации). Похоже на баг в разрезе
        # филиала — кандидат на отдельный фикс, не трогаем без ТЗ.
        location_writeoffs = writeoffs_cost

        location_profit_before_fixed = (
            location_net_revenue
            - location_cost
            - location_company_delivery
            - location_writeoffs
        )

        location_active_count = len(loc_active)
        location_average_check = ZERO
        if location_active_count > 0:
            location_average_check = location_revenue / location_active_count

        location_cash = ZERO
        location_bank = ZERO

        for o in loc_active:
            order_food_total = o.subtotal_amount - o.discount_amount
            order_commission = ZERO
            if o.source:
                order_commission = order_food_total * o.source.commission_percent / HUNDRED

            if o.payment_method and o.payment_method.is_cash:
                location_cash += o.total_amount
            else:
                if o.source and o.source.commission_percent > 0:
                    location_bank += o.total_amount - order_commission
                else:
                    location_bank += o.total_amount

        loc_customer_ids = {
            o.customer_id for o in loc_active if o.customer_id is not None
        }
        location_returning_customers = len(loc_customer_ids & prior_customer_ids)
        location_new_customers = len(loc_customer_ids) - location_returning_customers

        locations_summary.append({
            "name": location.name,

            "orders_count": len(loc_all),
            "active_orders_count": location_active_count,
            "cancelled_orders_count": len(loc_cancelled),

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

    # ---- ТОП блюд уже посчитан выше (top_dishes) из позиций в памяти ----

    # ---- сводка по методам оплаты (в памяти) ----
    payment_summary = []

    payment_methods = (
        PaymentMethod.objects
        .filter(country=country, is_active=True)
        .order_by("name")
    )

    for method in payment_methods:
        method_orders = [o for o in active if o.payment_method_id == method.id]
        payment_summary.append({
            "name": method.name,
            "orders_count": len(method_orders),
            "revenue": sum((o.total_amount for o in method_orders), ZERO),
        })

    # ---- сводка по источникам (в памяти) ----
    source_summary = []

    order_sources = (
        OrderSource.objects
        .filter(country=country, is_active=True)
        .order_by("name")
    )

    for source in order_sources:
        source_orders = [o for o in active if o.source_id == source.id]
        source_revenue = sum((o.total_amount for o in source_orders), ZERO)
        source_commission = sum(
            (o.total_amount * source.commission_percent / HUNDRED for o in source_orders),
            ZERO,
        )
        source_summary.append({
            "name": source.name,
            "orders_count": len(source_orders),
            "revenue": source_revenue,
            "commission": source_commission,
            "net_revenue": source_revenue - source_commission,
        })

    # ---- сводка по доставке (в памяти) ----
    delivery_summary = []

    delivery_providers = (
        DeliveryProvider.objects
        .filter(country=country, is_active=True)
        .order_by("name")
    )

    for provider in delivery_providers:
        provider_orders = [o for o in active if o.delivery_provider_id == provider.id]
        provider_delivery_total = sum((o.delivery_amount for o in provider_orders), ZERO)
        provider_customer_delivery = sum(
            (o.customer_delivery_amount for o in provider_orders), ZERO
        )
        delivery_summary.append({
            "name": provider.name,
            "orders_count": len(provider_orders),
            "delivery_sum": provider_delivery_total,
            "customer_delivery": provider_customer_delivery,
            "company_delivery": provider_delivery_total - provider_customer_delivery,
            "revenue": sum((o.total_amount for o in provider_orders), ZERO),
        })

    # ---- причины отмен (в памяти) ----
    cancel_reason_counts = {}
    for o in cancelled:
        name = o.cancel_reason.name if o.cancel_reason else None
        cancel_reason_counts[name] = cancel_reason_counts.get(name, 0) + 1

    cancel_reasons_stats = sorted(
        (
            {"cancel_reason__name": k, "total": v}
            for k, v in cancel_reason_counts.items()
        ),
        key=lambda r: r["total"],
        reverse=True,
    )

    # ---- по часам: ОДИН группирующий запрос (ExtractHour даёт тот же
    #      tz-срез, что прежний created_at__hour) ----
    hour_rows = (
        Order.objects
        .filter(
            country=country,
            order_date__date__gte=date_from,
            order_date__date__lte=date_to,
            is_cancelled=False,
        )
        .annotate(h=ExtractHour("order_date"))
        .values("h")
        .annotate(orders=Count("id"), revenue=Sum("total_amount"))
    )
    hour_map = {r["h"]: r for r in hour_rows}

    hourly_stats = []
    max_hour_revenue = ZERO

    for hour in range(24):
        row = hour_map.get(hour)
        hour_revenue = (row["revenue"] if row else None) or ZERO
        hour_orders_count = row["orders"] if row else 0

        if hour_revenue > max_hour_revenue:
            max_hour_revenue = hour_revenue

        hourly_stats.append({
            "hour": f"{hour:02d}:00",
            "orders": hour_orders_count,
            "revenue": hour_revenue,
        })

    for item in hourly_stats:
        percent = 0
        if max_hour_revenue > 0:
            percent = item["revenue"] / max_hour_revenue * 100
        item["percent"] = percent

    # ---- по дням: ОДИН группирующий запрос (TruncDate = тот же tz-срез,
    #      что прежний order_date__date) ----
    day_rows = (
        Order.objects
        .filter(
            country=country,
            order_date__date__gte=date_from,
            order_date__date__lte=date_to,
        )
        .annotate(d=TruncDate("order_date"))
        .values("d")
        .annotate(
            active=Count("id", filter=Q(is_cancelled=False)),
            cancelled=Count("id", filter=Q(is_cancelled=True)),
            revenue=Sum("total_amount", filter=Q(is_cancelled=False)),
        )
    )
    day_map = {r["d"]: r for r in day_rows}

    daily_stats = []
    current_day = date_from
    max_daily_revenue = ZERO

    while current_day <= date_to:
        row = day_map.get(current_day)
        day_revenue = (row["revenue"] if row else None) or ZERO

        if day_revenue > max_daily_revenue:
            max_daily_revenue = day_revenue

        daily_stats.append({
            "date": current_day,
            "orders": (row["active"] if row else 0),
            "cancelled": (row["cancelled"] if row else 0),
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

            "orders": orders_qs,
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
        UserProfile.SECTION_CUSTOMERS,
    )
    if access_error:
        return access_error

    g = request.GET
    search = g.get("search", "").strip()
    period = g.get("period", "all")
    status = g.get("status", "all")
    min_orders = g.get("min_orders", "all")
    source = g.get("source", "all")
    amount = g.get("amount", "all")
    sort = g.get("sort", "-last")

    try:
        per_page = int(g.get("per_page", 50))
    except (TypeError, ValueError):
        per_page = 50
    if per_page not in (25, 50, 100):
        per_page = 50

    now = timezone.now()
    money_field = DecimalField(max_digits=16, decimal_places=2)

    # ---- основной queryset: все агрегаты одним SQL (убирает N+1) ----
    # Все аннотации идут по ОДНОЙ связи (orders) -> один LEFT JOIN, без
    # размножения строк. site_orders / tilda_orders считаем по
    # is_legacy_import (надёжнее, чем сверять source.name строкой).
    customers = (
        Customer.objects
        .filter(country=country)
        .annotate(
            orders_count=Count("orders", distinct=True),
            total_spent=Coalesce(
                Sum("orders__total_amount"),
                Value(Decimal("0")),
                output_field=money_field,
            ),
            last_order_date=Max("orders__order_date"),
            site_orders=Count(
                "orders",
                filter=Q(orders__is_legacy_import=False),
                distinct=True,
            ),
            tilda_orders=Count(
                "orders",
                filter=Q(orders__is_legacy_import=True),
                distinct=True,
            ),
        )
    )

    if search:
        customers = customers.filter(
            Q(name__icontains=search) | Q(phone__icontains=search)
        )

    if status == "regular":
        customers = customers.filter(is_regular=True)
    elif status == "problematic":
        customers = customers.filter(is_problematic=True)
    elif status == "blocked":
        customers = customers.filter(delivery_blocked=True)
    elif status == "normal":
        customers = customers.filter(
            is_regular=False,
            is_problematic=False,
            delivery_blocked=False,
        )

    if min_orders == "none":
        customers = customers.filter(orders_count=0)
    elif min_orders == "1":
        customers = customers.filter(orders_count__gte=1)
    elif min_orders == "5":
        customers = customers.filter(orders_count__gte=5)

    if source == "site":
        customers = customers.filter(site_orders__gt=0)
    elif source == "tilda":
        customers = customers.filter(tilda_orders__gt=0)

    # период = активность: последний заказ в окне.
    # (нужна дата регистрации вместо активности? замени last_order_date__gte
    # на created_at__gte.)
    period_days = {"month": 30, "quarter": 90, "year": 365}.get(period)
    if period_days:
        customers = customers.filter(
            last_order_date__gte=now - timedelta(days=period_days)
        )

    # топ-50 по тратам (внутри уже применённых фильтров)
    if amount == "top50":
        top_ids = list(
            customers.order_by("-total_spent").values_list("id", flat=True)[:50]
        )
        customers = customers.filter(id__in=top_ids)

    # ---- сортировка (NULL по датам/суммам всегда вниз) ----
    sort_map = {
        "name":    ["name", "id"],
        "-name":   ["-name", "id"],
        "orders":  [F("orders_count").asc(nulls_last=True), "name"],
        "-orders": [F("orders_count").desc(nulls_last=True), "name"],
        "spent":   [F("total_spent").asc(nulls_last=True), "name"],
        "-spent":  [F("total_spent").desc(nulls_last=True), "name"],
        "last":    [F("last_order_date").asc(nulls_last=True), "name"],
        "-last":   [F("last_order_date").desc(nulls_last=True), "name"],
        "reg":     [F("created_at").asc(nulls_last=True), "name"],
        "-reg":    [F("created_at").desc(nulls_last=True), "name"],
    }
    if sort not in sort_map:
        sort = "-last"
    customers = customers.order_by(*sort_map[sort])

    paginator = Paginator(customers, per_page)
    page_obj = paginator.get_page(g.get("page") or 1)

    # ---- строки для шаблона (только текущая страница) ----
    rows = []
    for c in page_obj:
        oc = c.orders_count or 0
        spent = c.total_spent or Decimal("0")
        avg = (spent / oc) if oc else Decimal("0")

        if c.is_problematic:
            status_label, status_class = "Проблемный", "fc-badge-red"
        elif c.is_regular:
            status_label, status_class = "Постоянный", "fc-badge-green"
        else:
            status_label, status_class = "Обычный", "fc-badge-gray"

        source_badges = []
        if c.site_orders:
            source_badges.append({"label": "Сайт", "cls": "fc-badge-blue"})
        if c.tilda_orders:
            source_badges.append({"label": "Tilda", "cls": "fc-badge-amber"})

        rows.append({
            "id": c.id,
            "name": c.name or "Без имени",
            "phone": c.phone,
            "orders_count": oc,
            "total_display": _fmt_money(spent),
            "avg_display": _fmt_money(avg),
            "last_order_date": c.last_order_date,
            "status_label": status_label,
            "status_class": status_class,
            "blocked": c.delivery_blocked,
            "source_badges": source_badges,
        })

    # ---- KPI: фиксированное число запросов, по всей базе страны ----
    base = Customer.objects.filter(country=country)

    cust_agg = base.aggregate(
        total=Count("pk"),
        new_month=Count("pk", filter=Q(created_at__gte=now - timedelta(days=30))),
        problematic=Count("pk", filter=Q(is_problematic=True)),
    )

    order_count_sub = (
        Order.objects
        .filter(customer=OuterRef("pk"))
        .order_by()
        .values("customer")
        .annotate(c=Count("pk"))
        .values("c")
    )
    buckets = (
        base
        .annotate(oc=Coalesce(
            Subquery(order_count_sub, output_field=IntegerField()),
            Value(0),
        ))
        .aggregate(
            repeat=Count("pk", filter=Q(oc__gte=2)),
            vip=Count("pk", filter=Q(oc__gte=5)),
        )
    )

    order_agg = (
        Order.objects
        .filter(country=country)
        .aggregate(revenue=Sum("total_amount"), n=Count("pk"))
    )
    revenue = order_agg["revenue"] or Decimal("0")
    n_orders = order_agg["n"] or 0
    avg_check = (revenue / n_orders) if n_orders else Decimal("0")

    kpis = {
        "total": cust_agg["total"] or 0,
        "new_month": cust_agg["new_month"] or 0,
        "repeat": buckets["repeat"] or 0,
        "vip": buckets["vip"] or 0,
        "avg_check": _fmt_money(avg_check),
        "problematic": cust_agg["problematic"] or 0,
    }

    # ---- кликабельные заголовки-сортировки ----
    sort_links = {
        "name":   _sort_link(g, "name", sort, default_desc=False),
        "orders": _sort_link(g, "orders", sort),
        "spent":  _sort_link(g, "spent", sort),
        "last":   _sort_link(g, "last", sort),
        "reg":    _sort_link(g, "reg", sort),
    }

    # ---- chips активных фильтров (каждый ведёт на URL без этого фильтра) ----
    chips = []
    if search:
        chips.append({"label": f"Поиск: {search}", "url": _build_qs(g, drop="search")})
    period_labels = {"month": "Период: месяц", "quarter": "Период: квартал", "year": "Период: год"}
    if period in period_labels:
        chips.append({"label": period_labels[period], "url": _build_qs(g, drop="period")})
    status_labels = {
        "regular": "Статус: постоянный",
        "problematic": "Статус: проблемный",
        "blocked": "Статус: отказ в доставке",
        "normal": "Статус: обычный",
    }
    if status in status_labels:
        chips.append({"label": status_labels[status], "url": _build_qs(g, drop="status")})
    min_orders_labels = {"none": "Заказов: без заказов", "1": "Заказов: 1+", "5": "Заказов: 5+"}
    if min_orders in min_orders_labels:
        chips.append({"label": min_orders_labels[min_orders], "url": _build_qs(g, drop="min_orders")})
    source_labels = {"site": "Источник: сайт", "tilda": "Источник: Tilda"}
    if source in source_labels:
        chips.append({"label": source_labels[source], "url": _build_qs(g, drop="source")})
    if amount == "top50":
        chips.append({"label": "Топ-50 по тратам", "url": _build_qs(g, drop="amount")})

    # querystring без page — для ссылок пагинации (сохраняет фильтры/сортировку)
    pag = g.copy()
    pag.pop("page", None)
    base_qs = pag.urlencode()

    return render(request, "foodcost/customer_list.html", {
        "country": country,
        "rows": rows,
        "page_obj": page_obj,
        "base_qs": base_qs,
        "kpis": kpis,
        "sort_links": sort_links,
        "chips": chips,
        "reset_url": f"/c/{country.slug}/customers/",
        # эхо фильтров обратно в форму
        "search": search,
        "period": period,
        "status": status,
        "min_orders": min_orders,
        "source": source,
        "amount": amount,
        "sort": sort,
        "per_page": per_page,
    })