"""Uzum Tezkor (Retail API) — серверная часть. Этап 1: OAuth2 + каталог + доступность.

Uzum опрашивает НАС по своему контракту. Здесь реализованы:
  • POST  /security/oauth/token              — выдача токена (client_credentials)
  • GET   /v1/nomenclature/{storeId}/composition   — каталог (категории + товары)
  • GET   /v1/nomenclature/{storeId}/availability  — доступность (стоки)

storeId = id нашей точки (Location). Приём заказов — Этап 2.
Ошибки отдаём списком [{code, description}] по их схеме ErrorListV1.
"""

import json
import secrets
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import (
    Location, DishCategory, Dish, UzumApp,
    Order, OrderItem, OrderSource, PaymentMethod,
)

# Актуальная версия модели каталога по спецификации Uzum.
NOMENCLATURE_CT = "application/vnd.eda.picker.nomenclature.v1+json"


def _err(code, description, status):
    """Ответ-ошибка в формате ErrorListV1: массив [{code, description}]."""
    return JsonResponse(
        [{"code": code, "description": description}],
        status=status, safe=False,
    )


def _bearer_app(request):
    """UzumApp по токену из заголовка Authorization: Bearer, иначе None."""
    auth = request.META.get("HTTP_AUTHORIZATION", "") or ""
    if not auth.lower().startswith("bearer "):
        return None
    token = auth[7:].strip()
    if not token:
        return None
    return UzumApp.objects.filter(access_token=token, is_active=True).first()


def _store(store_id):
    try:
        return Location.objects.filter(id=int(store_id)).first()
    except (TypeError, ValueError):
        return None


def _dish_price(dish):
    """Цена для Uzum: своя uzum_price, иначе selling_price."""
    price = dish.uzum_price if dish.uzum_price is not None else dish.selling_price
    try:
        return float(price or 0)
    except (TypeError, ValueError):
        return 0.0


@csrf_exempt
def oauth_token(request):
    """OAuth2 client_credentials: выдаём access_token по client_id/secret."""
    if request.method != "POST":
        return _err(405, "Method not allowed", 405)

    client_id = (request.POST.get("client_id") or "").strip()
    client_secret = (request.POST.get("client_secret") or "").strip()
    grant_type = (request.POST.get("grant_type") or "").strip()

    if grant_type and grant_type != "client_credentials":
        return _err(400, "Unsupported grant_type", 400)

    app = UzumApp.objects.filter(client_id=client_id, is_active=True).first()
    if not app or app.client_secret != client_secret:
        return _err(401, "Invalid client credentials", 401)

    token = secrets.token_urlsafe(48)
    app.access_token = token
    app.token_issued_at = timezone.now()
    app.save(update_fields=["access_token", "token_issued_at"])

    return JsonResponse({
        "access_token": token,
        "token_type": "bearer",
        "scope": "read write",
    })


def composition(request, store_id):
    """Каталог точки: категории + товары в схеме PickerNomenclatureV1."""
    app = _bearer_app(request)
    if not app:
        return _err(401, "Access token missing or expired", 401)

    store = _store(store_id)
    if not store:
        return _err(404, "Restaurant not found", 404)

    country = store.country

    categories = [
        {
            "id": str(c.id),
            "name": (c.public_name or c.name or "").strip() or c.name,
            "sortOrder": int(c.site_sort_order or 0),
        }
        for c in DishCategory.objects.filter(country=country).order_by(
            "site_sort_order", "name"
        )
    ]

    items = []
    dishes = (
        Dish.objects.filter(country=country, is_archived=False)
        .select_related("category")
    )
    for d in dishes:
        if not d.category_id:
            continue
        price = _dish_price(d)
        if price <= 0:
            continue  # позиции с нулевой ценой Uzum отбрасывает

        grams = int(round(float(d.final_weight or 0) * 1000))
        if grams <= 0:
            grams = 1

        item = {
            "id": str(d.id),
            "categoryId": str(d.category_id),
            "name": (d.public_name or d.name or "").strip() or d.name,
            "description": {"general": (d.public_name or d.name or "").strip()},
            "images": [],
            "isCatchWeight": False,
            "measure": {"unit": "GRM", "value": grams, "quantum": 1},
            "price": price,
            "vendorCode": str(d.id),
            "barcode": {"value": str(d.id), "weightEncoding": "none"},
        }
        try:
            if d.old_price and float(d.old_price) > price:
                item["oldPrice"] = float(d.old_price)
        except (TypeError, ValueError):
            pass
        # Фискальные коды — заводим, но передаём только если заполнены.
        mxik = (getattr(d, "mxik_code", "") or "").strip()
        if mxik:
            sc = {"mxikCodeUz": mxik}
            pkg = (getattr(d, "package_code", "") or "").strip()
            if pkg:
                sc["packageCodeUz"] = pkg
            item["serviceCodesUz"] = sc

        items.append(item)

    return JsonResponse(
        {"categories": categories, "items": items},
        content_type=NOMENCLATURE_CT,
    )


