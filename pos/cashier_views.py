"""Экран кассира (зал): создание и ведение заказа на столе.

Живой экран: блюда добавляются в чек без перезагрузки (AJAX), сервер —
источник истины. Пишем в штатные foodcost.Order / OrderItem и в аддитивные
PosOrder / PosOrderItem. foodcost не меняем.

Права — секция SECTION_ORDERS (не SETTINGS). `country` кладём в контекст
страницы (его использует меню base.html).

Эндпоинты (все POST, возвращают JSON с полным состоянием заказа):
  /pos/order/                 — страница кассира (GET)
  /pos/order/api/open/        — открыть/создать заказ стола
  /pos/order/api/add/         — добавить блюдо
  /pos/order/api/qty/         — изменить количество позиции (0 — удалить)
  /pos/order/api/remove/      — удалить позицию
  /pos/order/api/send/        — отправить новые позиции на кухню
"""

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from django.utils.translation import gettext as _
from foodcost.views import get_country
from foodcost.models import UserProfile, DishCategory, Dish, Order, OrderItem

from .access import pos_require, POS_CASHIER
from .models import FloorZone, FloorTable, KitchenScreen, PosOrder, PosOrderItem

ZERO = Decimal("0")
HUNDRED = Decimal("100")


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _require_orders(request):
    """Доступ к кассе/залу (POS-право «касса»). HttpResponseForbidden или None."""
    return pos_require(request.user, POS_CASHIER)


def _int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _dec(value, default="0"):
    from decimal import InvalidOperation
    try:
        raw = str(value).replace(" ", "").replace(",", ".").strip()
        return Decimal(raw or default)
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(default)


def _tenant_tables(country):
    """Все столы арендатора (через зоны)."""
    return (
        FloorTable.objects
        .filter(zone__tenant=country, is_active=True)
        .select_related("zone")
        .order_by("zone__sort_order", "zone__id", "number")
    )


# Терминальные статусы — такой счёт уже закрыт и под стол открывается новый.
# Определяем «открытый счёт» исключением терминальных статусов, а не поиском
# одной константы «неоплачен» (в foodcost оплата бывает paid/cash/refunded,
# дефолт нового заказа — pending).
_CLOSED_ORDER_STATUSES = [Order.STATUS_DONE, Order.STATUS_CANCELLED]
_PAID_PAYMENT_STATUSES = [
    Order.PAYMENT_STATUS_PAID,
    Order.PAYMENT_STATUS_CASH,
    Order.PAYMENT_STATUS_REFUNDED,
]


def _open_pos_order_for_table(country, table):
    """Активный (не закрытый, не оплаченный) POS-заказ стола или None."""
    return (
        PosOrder.objects
        .filter(table=table, order__country=country)
        .exclude(order__status__in=_CLOSED_ORDER_STATUSES)
        .exclude(order__payment_status__in=_PAID_PAYMENT_STATUSES)
        .select_related("order", "table")
        .order_by("-id")
        .first()
    )


def _line_net(oi, pi):
    gross = (oi.price_snapshot or ZERO) * (oi.quantity or ZERO)
    disc = (pi.discount_percent if pi else ZERO) or ZERO
    if disc:
        gross = (gross * (HUNDRED - disc) / HUNDRED)
    return gross


def _recalc_order_totals(order):
    """Пересчёт сумм заказа по позициям (B1: сервис/скидки по счёту — в B3)."""
    subtotal = ZERO
    for oi in order.items.all():
        pi = getattr(oi, "pos", None)
        subtotal += _line_net(oi, pi)
    order.subtotal_amount = subtotal
    order.total_amount = subtotal
    order.save(update_fields=["subtotal_amount", "total_amount"])
    return subtotal


def _order_payload(pos_order):
    order = pos_order.order
    items = []
    subtotal = ZERO
    for oi in order.items.select_related("dish").all():
        pi = getattr(oi, "pos", None)
        net = _line_net(oi, pi)
        subtotal += net
        sent = bool(pi and pi.sent_to_kitchen_at)
        items.append({
            "id": oi.id,
            "name": oi.dish_name_snapshot or (oi.dish.name if oi.dish else "—"),
            "qty": float(oi.quantity or 0),
            "price": float(oi.price_snapshot or 0),
            "line_total": float(net),
            "kitchen_status": (pi.kitchen_status if pi else ""),
            "kitchen_status_display": (pi.get_kitchen_status_display() if pi else ""),
            "sent": sent,
            "discount_percent": float(pi.discount_percent) if pi else 0,
        })
    return {
        "order_id": order.id,
        "table_id": pos_order.table_id,
        "table_number": (pos_order.table.number if pos_order.table_id else ""),
        "guests": pos_order.guests,
        "items": items,
        "subtotal": float(subtotal),
        "has_unsent": any(not it["sent"] for it in items),
    }


def _assign_screen(country, dish):
    """Подобрать экран KDS для блюда: прямая привязка блюда к экрану, иначе по
    его категории (Dish.category)."""
    if dish is None:
        return None
    qs = KitchenScreen.objects.filter(tenant=country, is_active=True)
    screen = qs.filter(dishes=dish).order_by("sort_order", "id").first()
    if screen:
        return screen
    if dish.category_id:
        screen = qs.filter(categories__id=dish.category_id).order_by("sort_order", "id").first()
        if screen:
            return screen
    return None


# --------------------------------------------------------------------------- #
# page
# --------------------------------------------------------------------------- #

