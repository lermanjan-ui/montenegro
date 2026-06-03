"""
Meta Marketing API — push a Value-Based Custom Audience (LTV) from CRM.

Builds the audience from CRM customers and pushes it to a pre-created
Custom Audience in Meta Ads Manager, so Meta can build a Value-Based
Lookalike that optimizes toward the most valuable customers.

LTV definition (agreed with owner)
==================================
LTV(customer) = sum(Order.net_revenue) over orders that are
    payment_status == "paid" AND is_cancelled == False.

We send PHONE (SHA-256) + LOOKALIKE_VALUE (the LTV). Customer has no email
on raccoon.uz (phone is the primary identifier), so we only send phone.

Hashing
=======
Reuses the exact convention from meta_capi.py so the same person matches
across CAPI events and this audience:
    phone -> digits only -> SHA-256 hex.

Config (env vars on Render)
===========================
Separate from CAPI — these need Ads permissions, not just CAPI:
    META_ADS_ACCESS_TOKEN  — System User token with ads_management
    META_AD_ACCOUNT_ID     — e.g. "act_1234567890" (with or without act_)
    META_LTV_AUDIENCE_ID   — id of the pre-created Custom Audience
    META_CAPI_API_VERSION  — reused; defaults to v18.0

Design
======
* No exceptions escape the public push function unless config is missing —
  the management command logs and exits non-zero on transient errors so
  the cron can retry.
* Pushed in batches (Meta caps payload size); we use 5000 rows/batch.
* Segments are computed but, for the value-based audience, all paying
  customers are included with their LTV. Segment tags are exposed via
  build_audience_rows() so a future CSV export / separate audiences can
  reuse the same selection logic.
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .meta_capi import _sha256_hex, _normalize_phone

logger = logging.getLogger(__name__)

META_API_VERSION = os.environ.get("META_CAPI_API_VERSION", "v18.0")
META_API_TIMEOUT = 15  # audience pushes can be larger than CAPI events
BATCH_SIZE = 5000

# Segment thresholds (tunable). Days for recency/newness; counts/sums are
# relative so they work regardless of currency scale.
NEW_CUSTOMER_DAYS = 30      # first order within N days => "new"
LAPSED_DAYS = 60            # last order older than N days => "lapsed"
FREQUENT_MIN_ORDERS = 4     # >= N paid orders => "frequent"


class MetaAudienceNotConfigured(Exception):
    """Required env vars are missing — caller logs and skips."""


class MetaAudienceError(Exception):
    """Transient network/HTTP error talking to Meta — safe to retry."""


def _ad_account_id():
    raw = (os.environ.get("META_AD_ACCOUNT_ID") or "").strip()
    if not raw:
        return ""
    return raw if raw.startswith("act_") else f"act_{raw}"


def build_audience_rows(country=None):
    """Build the list of audience rows from CRM.

    Returns a list of dicts:
        {
          "phone_hash": <sha256 hex or "">,
          "ltv": <float>,
          "orders_count": <int>,
          "segment": <str>,            # frequent / high_value / lapsed / new / regular
        }

    Only customers with a non-empty phone and LTV > 0 are included
    (Meta needs at least one identifier and a positive value to be useful).

    Imported lazily to avoid circular imports at module load.
    """
    from .models import Customer, Order

    now = timezone.now()
    rows = []

    customers = Customer.objects.all()
    if country is not None:
        customers = customers.filter(country=country)

    for customer in customers.iterator():
        phone_norm = _normalize_phone(customer.phone)
        if not phone_norm:
            continue

        paid_orders = list(
            Order.objects.filter(
                customer=customer,
                payment_status=Order.PAYMENT_STATUS_PAID,
                is_cancelled=False,
            ).only("net_revenue", "order_date")
        )
        if not paid_orders:
            continue

        ltv = sum((Decimal(o.net_revenue or 0) for o in paid_orders), Decimal("0"))
        if ltv <= 0:
            continue

        orders_count = len(paid_orders)
        dates = [o.order_date for o in paid_orders if o.order_date]
        first_order = min(dates) if dates else None
        last_order = max(dates) if dates else None

        # Segment (single label, priority order). Used for separate
        # audiences / exclusions later; the value-based push uses LTV.
        segment = "regular"
        if first_order and (now - first_order) <= timedelta(days=NEW_CUSTOMER_DAYS):
            segment = "new"
        elif last_order and (now - last_order) > timedelta(days=LAPSED_DAYS):
            segment = "lapsed"
        elif orders_count >= FREQUENT_MIN_ORDERS:
            segment = "frequent"

        rows.append({
            "phone_hash": _sha256_hex(phone_norm),
            "ltv": float(ltv),
            "orders_count": orders_count,
            "segment": segment,
        })

    return rows


def _post_users_batch(audience_id, access_token, schema, batch):
    """POST one batch of users to the Custom Audience. Returns Meta's
    parsed response. Raises MetaAudienceError on network/HTTP failure.

    Uses urllib (not requests) to match meta_capi.py — the project ships
    no `requests` dependency."""
    url = (
        f"https://graph.facebook.com/{META_API_VERSION}/"
        f"{audience_id}/users"
    )
    payload = {
        "payload": {
            "schema": schema,
            "data": batch,
        },
        "access_token": access_token,
    }

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=META_API_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # Meta returns the error body with a 4xx/5xx — read it for the message.
        try:
            body = e.read().decode("utf-8", "replace")
            data = json.loads(body)
            err = data.get("error", {}) if isinstance(data, dict) else {}
            msg = err.get("message") or body[:300]
        except Exception:
            msg = str(e)
        raise MetaAudienceError(f"HTTP {e.code}: {msg}") from e
    except urllib.error.URLError as e:
        raise MetaAudienceError(f"network: {e}") from e

    try:
        return json.loads(raw)
    except ValueError:
        return {"_raw": raw[:500]}


def push_ltv_audience(country=None):
    """Push the value-based LTV audience to Meta.

    Schema: [PHONE_SHA256, LOOKALIKE_VALUE]. Phone is already SHA-256;
    LOOKALIKE_VALUE is the LTV as a number.

    Returns a summary dict: {"sent": N, "batches": M}.
    Raises MetaAudienceNotConfigured / MetaAudienceError.
    """
    access_token = os.environ.get("META_ADS_ACCESS_TOKEN")
    ad_account = _ad_account_id()
    audience_id = os.environ.get("META_LTV_AUDIENCE_ID")

    if not access_token or not ad_account or not audience_id:
        raise MetaAudienceNotConfigured(
            "META_ADS_ACCESS_TOKEN / META_AD_ACCOUNT_ID / "
            "META_LTV_AUDIENCE_ID missing in environment"
        )

    rows = build_audience_rows(country=country)
    if not rows:
        logger.info("[meta-audience] no rows to push")
        return {"sent": 0, "batches": 0}

    # Schema must match the order of values in each data row.
    schema = ["PHONE", "LOOKALIKE_VALUE"]
    data = [[r["phone_hash"], r["ltv"]] for r in rows]

    sent = 0
    batches = 0
    for start in range(0, len(data), BATCH_SIZE):
        batch = data[start:start + BATCH_SIZE]
        _post_users_batch(audience_id, access_token, schema, batch)
        sent += len(batch)
        batches += 1
        # Gentle pacing between batches to stay well under rate limits.
        if start + BATCH_SIZE < len(data):
            time.sleep(1)

    logger.info(
        "[meta-audience] pushed %s users in %s batch(es) to audience %s",
        sent, batches, audience_id,
    )
    return {"sent": sent, "batches": batches}
