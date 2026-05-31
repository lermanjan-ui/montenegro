"""
Import a Tilda CSV export into the ERP as legacy Order records.

Two phases — always run --dry-run first:

    # 1. Preview only — no DB writes, but DOES read the DB for matching
    python manage.py import_tilda_orders /path/to/leads.csv \\
        --country uzbekistan --dry-run

    # 2. Commit — wraps the whole import in a transaction
    python manage.py import_tilda_orders /path/to/leads.csv \\
        --country uzbekistan --commit

What it creates per CSV row:
  * Customer (if no existing one matches by digits-only phone)
  * Order with:
      - is_legacy_import=True
      - legacy_source="tilda"
      - legacy_order_ref=tranid  (idempotency key)
      - status="done"            (Tilda doesn't distinguish completed; user
                                  decision: import as completed orders)
      - payment_status="cash"    (we don't know if/how they paid; cash is
                                  the "outside-ERP payment" sentinel)
      - source = OrderSource("Tilda", country=...) — auto-created
      - order_date = CSV `Дата`
      - total_amount = CSV `Сумма заказа` (NOT the sum of item prices —
        Tilda's total is the operator's final total)
      - customer_phone = phone as-typed
      - cashier_comment = CSV `Промокод` + utm_campaign hint (for ops audit)
      - customer_comment = CSV `Комментарий`
  * OrderItem per parsed item line:
      - dish FK if a case-insensitive exact name match exists in Dish
      - dish_name_snapshot = original CSV name (always set)
      - quantity, price_snapshot, total_price = qty * unit_price

What it does NOT do:
  * Does NOT link Tilda promo codes to ERP PromoCode. The Tilda export
    has only the text label ("W26", "S12"); we store it in
    cashier_comment for traceability.
  * Does NOT touch CustomerAddress. Delivery address goes into the
    Order's delivery_address text field; building the structured
    address tree per import row would mis-match Yandex Maps coords.
  * Does NOT compute cost_snapshot per item — we don't have historical
    cost data for these orders, leaving cost_snapshot=0 (the default).
  * Does NOT fire any side effects: no Meta CAPI, no Telegram, no
    payment callbacks. The orders enter the DB silently.

Idempotency:
  Each CSV row's tranid lands in Order.legacy_order_ref. Re-running the
  command after a partial import re-reads the CSV, sees existing
  (legacy_source="tilda", legacy_order_ref=tranid) rows, and SKIPS them.
  This is safe to re-run on top of a partial commit.

Transaction safety:
  --commit wraps everything in one atomic block. If a single row fails
  in an unexpected way, the entire batch rolls back — you start over
  with --dry-run to find the bad row.
"""

from collections import Counter

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from foodcost.models import (
    Country,
    Customer,
    Dish,
    Order,
    OrderItem,
    OrderSource,
)
from foodcost.tilda_import import read_tilda_csv


# Name of the OrderSource for these imports. We don't reuse "Сайт"
# (the live website's source) so analytics queries can isolate the
# Tilda backfill if they want.
TILDA_SOURCE_NAME = "Tilda (импорт)"

# Marker stored in Order.legacy_source for idempotency lookups.
LEGACY_SOURCE = "tilda"


