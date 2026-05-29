"""
💳 Payme (Paycom) payment integration.

Two pieces:
  1. build_payme_payment_url(order)  — generates the hosted-checkout GET URL
     the frontend redirects to. Format (from developer.help.paycom.uz):

         https://checkout.paycom.uz/<base64(m=MID;ac.FIELD=VALUE;a=AMOUNT)>

     plus optional `;c=CALLBACK;l=ru`. Amount is in TIYIN (1 sum = 100 tiyin).
     The base64 is standard base64 of an ASCII string with `;`-separated
     key=value pairs. There is NO signature in the URL — Payme authenticates
     the callback separately using HTTP Basic Auth with the secret key.

  2. verify_payme_basic_auth(request) — checks the Authorization header on
     incoming JSON-RPC callbacks. Payme sends:

         Authorization: Basic base64("Paycom:<SECRET_KEY>")

     We decode the header and timing-safely compare against
     settings.PAYME_SECRET_KEY. Wrong / missing auth → -32504.

Both pieces refuse to run if env vars are empty so a half-configured deploy
fails loudly with a clean error instead of silently producing broken URLs
or accepting unauthenticated callbacks.

Constants below mirror the official spec at:
  https://developer.help.paycom.uz/metody-merchant-api/oshibki-errors
  https://developer.help.paycom.uz/metody-merchant-api/tipy-dannykh
"""

import base64
import binascii
import hmac
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import quote

from django.conf import settings


# =============================================================================
# Error codes (Payme JSON-RPC reply error.code)
# =============================================================================
# Generic JSON-RPC / transport errors:
ERROR_METHOD_NOT_POST = -32300
ERROR_PARSE = -32700
ERROR_INVALID_REQUEST = -32600       # missing / wrong-type RPC fields
ERROR_METHOD_NOT_FOUND = -32601
ERROR_INSUFFICIENT_PRIVILEGE = -32504  # wrong Basic Auth
ERROR_SYSTEM = -32400                  # internal — DB down, etc.

# Merchant-side errors:
ERROR_INVALID_AMOUNT = -31001
ERROR_TRANSACTION_NOT_FOUND = -31003
ERROR_CANNOT_CANCEL = -31007   # delivered — refuse cancel
ERROR_INVALID_STATE = -31008   # operation not allowed in current state

# Account errors (range -31050..-31099). One concrete code is enough for our
# single account field "order_id"; pick -31050 which Payme docs use as the
# canonical "invalid account" example.
ERROR_ACCOUNT_NOT_FOUND = -31050


# =============================================================================
# Transaction state codes (mirrored in PaymeTransaction model)
# =============================================================================
STATE_CREATED = 1            # awaiting payment
STATE_COMPLETED = 2          # paid
STATE_CANCELLED = -1         # cancelled from STATE_CREATED
STATE_CANCELLED_AFTER = -2   # refund (cancelled from STATE_COMPLETED)

# Cancellation reasons. We pass through whatever Payme sends — these are
# here so callers can reference symbolic names.
REASON_RECEIVERS_INACTIVE = 1
REASON_DEBIT_OPERATION_FAILED = 2
REASON_TRANSACTION_FAILED = 3
REASON_TIMEOUT = 4
REASON_REFUND = 5
REASON_UNKNOWN = 10

# Timeout the Payme platform itself uses to auto-cancel STATE_CREATED
# transactions. We don't enforce it locally (Payme will send CancelTransaction
# when it expires), but exposed here for reference / future logic.
PAYME_TIMEOUT_MS = 12 * 60 * 60 * 1000  # 43,200,000


class PaymeConfigError(RuntimeError):
    """Raised when PAYME_* env vars are missing — surfaced as a 503 to caller."""


class PaymeError(Exception):
    """
    Raised inside a JSON-RPC handler to short-circuit and produce a Payme
    error response with the right `code` / `message` / `data` envelope.

    Payme expects messages as objects localized into ru/uz/en. For account
    errors (-31050..-31099) the `data` field MUST be the account sub-field
    name (e.g. "order_id").
    """

    def __init__(self, code, message=None, data=None):
        self.code = int(code)
        self.message = message or _DEFAULT_MESSAGES.get(self.code, {
            "ru": "Ошибка",
            "uz": "Xato",
            "en": "Error",
        })
        self.data = data
        super().__init__(f"PaymeError {self.code}")


