"""
Meta Conversions API — server-side Purchase event sender.

Fired by `send_pending_meta_purchases` management command 15+ minutes after
an order is paid. The same `event_id` is used here as the frontend used
when it fired the Pixel client-side, so Meta deduplicates the pair into
one conversion (instead of double-counting).

Design notes
============
* Hashing convention agreed with the frontend (Next.js `/api/meta-capi/route.ts`):
    phone: digits-only, lower-cased (no leading "+"), SHA-256 hex.
    email: lower-cased trimmed, SHA-256 hex.
  If either side ever changes this, dedup-by-user_data breaks — but the
  primary dedup mechanism is `event_id`, so user_data is just a bonus
  signal for matching against FB user identities.

* `content_ids` are sent as STRINGS because the frontend sends them as
  strings (Product.id is typed string in their TS, contains numeric chars
  like "42"). Mixing string/number across Pixel and CAPI breaks Catalog
  Match — Meta sees them as different products. We always use str(dish.id).

* No exceptions propagate out — caller (management command) keeps running
  for other orders in the batch. The function raises only on truly
  unrecoverable bugs (missing env vars, malformed order). HTTP / network
  errors are caught and re-raised so the caller can log and retry on
  the next cron tick.

* Settings live in env vars, not Django settings.py, because they're
  secrets and we don't want to add them to the settings module — caller
  reads via os.environ.
"""

import hashlib
import logging
import os
import time
from decimal import Decimal

import requests

logger = logging.getLogger(__name__)


META_API_VERSION = os.environ.get("META_CAPI_API_VERSION", "v18.0")
META_API_TIMEOUT = 8  # seconds; Meta is fast, anything longer is broken


class MetaCapiNotConfigured(Exception):
    """Required env vars (PIXEL_ID, ACCESS_TOKEN) are missing — caller
    should log and skip, not retry."""


class MetaCapiError(Exception):
    """Network / HTTP error talking to graph.facebook.com — caller may
    retry on the next tick. Wraps the underlying exception's message."""


# -- Hashing helpers ---------------------------------------------------------

def _sha256_hex(value):
    """SHA-256 hex digest of a UTF-8 string. None / empty → empty string
    (do NOT send a hash of "" to Meta — they reject it)."""
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_phone(raw):
    """Frontend convention (CheckoutView):
        raw.trim().toLowerCase().replace(/[^\\d]/g, '')
    e.g. "+998 90 123 45 67" → "998901234567". Empty string if no digits.
    """
    if not raw:
        return ""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits


def _normalize_email(raw):
    """SHA-256(lowercase(trim(email)))."""
    if not raw:
        return ""
    return str(raw).strip().lower()


# -- Payload builders --------------------------------------------------------

def _build_user_data(order, request_meta=None):
    """Build the `user_data` block. All PII fields hashed per Meta spec.

    We omit fb-cookie fields (`fbc`, `fbp`) and `client_ip_address` /
    `client_user_agent` because the cron task runs hours after the user's
    request — those signals are gone. The Pixel event already sent them
    from the browser; Meta will use those for matching.
    """
    user_data = {}

    phone_norm = _normalize_phone(order.customer_phone)
    if phone_norm:
        user_data["ph"] = _sha256_hex(phone_norm)

    # Email — only if the customer FK has it. Order itself doesn't store
    # email on raccoon.uz's checkout (phone is the primary identifier).
    customer = getattr(order, "customer", None)
    if customer is not None:
        email_norm = _normalize_email(getattr(customer, "email", None))
        if email_norm:
            user_data["em"] = _sha256_hex(email_norm)

    # External ID — our internal order_number gives Meta something
    # stable to match across events for the same purchase. Hashed per spec.
    if order.public_order_number:
        user_data["external_id"] = _sha256_hex(
            order.public_order_number.lower()
        )

    return user_data


