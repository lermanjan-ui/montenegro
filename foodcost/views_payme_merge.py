"""
TEMPORARY — one-off merge of duplicate "Payme" PaymentMethod rows.

Why the duplicate exists:
  The cabinet operator manually created a payment method (e.g. "Pay me")
  so it could be picked on manual orders. Later the public website went
  live, and its checkout resolves Payme via:

      PaymentMethod.objects.get_or_create(country=country, name="Payme", ...)

  (see public_api._resolve_payment_method / PAYMENT_METHOD_LABELS["payme"]).
  The site's EXACT name "Payme" didn't match the manual "Pay me", so the
  site created its OWN row -> two rows for the same thing.

What "merge" does (per country, inside one transaction):
  1. Pick the KEEPER. It MUST end up named exactly "Payme", otherwise the
     site's get_or_create would recreate the duplicate on the next online
     order. Preference:
        a) an existing row whose name is exactly "Payme" (the site's row),
           tie-broken by most orders, then lowest id;
        b) if none is exactly "Payme", the payme-variant with most orders
           is kept and RENAMED to "Payme".
  2. Repoint every dependent FK from the duplicates to the keeper:
        - Order.payment_method            (on_delete=SET_NULL)
        - OrderSource.default_payment_method (on_delete=SET_NULL)
     This MUST happen before deletion — otherwise SET_NULL would wipe the
     method off historical orders (and break cash/bank split in analytics).
  3. Normalize the keeper: name="Payme", is_cash=False (Payme is an online
     method, never cash), is_active=True.
  4. Delete the duplicate rows. Their names are NOT in PAYMENT_METHOD_LABELS,
     so the site will never recreate them.

Safety:
  - superuser only
  - GET = read-only analysis (shows the plan, changes nothing)
  - POST = execute, wrapped in transaction.atomic() (all-or-nothing)
  - idempotent: after a successful merge each country has a single "Payme",
    so re-running finds nothing to do.

DELETE THIS FILE + its route in foodcost/urls.py once the merge is done.
"""

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import render

from .models import Country, PaymentMethod, Order, OrderSource


CANONICAL_NAME = "Payme"


def _is_payme_variant(name):
    """'Payme', 'pay me', 'PAYME', 'Pay Me', ' payme ' -> True."""
    return (name or "").strip().lower().replace(" ", "") == "payme"


def _order_count(pm):
    return Order.objects.filter(payment_method=pm).count()


def _source_count(pm):
    return OrderSource.objects.filter(default_payment_method=pm).count()


def _build_plans():
    """Read-only. Returns a list of per-country merge plans (only countries
    that actually have 2+ Payme-variant rows)."""
    plans = []

    for country in Country.objects.all().order_by("name"):
        rows = [
            pm for pm in PaymentMethod.objects.filter(country=country)
            if _is_payme_variant(pm.name)
        ]
        if len(rows) < 2:
            continue  # 0 or 1 -> nothing to merge

        counts = {pm.id: _order_count(pm) for pm in rows}

        exact = [pm for pm in rows if pm.name == CANONICAL_NAME]
        pool = exact if exact else rows
        keeper = sorted(pool, key=lambda pm: (-counts[pm.id], pm.id))[0]

        dups = [pm for pm in rows if pm.id != keeper.id]

        def row_info(pm):
            return {
                "id": pm.id,
                "name": pm.name,
                "is_active": pm.is_active,
                "is_cash": pm.is_cash,
                "orders": counts[pm.id],
                "sources": _source_count(pm),
            }

        plans.append({
            "country": country,
            "keeper": row_info(keeper),
            "keeper_obj": keeper,
            "dups": [row_info(pm) for pm in dups],
            "dup_objs": dups,
            "orders_to_move": sum(counts[pm.id] for pm in dups),
            "sources_to_move": sum(_source_count(pm) for pm in dups),
            "keeper_needs_rename": keeper.name != CANONICAL_NAME,
            "keeper_needs_cash_fix": keeper.is_cash,
        })

    return plans


@login_required(login_url="/login/")
def payme_merge_page(request):

    if not request.user.is_superuser:
        return HttpResponseForbidden("Доступно только суперадмину")

    result = None

    if request.method == "POST" and request.POST.get("action") == "merge":
        result = {
            "groups_merged": 0,
            "orders_reattached": 0,
            "sources_reattached": 0,
            "duplicates_deleted": 0,
            "details": [],
        }

        with transaction.atomic():
            for plan in _build_plans():
                keeper = plan["keeper_obj"]
                dup_ids = [pm.id for pm in plan["dup_objs"]]

                moved_orders = (
                    Order.objects
                    .filter(payment_method_id__in=dup_ids)
                    .update(payment_method=keeper)
                )
                moved_sources = (
                    OrderSource.objects
                    .filter(default_payment_method_id__in=dup_ids)
                    .update(default_payment_method=keeper)
                )

                changed = []
                if keeper.name != CANONICAL_NAME:
                    keeper.name = CANONICAL_NAME
                    changed.append("name")
                if keeper.is_cash:
                    keeper.is_cash = False
                    changed.append("is_cash")
                if not keeper.is_active:
                    keeper.is_active = True
                    changed.append("is_active")
                if changed:
                    keeper.save(update_fields=changed)

                deleted, _ = (
                    PaymentMethod.objects.filter(id__in=dup_ids).delete()
                )

                result["groups_merged"] += 1
                result["orders_reattached"] += moved_orders
                result["sources_reattached"] += moved_sources
                result["duplicates_deleted"] += len(dup_ids)
                result["details"].append({
                    "country": plan["country"].name,
                    "keeper_id": keeper.id,
                    "moved_orders": moved_orders,
                    "moved_sources": moved_sources,
                    "deleted_ids": dup_ids,
                })

    # Always show fresh analysis (after a merge it should be empty).
    plans = _build_plans()

    return render(request, "foodcost/payme_merge.html", {
        "plans": plans,
        "result": result,
        "canonical": CANONICAL_NAME,
    })
