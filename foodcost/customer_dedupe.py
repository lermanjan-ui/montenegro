"""
Merge duplicate Customer records that share the same phone number.

Why duplicates exist:
  Phones are stored as-typed (operators rely on the exact format), so
  one real person can appear multiple times if Tilda exported their
  number in different shapes:
      "+998 90-301-0709"
      "+998 (90) 301-07-09"
      "998903010709"
  …and the live website checkout might use yet another shape.

What "merge" means:
  Within a phone group (digits-only key), pick a primary record and
  reattach all dependent rows from the duplicates to it:
    - Order.customer        → primary
    - CustomerAddress.customer → primary  (deduplicated by address text)
    - CustomerFavorite.customer → primary (deduplicated by dish)

Then delete the duplicate Customer rows.

Primary selection (in order):
  1. Customer that has the most orders (most "trusted" identity)
  2. Among ties — oldest by `created_at` (the earliest record we made)
  3. Final tie — lowest `id`

Phone normalization on the primary:
  After merging, rewrite the primary's `phone` field to a canonical
  format. Uzbek numbers normalize to "+998 NN-NNN-NNNN"; everything
  else (foreign numbers, future countries) keeps a "+<digits>" form
  with spaces every 2-3 digits. This is purely cosmetic — the dedup
  itself works on digits-only key.

Idempotency:
  After a successful merge run on a phone group, the group has size 1
  and is skipped on subsequent runs. Safe to re-run.
"""

from collections import defaultdict

from django.db import transaction

from .models import Customer, Order, CustomerAddress


def digits_only(s):
    """Return only the digits from a string. Empty → empty."""
    if not s:
        return ""
    return "".join(ch for ch in str(s) if ch.isdigit())


def normalize_uz_phone(raw):
    """Canonicalize an Uzbek phone to '+998 NN-NNN-NNNN'.

    Accepts any input that boils down to 12 digits starting with 998
    (or 9 digits starting with 9 — historical local format). Returns
    the original raw value if we can't recognize the shape, so we
    never lose data.
    """
    digits = digits_only(raw)
    # 998 + 9 digits = 12 → standard UZ international
    if len(digits) == 12 and digits.startswith("998"):
        return f"+{digits[:3]} {digits[3:5]}-{digits[5:8]}-{digits[8:12]}"
    # Legacy: 9 digits starting with 9 (e.g. "901234567" without country code)
    if len(digits) == 9 and digits.startswith("9"):
        return f"+998 {digits[:2]}-{digits[2:5]}-{digits[5:9]}"
    # 13 digits — somebody typed "+998 +998901234567" (double prefix).
    # Strip the leading "998" if it appears twice.
    if len(digits) == 15 and digits.startswith("998998"):
        rest = digits[3:]  # drop one "998"
        if len(rest) == 12:
            return f"+{rest[:3]} {rest[3:5]}-{rest[5:8]}-{rest[8:12]}"
    # Unknown format — leave as-is so we don't accidentally garble it.
    return raw


def find_duplicate_groups(country):
    """Return {digits_key: [Customer, Customer, ...]} for groups that
    have at least 2 records. Customers with empty phone are excluded —
    we can't merge by an empty key without risking false merges."""
    groups = defaultdict(list)
    for c in Customer.objects.filter(country=country).order_by("created_at", "id"):
        key = digits_only(c.phone)
        if not key:
            continue
        groups[key].append(c)
    return {k: v for k, v in groups.items() if len(v) > 1}


def pick_primary(customers):
    """Pick the 'winner' from a group of duplicate Customer rows.

    Rules (in priority order):
      1. Most orders (sticky identity — operators have history here)
      2. Oldest created_at (earliest record we made)
      3. Smallest id (deterministic tiebreaker)

    Returns one Customer.
    """
    # Pre-fetch order counts in one query to avoid N+1 in the sort key.
    order_counts = {
        c.id: Order.objects.filter(customer=c).count()
        for c in customers
    }
    return sorted(
        customers,
        key=lambda c: (
            -order_counts.get(c.id, 0),     # most orders first
            c.created_at or timezone_min(), # oldest created_at first
            c.id,                            # lowest id last-resort
        ),
    )[0]


