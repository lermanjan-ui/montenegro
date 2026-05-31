"""
Promo code usage limits — server-side validation.

Lives next to models.py rather than buried inside public_api so admin
screens, the cart-calculate endpoint, and the order-create endpoint all
call the same source of truth.

Usage limits supported (see PromoCode.USAGE_LIMIT_CHOICES):

    none         — unrestricted, code can be redeemed any number of times
                   by any customer (current behavior, default).

    first_order  — code is rejected if the customer (matched by
                   country + phone) already has any prior order that
                   isn't in a "didn't count" state. See COUNTING_STATUSES
                   below for the exact rule.

The function returns a (ok: bool, error_code: str|None) tuple so the
caller can map error_code to its own response shape (api_error in the
public API, form ValidationError in admin, etc.).
"""

from .models import Order


# Order states that DO count as "the customer has already ordered".
# Anything outside this set means the order never went through (cancelled
# by operator, payment expired, abandoned in awaiting_payment past TTL)
# and shouldn't burn the customer's first-order promo eligibility.
#
# We list the counting statuses explicitly rather than negating the
# non-counting ones — adding a new status to the model will then be a
# safe no-op here (it defaults to "not counted") until we explicitly opt
# it in. The alternative (exclude list) would silently count a future
# status as "this customer already ordered", which is more dangerous.
COUNTING_STATUSES = {
    Order.STATUS_NEW,
    Order.STATUS_AWAITING_PAYMENT,
    Order.STATUS_COOKING,
    Order.STATUS_DELIVERY,
    Order.STATUS_DONE,
}

# Payment states that DO count even if the order's main status is one
# of the above. We additionally exclude orders whose payment ended in
# a non-success terminal state, because those are typically followed by
# the operator changing status to cancelled — but during the brief
# window between callback and operator-cancel they'd still be in
# cooking/delivery, and we don't want to count that as "ordered".
NON_COUNTING_PAYMENT_STATUSES = {
    Order.PAYMENT_STATUS_REFUNDED,
    Order.PAYMENT_STATUS_FAILED,
    Order.PAYMENT_STATUS_EXPIRED,
    Order.PAYMENT_STATUS_CANCELLED,
}


def has_prior_qualifying_order(country, phone):
    """True if the (country, phone) pair has at least one prior order
    that counts toward the customer's history.

    Used by first_order promo enforcement. Cheap query — uses the
    existing index on Order.customer_phone via the WHERE filter.

    Notes on matching:
      - We match by `customer_phone` on the Order itself (not on the
        Customer FK), so the rule applies even if the operator later
        re-points the order to a different customer. The phone is what
        the website typed at checkout.
      - We don't normalize the phone here — `_resolve_promo_code` will
        normalize once when it has the request payload. Storage-side
        we already match on the exact string the user typed, same as
        the existing customer-resolution code.
    """
    if not phone:
        # No phone → impossible to identify the customer → treat as
        # "first order" (permissive). The order-create endpoint requires
        # a phone, so this path only fires for malformed cart-calculate
        # calls.
        return False

    qs = (
        Order.objects
        .filter(country=country, customer_phone=phone)
        .filter(status__in=COUNTING_STATUSES)
        .exclude(payment_status__in=NON_COUNTING_PAYMENT_STATUSES)
        .exclude(auto_expired=True)
    )
    return qs.exists()


def check_promo_usage(promo, *, country, phone):
    """Validate the usage_limit rule on a promo code for a specific
    customer-by-phone.

    Returns (True, None) when the code is OK to apply, otherwise
    (False, error_code) where error_code is one of:
        "PROMO_FIRST_ORDER_ONLY"  — code requires first order, but the
                                    customer already has prior orders.

    Caller maps the error_code to its own response shape:
        api_error("PROMO_FIRST_ORDER_ONLY", "Промокод действует только для первого заказа", status=400)
    """
    if promo is None:
        return True, None

    limit = getattr(promo, "usage_limit", PromoCode_USAGE_LIMIT_NONE)

    if limit == PromoCode_USAGE_LIMIT_NONE:
        return True, None

    if limit == PromoCode_USAGE_LIMIT_FIRST_ORDER:
        if has_prior_qualifying_order(country, phone):
            return False, "PROMO_FIRST_ORDER_ONLY"
        return True, None

    # Unknown future limit type — fail-closed (reject the promo rather
    # than silently allowing it). If we ever ship a new limit type and
    # forget to update this function, the worst case is "promo rejected
    # in production" — visible and fixable, not a silent revenue leak.
    return False, "PROMO_LIMIT_UNKNOWN"


# Module-level shortcuts so we don't import PromoCode just for the
# constants — avoids a circular import (PromoCode → ... → promo_rules).
# These are duplicated string literals; if the model's choices ever
# change values, both sides need updating. Worth the trade for
# import-time safety.
PromoCode_USAGE_LIMIT_NONE = "none"
PromoCode_USAGE_LIMIT_FIRST_ORDER = "first_order"