def availability(request, store_id):
    """Доступность: {items:[{id, stock}]}. Нет в списке → недоступно.

    Точка выключена для Uzum (uzum_enabled=False) → пустой список = всё недоступно.
    Блюдо недоступно, если общий стоп, стоп Uzum, скрыто или цена 0.
    """
    app = _bearer_app(request)
    if not app:
        return _err(401, "Access token missing or expired", 401)

    store = _store(store_id)
    if not store:
        return _err(404, "Restaurant not found", 404)

    items = []
    if store.uzum_enabled:
        dishes = Dish.objects.filter(country=store.country, is_archived=False)
        for d in dishes:
            if not d.category_id:
                continue
            if _dish_price(d) <= 0:
                continue
            available = not (
                d.is_stop_list or d.uzum_stop or not d.is_visible_on_site
            )
            items.append({"id": str(d.id), "stock": 999 if available else 0})

    return JsonResponse({"items": items})


# ===================== ЗАКАЗЫ (Этап 2) =====================

# Наш статус заказа -> статус Uzum. «new» = «Принят» (заказ уже в системе),
# поэтому сразу отдаём ACCEPTED_BY_RESTAURANT (иначе Uzum отменит за 15 мин).
_STATUS_TO_UZUM = {
    Order.STATUS_NEW: "ACCEPTED_BY_RESTAURANT",
    Order.STATUS_COOKING: "COOKING",
    Order.STATUS_READY: "READY",
    Order.STATUS_DELIVERY: "TAKEN_BY_COURIER",
    Order.STATUS_DONE: "DELIVERED",
    Order.STATUS_CANCELLED: "CANCELLED",
    Order.STATUS_AWAITING_PAYMENT: "NEW",
    Order.STATUS_PAYMENT_FAILED: "CANCELLED",
}


def _uzum_status(order):
    if getattr(order, "is_cancelled", False):
        return "CANCELLED"
    return _STATUS_TO_UZUM.get(order.status, "NEW")


def _dec(value):
    try:
        return Decimal(str(value if value is not None else 0))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _find_order(order_id):
    """Заказ по нашему orderId (pk), либо public_order_number, либо eatsId."""
    oid = str(order_id).strip()
    order = None
    if oid.isdigit():
        order = Order.objects.filter(pk=int(oid)).first()
    if order is None:
        order = Order.objects.filter(public_order_number=oid).first()
    if order is None:
        order = Order.objects.filter(uzum_eats_id=oid).first()
    return order


def _order_payload(request):
    try:
        return json.loads((request.body or b"{}").decode("utf-8") or "{}")
    except Exception:  # noqa: BLE001
        return None