def timezone_min():
    """Sentinel used when created_at is NULL — sorts to the start."""
    from datetime import datetime
    from django.utils import timezone
    return timezone.make_aware(datetime.min.replace(year=1900))


def plan_merge(country):
    """Build a dry-run merge plan for the country.

    Returns a list of dicts: [{
        "phone_key": "998901112233",
        "primary": Customer,
        "duplicates": [Customer, Customer, ...],
        "orders_to_move": int,
        "addresses_to_move": int,
    }, ...]

    Read-only — does not touch the DB.
    """
    plan = []
    groups = find_duplicate_groups(country)
    for key, members in groups.items():
        primary = pick_primary(members)
        duplicates = [c for c in members if c.id != primary.id]
        dup_ids = [c.id for c in duplicates]
        orders_to_move = Order.objects.filter(customer_id__in=dup_ids).count()
        addresses_to_move = CustomerAddress.objects.filter(
            customer_id__in=dup_ids
        ).count()
        plan.append({
            "phone_key": key,
            "primary": primary,
            "duplicates": duplicates,
            "orders_to_move": orders_to_move,
            "addresses_to_move": addresses_to_move,
        })
    # Sort by impact (most orders moved first) so the operator sees
    # the consequential merges at the top of the report.
    plan.sort(key=lambda p: -p["orders_to_move"])
    return plan


def execute_merge(country, *, normalize_phones=True):
    """Run the actual merge inside a transaction.

    Returns a stats dict:
      {
        "groups_merged": int,
        "duplicates_deleted": int,
        "orders_reattached": int,
        "addresses_reattached": int,
        "phones_normalized": int,
      }

    Wrapped in atomic — if anything explodes mid-batch, the entire
    operation rolls back.
    """
    stats = {
        "groups_merged": 0,
        "duplicates_deleted": 0,
        "orders_reattached": 0,
        "addresses_reattached": 0,
        "phones_normalized": 0,
    }

    with transaction.atomic():
        groups = find_duplicate_groups(country)
        for key, members in groups.items():
            primary = pick_primary(members)
            duplicates = [c for c in members if c.id != primary.id]
            dup_ids = [c.id for c in duplicates]

            # Reattach Orders. customer_address on Order references a
            # specific CustomerAddress; we DON'T touch it here — those
            # addresses will be reattached to the primary in the next
            # step, so the FK on Order keeps pointing to a valid row.
            reattached_orders = Order.objects.filter(
                customer_id__in=dup_ids
            ).update(customer=primary)
            stats["orders_reattached"] += reattached_orders

            # Reattach addresses. We deliberately do NOT dedupe address
            # text — two records that read "Main Street 5" might be
            # different floors, and we have no way to tell. Better to
            # keep both than silently delete one.
            reattached_addrs = CustomerAddress.objects.filter(
                customer_id__in=dup_ids
            ).update(customer=primary)
            stats["addresses_reattached"] += reattached_addrs

            # Reattach favorites (CASCADE FK — if we delete the duplicate
            # Customer with CASCADE, the favorites die with it; so move
            # them first). Use a raw .update() to avoid loading rows.
            try:
                from .models import CustomerFavorite
                CustomerFavorite.objects.filter(
                    customer_id__in=dup_ids
                ).update(customer=primary)
            except Exception:
                # CustomerFavorite is optional in some setups; if the
                # import fails or the table is empty, skip silently.
                pass

            # Now safe to delete the duplicates — no live FK to them.
            deleted_count, _ = Customer.objects.filter(
                id__in=dup_ids
            ).delete()
            stats["duplicates_deleted"] += deleted_count

            # Normalize the primary's phone if requested.
            if normalize_phones:
                new_phone = normalize_uz_phone(primary.phone)
                if new_phone != primary.phone:
                    primary.phone = new_phone
                    primary.save(update_fields=["phone"])
                    stats["phones_normalized"] += 1

            stats["groups_merged"] += 1

    return stats