@login_required(login_url="/login/")
def cashier(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = _require_orders(request)
    if access_error:
        return access_error

    categories = list(
        DishCategory.objects.filter(country=country, show_in_pos=True).order_by("name")
    )
    dishes = list(
        Dish.objects.filter(country=country).order_by("name")
    )
    tables = list(_tenant_tables(country))

    # ?table=<id> — с какого стола пришли (из плана зала); JS откроет заказ.
    table_id = _int(request.GET.get("table"), 0) or ""

    # блюда с id категории для группировки/фильтра в разметке
    dish_rows = [{"obj": d, "cat_id": d.category_id or 0} for d in dishes]

    return render(request, "pos/cashier.html", {
        "country": country,
        "categories": categories,
        "dishes": dishes,
        "dish_rows": dish_rows,
        "tables": tables,
        "preselect_table": table_id,
    })


# --------------------------------------------------------------------------- #
# api
# --------------------------------------------------------------------------- #

@login_required(login_url="/login/")
@require_POST
def api_open(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = _require_orders(request)
    if access_error:
        return access_error

    table = get_object_or_404(
        FloorTable, id=_int(request.POST.get("table_id")), zone__tenant=country,
    )
    pos_order = _open_pos_order_for_table(country, table)
    if pos_order is None:
        with transaction.atomic():
            order = Order.objects.create(
                country=country,
                location=(table.zone.location if table.zone.location_id else None),
                customer=None,
                created_by=request.user,
                order_date=timezone.now(),
                customer_name="",
                customer_phone="",
                delivery_address="",
                fulfillment_method=Order.FULFILLMENT_PICKUP,
                subtotal_amount=0,
                total_amount=0,
            )
            guests = _int(request.POST.get("guests"), 1) or 1
            pos_order = PosOrder.objects.create(
                order=order, is_dine_in=True, table=table, guests=guests,
            )
    return JsonResponse(_order_payload(pos_order))


def _get_pos_order(country, order_id):
    return get_object_or_404(
        PosOrder.objects.select_related("order", "table"),
        order_id=_int(order_id), order__country=country,
    )


@login_required(login_url="/login/")
@require_POST
def api_add(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = _require_orders(request)
    if access_error:
        return access_error

    pos_order = _get_pos_order(country, request.POST.get("order_id"))
    order = pos_order.order
    dish = get_object_or_404(Dish, id=_int(request.POST.get("dish_id")), country=country)

    with transaction.atomic():
        # Если такое блюдо уже есть в чеке и ещё НЕ отправлено на кухню —
        # увеличиваем количество в той же строке, а не плодим строки.
        existing = None
        for oi in order.items.filter(dish=dish):
            pi = getattr(oi, "pos", None)
            if pi and pi.sent_to_kitchen_at:
                continue
            existing = oi
            break

        if existing is not None:
            existing.quantity = (existing.quantity or ZERO) + Decimal("1")
            existing.total_price = (existing.price_snapshot or ZERO) * existing.quantity
            existing.save(update_fields=["quantity", "total_price"])
        else:
            price = dish.selling_price or ZERO
            oi = OrderItem.objects.create(
                order=order, dish=dish, quantity=Decimal("1"),
                price_snapshot=price, cost_snapshot=dish.calculate_cost(),
                total_price=price, dish_name_snapshot=dish.name,
            )
            PosOrderItem.objects.create(order_item=oi, course=1)

        _recalc_order_totals(order)

    return JsonResponse(_order_payload(pos_order))


@login_required(login_url="/login/")
@require_POST
def api_qty(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = _require_orders(request)
    if access_error:
        return access_error

    oi = get_object_or_404(
        OrderItem, id=_int(request.POST.get("item_id")), order__country=country,
    )
    pos_order = _get_pos_order(country, oi.order_id)
    pi = getattr(oi, "pos", None)
    if pi and pi.sent_to_kitchen_at:
        return JsonResponse(
            {"error": _("Позиция уже отправлена на кухню — изменить нельзя.")}, status=400,
        )

    qty = _dec(request.POST.get("qty"), "0")
    with transaction.atomic():
        if qty <= ZERO:
            oi.delete()
        else:
            oi.quantity = qty
            oi.total_price = (oi.price_snapshot or ZERO) * qty
            oi.save(update_fields=["quantity", "total_price"])
        _recalc_order_totals(pos_order.order)

    return JsonResponse(_order_payload(pos_order))


@login_required(login_url="/login/")
@require_POST
def api_remove(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = _require_orders(request)
    if access_error:
        return access_error

    oi = get_object_or_404(
        OrderItem, id=_int(request.POST.get("item_id")), order__country=country,
    )
    pos_order = _get_pos_order(country, oi.order_id)
    pi = getattr(oi, "pos", None)
    if pi and pi.sent_to_kitchen_at:
        return JsonResponse(
            {"error": _("Позиция уже отправлена на кухню — снять нельзя.")}, status=400,
        )
    with transaction.atomic():
        oi.delete()
        _recalc_order_totals(pos_order.order)
    return JsonResponse(_order_payload(pos_order))


@login_required(login_url="/login/")
@require_POST
def api_send(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = _require_orders(request)
    if access_error:
        return access_error

    pos_order = _get_pos_order(country, request.POST.get("order_id"))
    order = pos_order.order
    now = timezone.now()
    sent = 0
    with transaction.atomic():
        for oi in order.items.select_related("dish").all():
            pi = getattr(oi, "pos", None)
            if pi is None:
                pi = PosOrderItem.objects.create(order_item=oi, course=1)
            if pi.sent_to_kitchen_at:
                continue
            pi.sent_to_kitchen_at = now
            if pi.screen_id is None:
                screen = _assign_screen(country, oi.dish)
                if screen:
                    pi.screen = screen
            pi.save(update_fields=["sent_to_kitchen_at", "screen"])
            sent += 1
        if sent and order.status == Order.STATUS_NEW:
            order.status = Order.STATUS_COOKING
            order.save(update_fields=["status"])

    payload = _order_payload(pos_order)
    payload["sent_count"] = sent
    return JsonResponse(payload)