class Command(BaseCommand):
    help = "Import a Tilda CSV export as legacy Order records."

    def add_arguments(self, parser):
        parser.add_argument("csv_path")
        parser.add_argument("--country", required=True)
        action_group = parser.add_mutually_exclusive_group(required=True)
        action_group.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be inserted without touching the DB.",
        )
        action_group.add_argument(
            "--commit",
            action="store_true",
            help=(
                "Actually write to the DB. Wraps the entire import in an "
                "atomic transaction — any single failure rolls back the lot."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Cap the number of rows imported. 0 = all.",
        )

    def handle(self, *args, **opts):
        csv_path = opts["csv_path"]
        country_slug = opts["country"]
        dry_run = opts["dry_run"]
        do_commit = opts["commit"]
        limit = opts["limit"]

        try:
            country = Country.objects.get(slug=country_slug)
        except Country.DoesNotExist:
            raise CommandError(f"Country with slug={country_slug!r} not found")

        rows, parse_errors = read_tilda_csv(csv_path)
        if parse_errors:
            self.stdout.write(self.style.ERROR(
                f"Refusing to import: {len(parse_errors)} fatal parse errors. "
                "Fix the CSV or run import_tilda_analyze for details."
            ))
            for err in parse_errors[:5]:
                self.stdout.write(f"  - {err}")
            return

        if limit > 0:
            rows = rows[:limit]
            self.stdout.write(self.style.WARNING(
                f"Limiting to first {limit} rows"
            ))

        # Pre-load the dish lookup index once. Strict matching:
        # case-insensitive exact match on Dish.name. Names with add-on
        # annotations like "Альфредо (Добавить: ...)" won't match, by
        # design — the user chose this strategy.
        dish_index = {
            d.name.strip().lower(): d
            for d in Dish.objects.filter(country=country).only("id", "name")
        }

        # Pre-load existing legacy refs for fast idempotency check.
        existing_refs = set(
            Order.objects
            .filter(legacy_source=LEGACY_SOURCE)
            .values_list("legacy_order_ref", flat=True)
        )

        # Pre-load Customer phone-key index once. The Customer table
        # has phones stored as-typed, so we build the digit-only key
        # client-side for matching against TildaRow.phone_key.
        customers_by_key = {}
        for c in Customer.objects.filter(country=country).only("id", "phone"):
            key = "".join(ch for ch in c.phone if ch.isdigit())
            if key:
                # If duplicates exist (e.g. legacy data quirks), we
                # keep the FIRST one we find. The duplicates remain
                # in the DB unchanged.
                customers_by_key.setdefault(key, c)

        # Banner
        self.stdout.write("")
        self.stdout.write(self.style.NOTICE(
            f"=== Importing {len(rows)} rows "
            f"({'DRY-RUN' if dry_run else 'COMMIT'}) ==="
        ))
        self.stdout.write(f"  Country:        {country.name} (slug={country.slug})")
        self.stdout.write(f"  Catalog dishes: {len(dish_index)}")
        self.stdout.write(f"  Existing refs:  {len(existing_refs)} (will skip if matched)")
        self.stdout.write(f"  Existing custs: {len(customers_by_key)}")
        self.stdout.write("")

        if do_commit:
            # One big atomic so a mid-batch failure rolls back the lot.
            # The downside is memory: we hold all rows in DB session
            # until the commit. For ~1.5k rows that's negligible; for
            # 100k it'd need batching.
            with transaction.atomic():
                stats = self._do_import(
                    rows=rows,
                    country=country,
                    dish_index=dish_index,
                    existing_refs=existing_refs,
                    customers_by_key=customers_by_key,
                    dry_run=False,
                )
        else:
            stats = self._do_import(
                rows=rows,
                country=country,
                dish_index=dish_index,
                existing_refs=existing_refs,
                customers_by_key=customers_by_key,
                dry_run=True,
            )

        # Summary
        self.stdout.write("")
        self.stdout.write(self.style.NOTICE("=== Summary ==="))
        self.stdout.write(f"  Orders inserted:      {stats['orders']}")
        self.stdout.write(f"  Orders skipped (dup): {stats['skipped']}")
        self.stdout.write(f"  Order items inserted: {stats['items']}")
        self.stdout.write(f"  Items with dish FK:   {stats['items_matched']}")
        self.stdout.write(f"  Items without (snap): {stats['items_unmatched']}")
        self.stdout.write(f"  New customers:        {stats['new_customers']}")
        self.stdout.write(f"  Reused customers:     {stats['reused_customers']}")
        if dry_run:
            self.stdout.write(self.style.WARNING(
                "\nDRY-RUN: nothing was written. Re-run with --commit to apply."
            ))
        else:
            self.stdout.write(self.style.SUCCESS("\n✓ Import complete."))

    # -- core --

    def _do_import(self, *, rows, country, dish_index, existing_refs,
                   customers_by_key, dry_run):
        """Walk the parsed rows, create DB rows.

        When dry_run is True we still walk the same code path but skip
        the .create() calls — that way the summary numbers reflect the
        real plan (including idempotency skips).
        """
        stats = Counter()

        # Make sure the source row exists before the loop. In dry-run
        # we don't actually create it; we use a sentinel.
        if dry_run:
            source = None
        else:
            source, _ = OrderSource.objects.get_or_create(
                country=country,
                name=TILDA_SOURCE_NAME,
                defaults={"is_active": True},
            )

        for r in rows:
            # Idempotency — skip rows already imported.
            if r.tranid in existing_refs:
                stats["skipped"] += 1
                continue

            # Customer resolution. Match by digits-only phone; fall back
            # to creating a new Customer for unseen numbers.
            customer = customers_by_key.get(r.phone_key)
            if customer is None:
                stats["new_customers"] += 1
                if not dry_run:
                    customer = Customer.objects.create(
                        country=country,
                        phone=r.customer_phone,
                        name=r.customer_name or "Клиент",
                    )
                    customers_by_key[r.phone_key] = customer
            else:
                stats["reused_customers"] += 1

            # Build an operator-readable comment that preserves the
            # Tilda-side promo code and any utm tag, so analytics can
            # still find them later.
            cashier_bits = []
            if r.promo_code:
                cashier_bits.append(f"Промокод (Tilda): {r.promo_code}")
            cashier_comment = "\n".join(cashier_bits)

            order_kwargs = dict(
                country=country,
                source=source,
                customer=customer,
                customer_name=r.customer_name,
                customer_phone=r.customer_phone,
                delivery_address=r.delivery_address,
                customer_comment=r.customer_comment,
                cashier_comment=cashier_comment,
                # Tilda's "Сумма заказа" is the final amount the
                # operator confirmed — trust it as total. Subtotal is
                # the same: we have no breakdown.
                subtotal_amount=r.total,
                discount_amount=0,
                delivery_amount=0,
                total_amount=r.total,
                # Per user decision: import as completed, payment unknown.
                status=Order.STATUS_DONE,
                payment_status=Order.PAYMENT_STATUS_CASH,
                fulfillment_method=Order.FULFILLMENT_DELIVERY,
                # Legacy import markers — what the cron / analytics filter on.
                is_legacy_import=True,
                legacy_source=LEGACY_SOURCE,
                legacy_order_ref=r.tranid,
                # order_date is the human-readable "когда сделан".
                order_date=self._aware(r.order_date),
            )

            if dry_run:
                stats["orders"] += 1
                # Count item matches even in dry-run so the report
                # accurately reflects what'd happen.
                for it in r.items:
                    stats["items"] += 1
                    if it.name.strip().lower() in dish_index:
                        stats["items_matched"] += 1
                    else:
                        stats["items_unmatched"] += 1
                continue

            order = Order.objects.create(**order_kwargs)

            # public_order_number — same convention as live orders
            # ("RCN-YYYY-NNNNNN"). Set after create() so the PK is known.
            year = (order.order_date or timezone.now()).year
            order.public_order_number = f"RCN-{year}-{order.id:06d}"
            order.save(update_fields=["public_order_number"])

            stats["orders"] += 1

            for it in r.items:
                dish_obj = dish_index.get(it.name.strip().lower())
                unit_price = it.unit_price
                line_total = unit_price * it.quantity

                OrderItem.objects.create(
                    order=order,
                    dish=dish_obj,
                    dish_name_snapshot=it.name,
                    quantity=it.quantity,
                    price_snapshot=unit_price,
                    total_price=line_total,
                )
                stats["items"] += 1
                if dish_obj is not None:
                    stats["items_matched"] += 1
                else:
                    stats["items_unmatched"] += 1

        return stats

    # -- helpers --

    def _aware(self, dt):
        """Localize a naive datetime from the CSV to the project's
        timezone. Django won't accept a naive datetime when USE_TZ=True.
        """
        if dt is None:
            return None
        if timezone.is_aware(dt):
            return dt
        return timezone.make_aware(dt, timezone.get_current_timezone())
