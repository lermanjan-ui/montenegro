"""
💳 Click payment integration — URL builder (Part 1).

Public hosted-checkout URL format (Click documentation):

    https://my.click.uz/services/pay
        ?service_id={CLICK_SERVICE_ID}
        &merchant_id={CLICK_MERCHANT_ID}
        &amount={amount}
        &transaction_param={merchant_trans_id}
        &return_url={CLICK_SUCCESS_URL}

Notes / invariants:
- CLICK_SECRET_KEY is NEVER part of the URL. It is used server-side ONLY
  for verifying the md5 sign_string sent by Click in the callback (Part 2).
- CLICK_MERCHANT_USER_ID is required by Click's API endpoints (invoice /
  refund / etc.), but is NOT part of the hosted-checkout URL.
- `transaction_param` is what Click echoes back in the callback so we can
  find the order. We use Order.public_order_number when available (stable
  human-readable id printed on receipts) and fall back to the numeric id
  otherwise.
- amount is rounded to whole UZS — Click rejects sub-tiyin precision on
  hosted checkout for typical merchant configurations.
"""

from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import urlencode

from django.conf import settings


CLICK_CHECKOUT_URL = "https://my.click.uz/services/pay"


class ClickConfigError(RuntimeError):
    """Raised when CLICK_SERVICE_ID / CLICK_MERCHANT_ID are missing."""


def _format_amount(value) -> str:
    """
    Click expects the amount as a plain decimal number. We round half-up to
    a whole sum because the typical UZS merchant config does not accept
    fractional tiyin from hosted checkout. The order's total_amount is the
    server-calculated authoritative figure.
    """
    if value is None:
        raise ClickConfigError("Order total is empty")
    amount = Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if amount <= 0:
        raise ClickConfigError("Order total must be > 0 for Click payment")
    return str(amount)


def _merchant_trans_id(order) -> str:
    """
    Identifier Click echoes back in the callback. Prefer the public order
    number (human-readable, printed on the receipt); fall back to the pk
    when the number wasn't generated yet.
    """
    number = (getattr(order, "public_order_number", "") or "").strip()
    if number:
        return number
    return str(order.id)


def build_click_payment_url(order) -> str:
    """
    Build the hosted-checkout URL the frontend will redirect the customer
    to. Pure function — does NOT touch the order, does NOT call Click's API,
    does NOT include the secret key.

    Raises ClickConfigError when CLICK_SERVICE_ID / CLICK_MERCHANT_ID is
    missing in settings, so the caller can return a clean API error
    instead of generating a broken URL.
    """
    service_id = (getattr(settings, "CLICK_SERVICE_ID", "") or "").strip()
    merchant_id = (getattr(settings, "CLICK_MERCHANT_ID", "") or "").strip()

    if not service_id or not merchant_id:
        raise ClickConfigError(
            "Click is not configured: CLICK_SERVICE_ID and CLICK_MERCHANT_ID "
            "must be set in environment variables."
        )

    params = {
        "service_id": service_id,
        "merchant_id": merchant_id,
        "amount": _format_amount(order.total_amount),
        "transaction_param": _merchant_trans_id(order),
    }

    # return_url is optional in Click — include it only when the operator
    # has configured one, so we don't send an empty string and confuse the
    # gateway.
    success_url = (getattr(settings, "CLICK_SUCCESS_URL", "") or "").strip()
    if success_url:
        params["return_url"] = success_url

    return f"{CLICK_CHECKOUT_URL}?{urlencode(params)}"
