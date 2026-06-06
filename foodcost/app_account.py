"""
👤 Личный кабинет приложения: профиль, история заказов, адреса.

Все эндпоинты требуют Bearer-токен (см. app_auth._authenticate). Работают
поверх существующих моделей Customer / CustomerAddress / Order — ничего не
дублируем.

Маршруты (см. urls.py):
  GET/POST  /api/app/profile
  GET       /api/app/orders                       (?limit=&offset=)
  GET       /api/app/orders/<public_order_number>
  GET/POST  /api/app/addresses
  GET/POST/PUT/PATCH/DELETE  /api/app/addresses/<id>
"""

from decimal import Decimal, InvalidOperation

from django.views.decorators.csrf import csrf_exempt

from .models import CustomerAddress, Location, Order
from .public_api import (
    api_success,
    api_error,
    _parse_json_body,
    _serialize_order_for_tracking,
    _status_label,
    _to_float,
)
from .app_auth import _authenticate


# ---------------------------------------------------------------------------
# serializers
# ---------------------------------------------------------------------------
def _serialize_customer(c):
    return {
        "id": c.id,
        "phone": c.phone or "",
        "name": c.name or "",
        "telegram": c.telegram or "",
    }


def _serialize_order_brief(o):
    return {
        "order_number": o.public_order_number or "",
        "status": o.status,
        "status_label": _status_label(o.status),
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "total": _to_float(o.total_amount),
        "payment_status": o.payment_status or "",
        "fulfillment_method": o.fulfillment_method or "",
        "items_count": o.items.count(),
    }


def _serialize_address(a):
    return {
        "id": a.id,
        "address": a.address or "",
        "comment": a.comment or "",
        "is_default": bool(a.is_default),
        "apartment": a.apartment or "",
        "entrance": a.entrance or "",
        "floor": a.floor or "",
        "intercom": a.intercom or "",
        "landmark": a.landmark or "",
        "courier_comment": a.courier_comment or "",
        "latitude": float(a.latitude) if a.latitude is not None else None,
        "longitude": float(a.longitude) if a.longitude is not None else None,
        "location_id": a.location_id,
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------
@csrf_exempt
def profile(request):
    customer, _row, err = _authenticate(request)
    if err:
        return err

    if request.method == "GET":
        return api_success(_serialize_customer(customer))

    if request.method in ("POST", "PUT", "PATCH"):
        payload, perr = _parse_json_body(request)
        if perr:
            return perr
        fields = []
        if "name" in payload:
            customer.name = str(payload.get("name") or "").strip()[:255]
            fields.append("name")
        if "telegram" in payload:
            customer.telegram = str(payload.get("telegram") or "").strip()[:120]
            fields.append("telegram")
        if fields:
            customer.save(update_fields=fields)
        return api_success(_serialize_customer(customer))

    return api_error("METHOD_NOT_ALLOWED", "Метод не поддерживается", status=405)


# ---------------------------------------------------------------------------
# orders history
# ---------------------------------------------------------------------------
@csrf_exempt
def orders_list(request):
    customer, _row, err = _authenticate(request)
    if err:
        return err
    if request.method != "GET":
        return api_error("METHOD_NOT_ALLOWED", "Метод не поддерживается", status=405)

    try:
        limit = min(max(int(request.GET.get("limit", 20)), 1), 50)
    except (TypeError, ValueError):
        limit = 20
    try:
        offset = max(int(request.GET.get("offset", 0)), 0)
    except (TypeError, ValueError):
        offset = 0

    qs = Order.objects.filter(customer=customer).order_by("-created_at")
    total = qs.count()
    rows = list(qs[offset:offset + limit])
    return api_success({
        "orders": [_serialize_order_brief(o) for o in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    })


@csrf_exempt
def order_detail(request, public_order_number):
    customer, _row, err = _authenticate(request)
    if err:
        return err
    if request.method != "GET":
        return api_error("METHOD_NOT_ALLOWED", "Метод не поддерживается", status=405)

    number = (public_order_number or "").strip()
    order = (
        Order.objects.filter(customer=customer, public_order_number=number)
        .select_related("payment_method")
        .first()
    )
    if order is None:
        return api_error("NOT_FOUND", "Заказ не найден", status=404)
    return api_success(_serialize_order_for_tracking(order))


# ---------------------------------------------------------------------------
# addresses
# ---------------------------------------------------------------------------
_ADDR_FIELD_MAX = {
    "comment": 255,
    "apartment": 50,
    "entrance": 50,
    "floor": 50,
    "intercom": 50,
    "landmark": 255,
    "courier_comment": 255,
}


def _apply_address_fields(addr, payload, customer):
    for field, maxlen in _ADDR_FIELD_MAX.items():
        if field in payload:
            setattr(addr, field, str(payload.get(field) or "")[:maxlen])
    for coord in ("latitude", "longitude"):
        if coord in payload:
            raw = payload.get(coord)
            if raw in (None, ""):
                setattr(addr, coord, None)
            else:
                try:
                    setattr(addr, coord, Decimal(str(raw)))
                except (InvalidOperation, TypeError, ValueError):
                    pass
    if "location_id" in payload:
        loc_id = payload.get("location_id")
        if loc_id in (None, "", 0):
            addr.location = None
        else:
            addr.location = Location.objects.filter(
                id=loc_id, country=customer.country
            ).first()


def _maybe_set_default(customer, addr, payload):
    if bool(payload.get("is_default")):
        customer.addresses.exclude(id=addr.id).update(is_default=False)
        if not addr.is_default:
            addr.is_default = True
            addr.save(update_fields=["is_default"])


@csrf_exempt
def addresses(request):
    customer, _row, err = _authenticate(request)
    if err:
        return err

    if request.method == "GET":
        rows = customer.addresses.order_by("-is_default", "-created_at")
        return api_success({"addresses": [_serialize_address(a) for a in rows]})

    if request.method == "POST":
        payload, perr = _parse_json_body(request)
        if perr:
            return perr
        text = str(payload.get("address") or "").strip()
        if not text:
            return api_error("INVALID_REQUEST", "Укажите адрес", status=400)
        addr = CustomerAddress(customer=customer, address=text[:2000])
        _apply_address_fields(addr, payload, customer)
        addr.save()
        _maybe_set_default(customer, addr, payload)
        return api_success(_serialize_address(addr), status=201)

    return api_error("METHOD_NOT_ALLOWED", "Метод не поддерживается", status=405)


@csrf_exempt
def address_detail(request, address_id):
    customer, _row, err = _authenticate(request)
    if err:
        return err

    addr = customer.addresses.filter(id=address_id).first()
    if addr is None:
        return api_error("NOT_FOUND", "Адрес не найден", status=404)

    if request.method == "GET":
        return api_success(_serialize_address(addr))

    if request.method in ("POST", "PUT", "PATCH"):
        payload, perr = _parse_json_body(request)
        if perr:
            return perr
        if "address" in payload:
            text = str(payload.get("address") or "").strip()
            if not text:
                return api_error("INVALID_REQUEST", "Адрес не может быть пустым", status=400)
            addr.address = text[:2000]
        _apply_address_fields(addr, payload, customer)
        addr.save()
        _maybe_set_default(customer, addr, payload)
        return api_success(_serialize_address(addr))

    if request.method == "DELETE":
        addr.delete()
        return api_success({"ok": True})

    return api_error("METHOD_NOT_ALLOWED", "Метод не поддерживается", status=405)