def _build_custom_data(order):
    """Build `custom_data` — purchase value, currency, contents.

    Convention agreed with frontend:
      - `currency`: "UZS"
      - `value`: numeric, total in sums (we send Decimal as int — Meta
        accepts both, but matching the frontend's `order.total` shape is
        safer. Frontend sends a number not a string.)
      - `content_ids`: list of strings, e.g. ["42", "18"]
      - `contents`: list of {id, quantity, item_price}
      - `content_type`: "product"
      - `num_items`: total quantity across the order
    """
    items_qs = order.items.select_related("dish").all()

    content_ids = []
    contents = []
    num_items = 0

    for item in items_qs:
        # str() not number — see module docstring.
        dish_id = str(item.dish_id) if item.dish_id else ""
        if not dish_id:
            continue
        content_ids.append(dish_id)

        try:
            qty = int(item.quantity)
        except (TypeError, ValueError):
            qty = 1
        num_items += qty

        # `item_price` is per-unit price as a float (Meta wants number not str).
        try:
            unit_price = float(item.price_snapshot or 0)
        except (TypeError, ValueError):
            unit_price = 0.0

        contents.append({
            "id": dish_id,
            "quantity": qty,
            "item_price": unit_price,
        })

    # Total — Meta wants a number. Decimal → float keeps precision for our
    # use case (sums are whole numbers in UZS anyway, no fractional sums).
    try:
        value = float(order.total_amount or 0)
    except (TypeError, ValueError):
        value = 0.0

    return {
        "currency": "UZS",
        "value": value,
        "content_type": "product",
        "content_ids": content_ids,
        "contents": contents,
        "num_items": num_items,
        # `order_id` is a top-level convention recognized by Meta for
        # purchase events; helps with their own dedup on top of event_id.
        "order_id": order.public_order_number or str(order.id),
    }


def build_purchase_payload(order):
    """Build the full request body for one Purchase event.

    Returns a dict ready to JSON-serialize. Caller wraps it in the outer
    {"data": [<this>], ...} envelope and POSTs to Meta.
    """
    event_time = int(time.time())

    payload = {
        "event_name": "Purchase",
        "event_time": event_time,
        # `event_id` is THE deduplication key with the Pixel event. Must be
        # the exact UUID the frontend generated, not a fresh one here.
        "event_id": order.meta_event_id,
        "action_source": "website",
        "user_data": _build_user_data(order),
        "custom_data": _build_custom_data(order),
    }

    # `event_source_url` helps Meta verify the event came from a real page
    # on the registered domain. For a server-side event 15 min after the
    # fact we approximate with the checkout-success URL.
    site_base = os.environ.get("PUBLIC_SITE_BASE_URL", "https://raccoon.uz")
    payload["event_source_url"] = (
        f"{site_base.rstrip('/')}/checkout/success/"
        f"{order.public_order_number}"
    )

    return payload


# -- Sender ------------------------------------------------------------------

def send_meta_capi_purchase(order):
    """Send a Purchase event for one Order to Meta CAPI.

    Raises:
        MetaCapiNotConfigured — env vars missing; do not retry.
        MetaCapiError         — transient network/HTTP error; safe to retry.

    On success returns the parsed response dict from Meta (contains
    `events_received: 1` on accept).
    """
    pixel_id = os.environ.get("META_PIXEL_ID")
    access_token = os.environ.get("META_CAPI_ACCESS_TOKEN")
    if not pixel_id or not access_token:
        raise MetaCapiNotConfigured(
            "META_PIXEL_ID or META_CAPI_ACCESS_TOKEN missing in environment"
        )

    if not order.meta_event_id:
        # No event_id → no dedup possible → skip rather than send a
        # standalone event that would double-count against the Pixel.
        raise MetaCapiNotConfigured(
            f"order {order.public_order_number}: meta_event_id is empty"
        )

    event_payload = build_purchase_payload(order)

    body = {
        "data": [event_payload],
        "access_token": access_token,
    }

    # Test Events mode — when set, events go to the Test Events tab in
    # Events Manager, not into the live Pixel statistics. Useful for
    # verifying the integration without polluting the production funnel.
    test_code = os.environ.get("META_CAPI_TEST_CODE")
    if test_code:
        body["test_event_code"] = test_code

    url = f"https://graph.facebook.com/{META_API_VERSION}/{pixel_id}/events"

    try:
        resp = requests.post(url, json=body, timeout=META_API_TIMEOUT)
    except requests.RequestException as e:
        raise MetaCapiError(f"network: {e}") from e

    # Meta returns 200 with JSON on accept; 4xx with `error.message` on
    # rejection. We log the response body either way for debugging.
    try:
        data = resp.json()
    except ValueError:
        data = {"_raw": resp.text[:500]}

    if resp.status_code >= 400:
        # Common Meta error shape: {"error": {"message": "...", "code": N}}
        err = data.get("error", {}) if isinstance(data, dict) else {}
        msg = err.get("message") or resp.text[:300]
        # Permissions errors (400 with code=190) are configuration — would
        # fail every retry. Caller decides; here we just surface the message.
        raise MetaCapiError(
            f"HTTP {resp.status_code}: {msg}"
        )

    logger.info(
        "[meta-capi] order %s Purchase sent: %s",
        order.public_order_number, data,
    )
    return data