@csrf_exempt
def order_create(request):
    """POST /order — приём заказа от Uzum. Идемпотентно по eatsId."""
    if request.method != "POST":
        return _err(405, "Method not allowed", 405)
    app = _bearer_app(request)
    if not app:
        return _err(401, "Access token missing or expired", 401)

    data = _order_payload(request)
    if data is None:
        return _err(400, "Bad JSON", 400)

    eats_id = str(data.get("eatsId") or "").strip()
    if not eats_id:
        return _err(400, "eatsId is required", 400)

    # Идемпотентность: повтор того же заказа -> 200 + тот же orderId.
    existing = Order.objects.filter(uzum_eats_id=eats_id).first()
    if existing:
        return JsonResponse(
            {"orderId": str(existing.pk), "eatsId": eats_id, "result": "OK"}
        )

    store = _store(data.get("restaurantId"))
    if not store:
        return _err(404, "Restaurant not found", 404)
    country = store.country

    raw_items = data.get("items") or []
    if not raw_items:
        return _err(400, "items is required", 400)

    payment_info = data.get("paymentInfo") or {}
    pay_type = str(payment_info.get("paymentType") or "").upper()
    items_cost = payment_info.get("itemsCost")
    delivery = data.get("deliveryInfo") or {}

    source, _ = OrderSource.objects.get_or_create(
        country=country, name="Uzum Tezkor"
    )
    pay_method = PaymentMethod.objects.filter(
        country=country, is_cash=(pay_type == "CASH")
    ).first()

    try:
        with transaction.atomic():
            order = Order(
                country=country,
                location=store,
                source=source,
                payment_method=pay_method,
                uzum_eats_id=eats_id,
                status=Order.STATUS_NEW,
                payment_status=(
                    Order.PAYMENT_STATUS_PAID if pay_type == "CARD"
                    else Order.PAYMENT_STATUS_CASH
                ),
                order_date=timezone.now(),
                customer_name=str(delivery.get("clientName") or "Uzum")[:255],
                customer_phone=str(
                    delivery.get("clientPhoneNumber")
                    or delivery.get("phoneNumber") or ""
                )[:30],
                delivery_address="",
                customer_comment=str(data.get("comment") or ""),
                fulfillment_method=Order.FULFILLMENT_DELIVERY,
                subtotal_amount=_dec(items_cost),
                total_amount=_dec(items_cost),
            )
            order.save()

            total = Decimal(0)
            for it in raw_items:
                dish = Dish.objects.filter(
                    id=_to_int(it.get("id")), country=country
                ).first()
                if not dish:
                    continue
                qty = _dec(it.get("quantity")) or Decimal(1)
                price = _dec(it.get("price"))
                line = price * qty
                OrderItem.objects.create(
                    order=order, dish=dish, quantity=qty,
                    price_snapshot=price,
                    cost_snapshot=getattr(dish, "cached_total_cost", 0) or 0,
                    total_price=line,
                )
                total += line

            if not items_cost:
                order.subtotal_amount = total
                order.total_amount = total
                order.save(update_fields=["subtotal_amount", "total_amount"])

            if not (order.public_order_number or "").strip():
                order.public_order_number = f"UZ-{order.pk}"
                order.save(update_fields=["public_order_number"])
    except Exception as exc:  # noqa: BLE001
        return _err(500, f"Order create failed: {exc}", 500)

    return JsonResponse(
        {"orderId": str(order.pk), "eatsId": eats_id, "result": "OK"}
    )


@csrf_exempt
def order_detail(request, order_id):
    """GET — детали заказа; PUT — обновление (для магазинов обычно не исп.);
    DELETE — отмена заказа со стороны Uzum."""
    app = _bearer_app(request)
    if not app:
        return _err(401, "Access token missing or expired", 401)

    order = _find_order(order_id)
    if order is None:
        return _err(404, "Order not found", 404)

    if request.method == "DELETE":
        data = _order_payload(request) or {}
        comment = str(data.get("comment") or "")
        with transaction.atomic():
            locked = Order.objects.select_for_update().get(pk=order.pk)
            locked.status = Order.STATUS_CANCELLED
            if hasattr(locked, "is_cancelled"):
                locked.is_cancelled = True
            fields = ["status"]
            if hasattr(locked, "is_cancelled"):
                fields.append("is_cancelled")
            if comment and hasattr(locked, "cashier_comment"):
                locked.cashier_comment = (
                    (locked.cashier_comment or "") + f"\n[Uzum отмена] {comment}"
                ).strip()
                fields.append("cashier_comment")
            locked.save(update_fields=fields)
        return JsonResponse({"result": "OK"}, status=200)

    if request.method == "PUT":
        # Обновление со стороны Uzum для магазинов обычно отключено.
        # Принимаем и подтверждаем без изменений состава.
        return JsonResponse(
            {"orderId": str(order.pk), "eatsId": order.uzum_eats_id, "result": "OK"}
        )

    # GET — представление заказа (для сверки состава при обновлениях).
    items = []
    for oi in order.items.select_related("dish").all():
        if not oi.dish_id:
            continue
        items.append({
            "id": str(oi.dish_id),
            "name": (oi.dish.public_name or oi.dish.name or ""),
            "price": float(oi.price_snapshot or 0),
            "quantity": float(oi.quantity or 0),
            "modifications": [],
            "promos": {"discounts": []},
        })
    pay_type = "CARD" if order.payment_status == Order.PAYMENT_STATUS_PAID else "CASH"
    return JsonResponse({
        "discriminator": "uzum",
        "eatsId": order.uzum_eats_id,
        "comment": order.customer_comment or "",
        "items": items,
        "paymentInfo": {
            "itemsCost": float(order.total_amount or 0),
            "paymentType": pay_type,
        },
        "restaurantId": str(order.location_id or ""),
        "status": _uzum_status(order),
    })


def order_status(request, order_id):
    """GET /order/{orderId}/status — текущий статус заказа в формате Uzum."""
    app = _bearer_app(request)
    if not app:
        return _err(401, "Access token missing or expired", 401)

    order = _find_order(order_id)
    if order is None:
        return _err(404, "Order not found", 404)

    updated = getattr(order, "updated_at", None) or getattr(order, "order_date", None)
    payload = {"status": _uzum_status(order)}
    if updated is not None:
        payload["updatedAt"] = updated.isoformat()
    return JsonResponse(payload)
