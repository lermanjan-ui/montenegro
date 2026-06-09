"""Octo (octo.uz) — онлайн-оплата, Этап 1: оплата через платёжную страницу Octo.

Поток как у Click/Payme:
  build_octo_payment_url(order) -> prepare_payment -> octo_pay_url (редирект клиента)
  Octo шлёт уведомление на notify_url -> public_api.octo_callback помечает заказ.

Карты НЕ храним — это Этап 2 (токенизация). HTTP — через urllib.
"""

import hashlib
import json
import urllib.request
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

PREPARE_URL = "https://secure.octo.uz/prepare_payment"


class OctoConfigError(Exception):
    """Конфигурация Octo отсутствует или prepare_payment вернул ошибку."""


def _cfg(name, default=""):
    return getattr(settings, name, default) or default


def octo_configured():
    return bool(
        str(_cfg("OCTO_SHOP_ID")).strip()
        and str(_cfg("OCTO_SECRET")).strip()
    )


def _shop_transaction_id(order):
    # Тот же идентификатор, что Click кладёт в transaction_param — публичный номер
    # заказа (стабильный). По нему уведомление найдёт заказ.
    return str(order.public_order_number or order.id)


def build_octo_payment_url(order):
    shop_id = str(_cfg("OCTO_SHOP_ID")).strip()
    secret = str(_cfg("OCTO_SECRET")).strip()
    if not shop_id or not secret:
        raise OctoConfigError("Octo не настроен (OCTO_SHOP_ID / OCTO_SECRET)")

    total = order.total_amount or Decimal("0")
    if total <= 0:
        raise OctoConfigError("Нулевая сумма заказа")

    payload = {
        "octo_shop_id": int(shop_id) if shop_id.isdigit() else shop_id,
        "octo_secret": secret,
        "shop_transaction_id": _shop_transaction_id(order),
        "auto_capture": True,  # Этап 1 — одностадийная (списываем сразу)
        "init_time": timezone.localtime().strftime("%Y-%m-%d %H:%M:%S"),
        "test": bool(_cfg("OCTO_TEST")),
        "total_sum": float(total),
        "currency": "UZS",
        "description": f"Заказ {order.public_order_number or order.id}",
        "payment_methods": [
            {"method": "bank_card"},
            {"method": "uzcard"},
            {"method": "humo"},
        ],
        "return_url": _cfg("OCTO_RETURN_URL", "https://raccoon.uz/order-success"),
        "notify_url": _cfg("OCTO_NOTIFY_URL"),
        "language": "ru",
        "ttl": 15,
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        PREPARE_URL, data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise OctoConfigError(f"Octo prepare_payment: ошибка связи ({exc})")

    if body.get("error") not in (0, "0"):
        msg = body.get("errMessage") or body.get("errorMessage") or "ошибка Octo"
        raise OctoConfigError(f"Octo prepare_payment: {msg}")

    data_obj = body.get("data") or {}
    url = data_obj.get("octo_pay_url") or body.get("octo_pay_url")
    if not url:
        raise OctoConfigError("Octo не вернул octo_pay_url")
    return url


def verify_octo_signature(octo_payment_uuid, status, signature):
    """Подпись уведомления: SHA1(unique_key + octo_payment_UUID + status).

    unique_key выдаёт техподдержка Octo (env OCTO_NOTIFY_SECRET). Сравниваем
    регистронезависимо (Octo присылает hex в верхнем регистре).
    """
    unique_key = str(_cfg("OCTO_NOTIFY_SECRET")).strip()
    if not unique_key or not signature:
        return False
    raw = f"{unique_key}{octo_payment_uuid}{status}".encode("utf-8")
    calc = hashlib.sha1(raw).hexdigest()
    return calc.lower() == str(signature).strip().lower()
