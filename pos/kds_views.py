"""Экран кухни (KDS): доска тикетов, таймеры по порогам, отметка готовности.

Логика цикла (как договорились): позиция видна на доске, пока не готов ВЕСЬ
заказ. «Готово» по позиции помечает её, но тикет остаётся на экране, пока не
готовы все его позиции; когда готовы все — тикет пропадает, а заказ переходит
в статус «готов» (Order.STATUS_READY).

Читает аддитивные PosOrderItem (у которых проставлен sent_to_kitchen_at) и
штатные Order/OrderItem. foodcost не меняется. Права — секция SECTION_ORDERS.

Эндпоинты:
  /pos/kds/                 — страница доски (GET)
  /pos/kds/api/board/       — состояние доски JSON (POST, для опроса)
  /pos/kds/api/ready/       — пометить позицию готовой (POST)
  /pos/kds/api/undo/        — снять готовность (POST)
"""

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from django.utils.translation import gettext as _
from foodcost.views import get_country

from .access import pos_require, POS_KDS
from .models import KitchenScreen, KitchenTimeThreshold, PosOrderItem

DEFAULT_WARN_MIN = 8
DEFAULT_LATE_MIN = 15


def _require_orders(request):
    """Доступ к кухне (POS-право «KDS»). HttpResponseForbidden или None."""
    return pos_require(request.user, POS_KDS)


def _int(value, default=0):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _thresholds_for(country):
    return list(KitchenTimeThreshold.objects.filter(tenant=country))


def _pick_threshold(thresholds, item_screen_id, dish):
    """Самый специфичный порог: блюдо > категория > экран(общий) > дефолт.
    Порог экрана учитывается, только если его screen совпадает с экраном
    позиции (или порог общий, screen=null)."""
    cat_id = dish.category_id if dish else None
    dish_id = dish.id if dish else None
    best = None
    best_rank = -1
    for t in thresholds:
        if t.screen_id is not None and t.screen_id != item_screen_id:
            continue
        if t.dish_id is not None:
            if t.dish_id != dish_id:
                continue
            rank = 3
        elif t.category_id is not None:
            if t.category_id != cat_id:
                continue
            rank = 2
        elif t.screen_id is not None:
            rank = 1
        else:
            rank = 0
        if rank > best_rank:
            best = t
            best_rank = rank
    if best is not None:
        return best.warn_minutes, best.late_minutes
    return DEFAULT_WARN_MIN, DEFAULT_LATE_MIN


def _build_board(country, screen_id):
    """Список тикетов (заказов) с неготовыми позициями для выбранного экрана.
    screen_id = 0/None -> все экраны."""
    thresholds = _thresholds_for(country)
    now = timezone.now()

    qs = (
        PosOrderItem.objects
        .filter(
            order_item__order__country=country,
            sent_to_kitchen_at__isnull=False,
        )
        .exclude(kitchen_status=PosOrderItem.KS_CANCELLED)
        .select_related(
            "order_item", "order_item__order", "order_item__dish", "screen",
        )
    )
    if screen_id:
        qs = qs.filter(screen_id=screen_id)

    grouped = {}
    for pi in qs:
        order = pi.order_item.order
        grouped.setdefault(order.id, {"order": order, "items": []})["items"].append(pi)

    tickets = []
    for oid, info in grouped.items():
        items = info["items"]
        if all(pi.kitchen_status == PosOrderItem.KS_READY for pi in items):
            continue  # весь тикет готов — не показываем
        order = info["order"]
        pos_order = getattr(order, "pos", None)
        earliest = min(pi.sent_to_kitchen_at for pi in items)

        line_items = []
        for pi in items:
            oi = pi.order_item
            warn_min, late_min = _pick_threshold(thresholds, pi.screen_id, oi.dish)
            line_items.append({
                "id": pi.id,
                "name": oi.dish_name_snapshot or (oi.dish.name if oi.dish else "—"),
                "qty": float(oi.quantity or 0),
                "ready": pi.kitchen_status == PosOrderItem.KS_READY,
                "cook_seconds": pi.cook_seconds or 0,
                "warn_seconds": warn_min * 60,
                "late_seconds": late_min * 60,
            })
        line_items.sort(key=lambda x: (x["ready"], x["name"]))

        tickets.append({
            "order_id": oid,
            "table": (pos_order.table.number if pos_order and pos_order.table_id else ""),
            "guests": (pos_order.guests if pos_order else None),
            "public_number": order.public_order_number or "",
            "elapsed": int((now - earliest).total_seconds()),
            "items": line_items,
        })

    tickets.sort(key=lambda t: -t["elapsed"])
    return tickets


def _screens(country):
    return list(
        KitchenScreen.objects.filter(tenant=country, is_active=True)
        .order_by("sort_order", "id")
    )


# --------------------------------------------------------------------------- #

@login_required(login_url="/login/")
def kds(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = _require_orders(request)
    if access_error:
        return access_error
    screens = _screens(country)
    screen_id = _int(request.GET.get("screen"), 0)
    return render(request, "pos/kds.html", {
        "country": country,
        "screens": screens,
        "preselect_screen": screen_id or "",
    })


@login_required(login_url="/login/")
@require_POST
def api_board(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = _require_orders(request)
    if access_error:
        return access_error
    screen_id = _int(request.POST.get("screen"), 0)
    tickets = _build_board(country, screen_id)
    return JsonResponse({"screen": screen_id or 0, "tickets": tickets})


def _set_order_ready_if_done(order):
    """Если ВСЕ отправленные позиции заказа готовы — переводим заказ в «готов»."""
    sent = [
        pi for pi in PosOrderItem.objects.filter(
            order_item__order=order, sent_to_kitchen_at__isnull=False,
        ).exclude(kitchen_status=PosOrderItem.KS_CANCELLED)
    ]
    if sent and all(pi.kitchen_status == PosOrderItem.KS_READY for pi in sent):
        if order.status != order.STATUS_READY:
            order.status = order.STATUS_READY
            order.save(update_fields=["status"])
        return True
    return False


@login_required(login_url="/login/")
@require_POST
def api_ready(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = _require_orders(request)
    if access_error:
        return access_error
    pi = get_object_or_404(
        PosOrderItem.objects.select_related("order_item__order"),
        id=_int(request.POST.get("item_id")),
        order_item__order__country=country,
    )
    with transaction.atomic():
        pi.kitchen_status = PosOrderItem.KS_READY
        if pi.ready_at is None:
            pi.ready_at = timezone.now()
        pi.save(update_fields=["kitchen_status", "ready_at"])
        _set_order_ready_if_done(pi.order_item.order)

    screen_id = _int(request.POST.get("screen"), 0)
    return JsonResponse({"screen": screen_id or 0, "tickets": _build_board(country, screen_id)})


@login_required(login_url="/login/")
@require_POST
def api_undo(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = _require_orders(request)
    if access_error:
        return access_error
    pi = get_object_or_404(
        PosOrderItem.objects.select_related("order_item__order"),
        id=_int(request.POST.get("item_id")),
        order_item__order__country=country,
    )
    with transaction.atomic():
        pi.kitchen_status = PosOrderItem.KS_ACCEPTED
        pi.ready_at = None
        pi.save(update_fields=["kitchen_status", "ready_at"])
        # заказ больше не «готов» — возвращаем в «готовится»
        order = pi.order_item.order
        if order.status == order.STATUS_READY:
            order.status = order.STATUS_COOKING
            order.save(update_fields=["status"])

    screen_id = _int(request.POST.get("screen"), 0)
    return JsonResponse({"screen": screen_id or 0, "tickets": _build_board(country, screen_id)})
