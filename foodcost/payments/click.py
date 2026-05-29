"""
💳 Click payment integration.

Part 1 — URL builder for hosted checkout (no secret in URL).
Part 2 — callback signature verification + Prepare/Complete protocol.

Public hosted-checkout URL format (Click documentation):

    https://my.click.uz/services/pay
        ?service_id={CLICK_SERVICE_ID}
        &merchant_id={CLICK_MERCHANT_ID}
        &amount={amount}
        &transaction_param={merchant_trans_id}
        &return_url={CLICK_SUCCESS_URL}

Notes / invariants:
- CLICK_SECRET_KEY is NEVER part of the URL. It is used server-side ONLY
  for verifying the md5 sign_string sent by Click in the callback.
- CLICK_MERCHANT_USER_ID is required by Click's API endpoints (invoice /
  refund / etc.), but is NOT part of the hosted-checkout URL.
- `transaction_param` is what Click echoes back in the callback so we can
  find the order. We use Order.public_order_number when available (stable
  human-readable id printed on receipts) and fall back to the numeric id
  otherwise.
- amount is rounded to whole UZS — Click rejects sub-tiyin precision on
  hosted checkout for typical merchant configurations.

Callback protocol (Click sends POST form-encoded to ONE URL — action field
discriminates the stage):

    action=0 (PREPARE)  — Click asks "can this order be paid? confirm and
                          give us a merchant_prepare_id". We validate the
                          order and reply with error=0 + merchant_prepare_id.
    action=1 (COMPLETE) — Click reports the final outcome. We mark the
                          order paid / failed accordingly.

Signature (sent by Click as `sign_string`):

    md5(click_trans_id + service_id + SECRET_KEY + merchant_trans_id
        + [merchant_prepare_id for action=1]
        + amount + action + sign_time)

The amount and sign_time fields come as STRINGS in the form-encoded body and
the md5 is computed over those exact string concatenations — no separator,
no normalization. We verify with hmac.compare_digest to avoid timing leaks.

Click error codes we use in replies:
     0 — OK / success
    -1 — SIGN CHECK FAILED
    -2 — INCORRECT AMOUNT
    -3 — ACTION NOT FOUND
    -4 — ALREADY PAID
    -5 — USER (order) DOES NOT EXIST
    -8 — ERROR IN REQUEST FROM CLICK
    -9 — TRANSACTION CANCELLED
"""

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
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
    #
    # Click does NOT echo merchant_trans_id back into return_url for us, so
    # we have to bake order_number into the URL ourselves. Without it the
    # frontend lands on /order-success with no idea WHICH order — every
    # success-page lookup would fail.
    success_url = (getattr(settings, "CLICK_SUCCESS_URL", "") or "").strip()
    if success_url:
        params["return_url"] = _append_order_number(success_url, order)

    return f"{CLICK_CHECKOUT_URL}?{urlencode(params)}"


def _append_order_number(base_url: str, order) -> str:
    """
    Append ?order_number=... (or &order_number=... if the URL already has
    a query string) to a return URL. Idempotent — if order_number is
    already present (e.g. operator pre-baked it into env), don't duplicate.
    """
    order_number = (order.public_order_number or "").strip()
    if not order_number:
        # Fallback to numeric id so the frontend at least gets SOMETHING.
        order_number = str(order.id) if order.id else ""
    if not order_number:
        return base_url
    if "order_number=" in base_url:
        return base_url
    sep = "&" if "?" in base_url else "?"
    return f"{base_url}{sep}order_number={order_number}"


# =============================================================================
# Click callback constants & signature verification (Part 2)
# =============================================================================

# Click action field — discriminates Prepare vs Complete on the SAME URL.
ACTION_PREPARE = 0
ACTION_COMPLETE = 1

# Click reply error codes (see module docstring for the full list).
ERROR_OK = 0
ERROR_SIGN_CHECK_FAILED = -1
ERROR_INCORRECT_AMOUNT = -2
ERROR_ACTION_NOT_FOUND = -3
ERROR_ALREADY_PAID = -4
ERROR_USER_DOES_NOT_EXIST = -5
ERROR_BAD_REQUEST = -8
ERROR_TRANSACTION_CANCELLED = -9


def _compute_click_signature(
    *,
    click_trans_id: str,
    service_id: str,
    secret_key: str,
    merchant_trans_id: str,
    merchant_prepare_id: str,
    amount: str,
    action: str,
    sign_time: str,
) -> str:
    """
    Reproduce Click's md5 sign_string.

    For PREPARE (action=0), Click does NOT include merchant_prepare_id in the
    request and the signature is computed over an empty string in that
    position. For COMPLETE (action=1), merchant_prepare_id is whatever we
    returned during Prepare and Click echoes it back.

    All inputs are concatenated as strings — no separator, no normalization.
    """
    import hashlib
    raw = (
        f"{click_trans_id}"
        f"{service_id}"
        f"{secret_key}"
        f"{merchant_trans_id}"
        f"{merchant_prepare_id}"
        f"{amount}"
        f"{action}"
        f"{sign_time}"
    )
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def verify_click_signature(
    *,
    sign_string: str,
    click_trans_id: str,
    service_id: str,
    merchant_trans_id: str,
    merchant_prepare_id: str,
    amount: str,
    action: str,
    sign_time: str,
) -> bool:
    """
    Constant-time signature check. Returns True iff the supplied sign_string
    matches what we compute with our local CLICK_SERVICE_ID / SECRET_KEY.

    Implementation notes:
      - service_id MUST equal settings.CLICK_SERVICE_ID. If Click sends a
        request for a different service, the signature won't match and we
        return False without leaking which check failed.
      - Uses hmac.compare_digest for timing-safe comparison.
      - On any input being missing / wrong type, returns False rather than
        raising — Click should get a clean -1 reply, not a 500.
    """
    import hmac
    secret_key = (getattr(settings, "CLICK_SECRET_KEY", "") or "").strip()
    if not secret_key:
        return False

    expected_service_id = (getattr(settings, "CLICK_SERVICE_ID", "") or "").strip()
    if not expected_service_id or service_id != expected_service_id:
        return False

    if not sign_string:
        return False

    try:
        computed = _compute_click_signature(
            click_trans_id=str(click_trans_id or ""),
            service_id=str(service_id or ""),
            secret_key=secret_key,
            merchant_trans_id=str(merchant_trans_id or ""),
            merchant_prepare_id=str(merchant_prepare_id or ""),
            amount=str(amount or ""),
            action=str(action or ""),
            sign_time=str(sign_time or ""),
        )
    except Exception:
        return False

    return hmac.compare_digest(computed.lower(), str(sign_string).lower())


def amounts_match(order_total, click_amount_str: str) -> bool:
    """
    Compare order.total_amount (Decimal) with the amount Click sent (string).

    Click sends amounts with decimals (e.g. "172200.00"). We compare as
    Decimal so "172200" == "172200.00" == 172200, and tiny float drift can't
    accept the wrong amount.
    """
    if order_total is None:
        return False
    try:
        from_click = Decimal(str(click_amount_str).strip())
    except (InvalidOperation, AttributeError, TypeError):
        return False
    try:
        local = Decimal(str(order_total))
    except (InvalidOperation, AttributeError, TypeError):
        return False
    # Quantize both to the same precision to handle "100" vs "100.00".
    q = Decimal("0.01")
    return local.quantize(q) == from_click.quantize(q)