# Localized error texts. Payme docs say ru/uz/en are expected.
_DEFAULT_MESSAGES = {
    ERROR_METHOD_NOT_POST: {
        "ru": "Метод запроса должен быть POST",
        "uz": "So‘rov metodi POST bo‘lishi kerak",
        "en": "Request method must be POST",
    },
    ERROR_PARSE: {
        "ru": "Ошибка парсинга JSON",
        "uz": "JSON tahlil qilishda xatolik",
        "en": "JSON parse error",
    },
    ERROR_INVALID_REQUEST: {
        "ru": "Отсутствуют обязательные поля",
        "uz": "Majburiy maydonlar yo‘q",
        "en": "Missing required fields",
    },
    ERROR_METHOD_NOT_FOUND: {
        "ru": "Метод не найден",
        "uz": "Metod topilmadi",
        "en": "Method not found",
    },
    ERROR_INSUFFICIENT_PRIVILEGE: {
        "ru": "Недостаточно привилегий",
        "uz": "Ruxsat yetarli emas",
        "en": "Insufficient privileges",
    },
    ERROR_SYSTEM: {
        "ru": "Системная ошибка",
        "uz": "Tizim xatosi",
        "en": "System error",
    },
    ERROR_INVALID_AMOUNT: {
        "ru": "Неверная сумма",
        "uz": "Noto‘g‘ri summa",
        "en": "Invalid amount",
    },
    ERROR_TRANSACTION_NOT_FOUND: {
        "ru": "Транзакция не найдена",
        "uz": "Tranzaksiya topilmadi",
        "en": "Transaction not found",
    },
    ERROR_CANNOT_CANCEL: {
        "ru": "Невозможно отменить транзакцию. Услуга оказана.",
        "uz": "Tranzaksiyani bekor qilib bo‘lmaydi. Xizmat ko‘rsatildi.",
        "en": "Cannot cancel transaction. Service already provided.",
    },
    ERROR_INVALID_STATE: {
        "ru": "Невозможно выполнить операцию",
        "uz": "Amalni bajarib bo‘lmaydi",
        "en": "Operation not allowed",
    },
    ERROR_ACCOUNT_NOT_FOUND: {
        "ru": "Заказ не найден",
        "uz": "Buyurtma topilmadi",
        "en": "Order not found",
    },
}


# =============================================================================
# Checkout URL builder
# =============================================================================

def _require_env(name):
    value = (getattr(settings, name, "") or "").strip()
    if not value:
        raise PaymeConfigError(
            f"{name} is empty — Payme is not configured on the server."
        )
    return value


