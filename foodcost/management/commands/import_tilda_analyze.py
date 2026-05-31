"""
Analyze a Tilda CSV export without touching the database.

Usage:

    python manage.py import_tilda_analyze /path/to/leads.csv --country uzbekistan

Outputs:
  * Row-level stats (parsed / errored / warning counts)
  * Customer dedupe (unique phones vs total rows)
  * Order-status of any tranid that's already been imported (idempotency check)
  * Dish-matching report:
      - how many CSV dish names match ERP Dish.name exactly (case-insensitive)
      - how many don't match → these will land in OrderItem with dish=NULL
        and dish_name_snapshot set
  * Promo code summary (the codes Tilda recorded, NOT linked to ERP PromoCode)
  * Suggested next steps

Optionally exports the unmatched dish names to a CSV for manual review.

The companion command `import_tilda_orders` does the actual writing —
this one is safe to run anytime, makes no changes.
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from foodcost.models import Country, Dish, Order, Customer
from foodcost.tilda_import import (
    read_tilda_csv,
    dish_name_index,
    unique_phones,
    order_count_by_phone,
)


class Command(BaseCommand):
    help = (
        "Analyze a Tilda CSV export. Reports row stats, customer dedupe, "
        "dish-matching coverage, and idempotency status. No DB writes."
    )

    def add_arguments(self, parser):
        parser.add_argument("csv_path", help="Path to the Tilda leads CSV.")
        parser.add_argument(
            "--country",
            required=True,
            help="Country slug to scope dish/customer lookups (e.g. 'uzbekistan').",
        )
        parser.add_argument(
            "--unmatched-dishes-out",
            default=None,
            help=(
                "Optional path: write a CSV of dish names that won't "
                "match any ERP Dish, sorted by occurrence count. Useful "
                "for cleaning up the catalog before re-running analyze."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help=(
                "If >0, only analyze the first N parsed rows. Helps "
                "preview a huge export quickly. 0 = all rows."
            ),
        )

    def handle(self, *args, **opts):
        csv_path = opts["csv_path"]
        country_slug = opts["country"]
        out_path = opts["unmatched_dishes_out"]
        limit = opts["limit"]

        try:
            country = Country.objects.get(slug=country_slug)
        except Country.DoesNotExist:
            raise CommandError(f"Country with slug={country_slug!r} not found")

        # --- Parse ---
        self.stdout.write(self.style.NOTICE(f"Reading {csv_path}…"))
        rows, parse_errors = read_tilda_csv(csv_path)

        if parse_errors:
            self.stdout.write(self.style.ERROR(
                f"\n{len(parse_errors)} fatal parse errors (rows skipped):"
            ))
            for err in parse_errors[:10]:
                self.stdout.write(f"  - {err}")
            if len(parse_errors) > 10:
                self.stdout.write(f"  … and {len(parse_errors) - 10} more")

        if limit > 0:
            rows = rows[:limit]
            self.stdout.write(self.style.WARNING(
                f"Limiting analysis to first {limit} parsed rows"
            ))

        total_warns = sum(len(r.warnings) for r in rows)

        self.stdout.write("")
        self.stdout.write(self.style.NOTICE("=== Row stats ==="))
        self.stdout.write(f"  Parsed OK:        {len(rows)}")
        self.stdout.write(f"  Parse errors:     {len(parse_errors)}")
        self.stdout.write(f"  Soft warnings:    {total_warns}")

        # Warning categories (top 5)
        if total_warns:
            warn_kinds = Counter()
            for r in rows:
                for w in r.warnings:
                    warn_kinds[w.split(":")[0]] += 1
            self.stdout.write("  Warning kinds:")
            for k, c in warn_kinds.most_common(5):
                self.stdout.write(f"    {c:5d}  {k}")

        # --- Customers ---
        self.stdout.write("")
        self.stdout.write(self.style.NOTICE("=== Customers ==="))
        phones = unique_phones(rows)
        self.stdout.write(f"  Unique phones in CSV:           {len(phones)}")

        # How many of those already exist as Customer in the ERP for
        # this country? We match by phone-digits-only on a Python side
        # because the ERP stores phones as-typed.
        existing_phone_keys = set()
        for c in Customer.objects.filter(country=country).only("phone"):
            digits = "".join(ch for ch in c.phone if ch.isdigit())
            if digits:
                existing_phone_keys.add(digits)
        will_match = phones & existing_phone_keys
        will_create = phones - existing_phone_keys
        self.stdout.write(f"  Already in ERP (match by phone):{len(will_match)}")
        self.stdout.write(f"  Will create new Customer rows:   {len(will_create)}")

        # Top frequent buyers in the CSV
        counts = order_count_by_phone(rows)
        top = sorted(counts.items(), key=lambda x: -x[1])[:5]
        if top:
            self.stdout.write("  Top customers by order count:")
            for phone_key, n in top:
                # Find first row with this phone_key for the name
                name = next(
                    (r.customer_name for r in rows if r.phone_key == phone_key),
                    "",
                )
                self.stdout.write(f"    {n:3d} orders  {phone_key}  ({name})")

        # --- Idempotency ---
        self.stdout.write("")
        self.stdout.write(self.style.NOTICE("=== Already imported ==="))
        csv_refs = {r.tranid for r in rows}
        existing_refs = set(
            Order.objects
            .filter(legacy_source="tilda", legacy_order_ref__in=csv_refs)
            .values_list("legacy_order_ref", flat=True)
        )
        will_skip = csv_refs & existing_refs
        will_insert = csv_refs - existing_refs
        self.stdout.write(f"  CSV tranids:                {len(csv_refs)}")
        self.stdout.write(f"  Already imported (skip):    {len(will_skip)}")
        self.stdout.write(f"  Will insert as new orders:  {len(will_insert)}")

        # --- Dish matching ---
        self.stdout.write("")
        self.stdout.write(self.style.NOTICE("=== Dish matching ==="))

        # Build a case-insensitive lookup over the ERP catalog.
        # We match against Dish.name only (strict — the user chose
        # exact case-insensitive matching for this import).
        erp_dishes = {
            d.name.strip().lower(): d
            for d in Dish.objects.filter(country=country).only("id", "name")
        }
        self.stdout.write(f"  ERP catalog (country={country_slug}): "
                          f"{len(erp_dishes)} dishes")

        csv_dish_names = dish_name_index(rows)
        self.stdout.write(f"  Unique dish names in CSV:           "
                          f"{len(csv_dish_names)}")

        matched, unmatched = [], []
        # Count item-line occurrences too, not just distinct names —
        # one unmatched popular name can affect many orders.
        line_occurrence = Counter()
        for r in rows:
            for it in r.items:
                line_occurrence[it.name.strip().lower()] += 1

        for key in csv_dish_names:
            if key in erp_dishes:
                matched.append(key)
            else:
                unmatched.append(key)
        self.stdout.write(f"  Matched (case-insensitive exact):   {len(matched)}")
        self.stdout.write(f"  Unmatched (dish=NULL on insert):    {len(unmatched)}")

        if unmatched:
            self.stdout.write("")
            self.stdout.write("  Top 15 unmatched dish names (by line occurrence):")
            unmatched_sorted = sorted(
                unmatched,
                key=lambda k: -line_occurrence.get(k, 0),
            )
            for key in unmatched_sorted[:15]:
                originals = csv_dish_names[key]
                shown = sorted(originals)[0]  # one representative
                self.stdout.write(
                    f"    {line_occurrence[key]:4d}× {shown!r}"
                )
            if len(unmatched) > 15:
                self.stdout.write(f"    … and {len(unmatched) - 15} more")

        # Optional CSV export of unmatched
        if out_path and unmatched:
            self._export_unmatched_csv(
                out_path, csv_dish_names, unmatched, line_occurrence,
            )
            self.stdout.write(self.style.SUCCESS(
                f"\n  Unmatched dishes exported to {out_path}"
            ))

        # --- Promo codes ---
        self.stdout.write("")
        self.stdout.write(self.style.NOTICE("=== Promo codes in CSV ==="))
        promo_count = Counter(r.promo_code for r in rows if r.promo_code)
        self.stdout.write(f"  Distinct codes: {len(promo_count)}")
        if promo_count:
            self.stdout.write("  Top 10 by usage (won't be linked to ERP PromoCode):")
            for code, n in promo_count.most_common(10):
                self.stdout.write(f"    {n:4d}× {code!r}")

        # --- Next steps ---
        self.stdout.write("")
        self.stdout.write(self.style.NOTICE("=== Next steps ==="))
        if not parse_errors and len(will_insert) > 0:
            self.stdout.write(self.style.SUCCESS(
                "  ✓ CSV is import-ready."
            ))
            self.stdout.write(
                "  Run import (dry-run first) with:"
            )
            self.stdout.write(
                f"    python manage.py import_tilda_orders "
                f"{csv_path} --country {country_slug} --dry-run"
            )
            self.stdout.write(
                f"    python manage.py import_tilda_orders "
                f"{csv_path} --country {country_slug} --commit"
            )
        elif parse_errors:
            self.stdout.write(self.style.ERROR(
                "  ✗ Fix the parse errors above before importing."
            ))
        elif not will_insert:
            self.stdout.write(self.style.SUCCESS(
                "  ✓ Nothing to import — every CSV row is already imported."
            ))

    # -- helpers --

    def _export_unmatched_csv(self, path, csv_dish_names, unmatched, occurrences):
        """Write a single-column CSV of unmatched dish names with their
        occurrence count. Sorted by occurrences DESC so the catalog
        manager fixes the most-impactful items first."""
        import csv
        rows = sorted(
            unmatched,
            key=lambda k: -occurrences.get(k, 0),
        )
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["occurrences", "dish_name_in_csv"])
            for key in rows:
                originals = sorted(csv_dish_names[key])
                w.writerow([occurrences.get(key, 0), originals[0]])