def amount_to_tiyin(order_total) -> int:
    """
    Convert order.total_amount (Decimal sums) to integer tiyin.
    1 sum = 100 tiyin. ROUND_HALF_UP so 100.50 → 10050 tiyin, not 10049.
    """
    if order_total is None:
        raise PaymeConfigError("Cannot build Payme URL for order with no total.")
    try:
        d = Decimal(str(order_total))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise PaymeConfigError(f"Invalid order total for Payme: {order_total!r}") from exc
    tiyin = (d * Decimal(100)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    if tiyin <= 0:
        raise PaymeConfigError("Order total must be positive for Payme.")
    return int(tiyin)


def build_payme_payment_url(order) -> str:
    """
    Build the hosted-checkout GET URL. Format from official docs:

        https://checkout.paycom.uz/<base64(m=MID;ac.FIELD=VALUE;a=AMOUNT)>

    Where:
      - m       = merchant cashbox id (settings.PAYME_MERCHANT_ID)
      - ac.X=Y  = order identifier. Field name is settings.PAYME_ACCOUNT_FIELD
                  (default "order_id"), value is order.public_order_number
                  or order.id as fallback. The SAME field name must be
                  configured in Payme cabinet — they must match exactly.
      - a       = amount in TIYIN (positive integer)

    Optional, appended after `a`:
      - c       = callback URL Payme redirects to after payment
      - l       = language (ru / uz / en)

    Base64 is standard (not URL-safe variant) — Payme accepts both, but
    we use standard to match every reference implementation.
    """
    merchant_id = _require_env("PAYME_MERCHANT_ID")
    checkout_base = _require_env("PAYME_CHECKOUT_URL").rstrip("/")
    account_field = (
        getattr(settings, "PAYME_ACCOUNT_FIELD", "") or "order_id"
    ).strip()

    # The token Payme will echo back in callback `params.account.<field>`.
    # Prefer the human-readable public_order_number ("RCN-2026-000123") so
    # support and customer-facing receipts match. Fall back to numeric pk
    # for very old orders that never got a public_order_number.
    account_value = (order.public_order_number or "").strip() or str(order.id)

    amount_tiyin = amount_to_tiyin(order.total_amount)

    # Optional return URL — Payme uses this to redirect the user's BROWSER
    # back after payment. We pass the success URL; on failure Payme stays
    # on its own page or shows an error, and our frontend learns about it
    # from order_tracking (payment_status=cancelled/failed).
    return_url = (getattr(settings, "PAYME_SUCCESS_URL", "") or "").strip()

    parts = [
        f"m={merchant_id}",
        f"ac.{account_field}={account_value}",
        f"a={amount_tiyin}",
    ]
    if return_url:
        # Per docs, the `c=` value can contain `:` and `/` directly; Payme
        # does not URL-decode it. We include it as-is.
        parts.append(f"c={return_url}")
    # Force Russian UI by default — most users are RU-speaking. Override
    # via env if you want auto-detect.
    parts.append("l=ru")

    raw = ";".join(parts).encode("utf-8")
    token = base64.b64encode(raw).decode("ascii")

    # Defensive: never include the secret key. (We don't, but assert just
    # in case someone "helpfully" appends it later.)
    assert "PAYME_SECRET" not in token, "secret leaked into checkout URL"

    return f"{checkout_base}/{token}"


# =============================================================================
# Callback authentication (HTTP Basic, NOT a signature)
# =============================================================================

def verify_payme_basic_auth(request) -> bool:
    """
    Verify the Authorization: Basic <base64(Paycom:KEY)> header.

    Returns True only when:
      - header is present and starts with "Basic "
      - base64 decodes cleanly
      - decoded body is "Paycom:<KEY>" (the literal "Paycom" is the login
        Payme uses for every merchant)
      - KEY matches settings.PAYME_SECRET_KEY (timing-safe compare)

    Anything else → False. The caller should then return error -32504
    (insufficient privileges). We never log the header contents.
    """
    secret = (getattr(settings, "PAYME_SECRET_KEY", "") or "").strip()
    if not secret:
        # Server misconfigured — fail closed. Returning False makes Payme
        # see -32504 and stop hammering us with calls that would otherwise
        # silently pass.
        return False

    header = (
        request.META.get("HTTP_AUTHORIZATION")
        or request.headers.get("Authorization")
        or ""
    )
    header = header.strip()
    if not header.lower().startswith("basic "):
        return False

    encoded = header[6:].strip()
    if not encoded:
        return False

    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8", errors="strict")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return False

    # Expected form is exactly "Paycom:<key>". The login part is fixed.
    if ":" not in decoded:
        return False
    login, _, key = decoded.partition(":")
    if login != "Paycom":
        return False

    return hmac.compare_digest(key, secret)


def amount_matches_order(order, amount_tiyin) -> bool:
    """
    Compare order.total_amount (Decimal sums) with the integer tiyin Payme
    sent in CreateTransaction / CheckPerformTransaction. Returns True iff
    they're equal to the cent.

    Strictly requires an int — Payme spec says `Amount` is "положительное
    целое число" (positive integer). Strings, floats, bools, None all
    return False to surface as -31001 to Payme.
    """
    if order is None or order.total_amount is None:
        return False
    # Reject bool first — bool is a subtype of int in Python (True == 1).
    if isinstance(amount_tiyin, bool):
        return False
    if not isinstance(amount_tiyin, int):
        return False
    if amount_tiyin <= 0:
        return False
    try:
        local_tiyin = (
            Decimal(str(order.total_amount)) * Decimal(100)
        ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        wire_tiyin = Decimal(amount_tiyin)
    except (InvalidOperation, TypeError, ValueError):
        return False
    return local_tiyin == wire_tiyin
