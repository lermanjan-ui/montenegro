"""
Send queued Meta CAPI Purchase events for paid orders.

Run via cron / Render Scheduler every minute:

    python manage.py send_pending_meta_purchases

Idempotent — running it twice in a row is a no-op for orders already sent.

What it does:
  - finds orders with payment_status=paid, paid 15+ minutes ago, with a
    meta_event_id set, that haven't been CAPI-sent yet, and aren't in
    cancelled/refunded/expired state
  - for each, calls foodcost.meta_capi.send_meta_capi_purchase(order)
  - on success sets meta_capi_sent=True (idempotency latch)
  - on transient error logs and continues; the order stays unflipped and
    will be retried on the next tick
  - on permanent misconfiguration (missing env vars) logs once and exits
    the whole batch — no point retrying every minute until vars are set

What it does NOT do:
  - does NOT modify the order otherwise (no status change, no comment)
  - does NOT send for cash orders (payment_status=cash, not paid) — the
    online-payment funnel is the only one Meta Pixel + CAPI tracks
  - does NOT fire if meta_event_id is empty (no Pixel event to dedup
    against → sending CAPI alone would double-count vs the Pixel)

Why 15 minutes:
  Operators can cancel an order in the first 15 min after payment.
  Firing Purchase only after that window avoids counting refunded
  conversions in Meta's reporting and ad-budget optimization.

Why a cron and not e.g. a delayed Celery task fired at payment time:
  We don't have Celery in this project (Render free tier). Cron + a
  simple WHERE filter is operationally simpler and just as accurate
  with a 1-minute granularity.
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from foodcost.models import Order
from foodcost.meta_capi import (
    send_meta_capi_purchase,
    MetaCapiError,
    MetaCapiNotConfigured,
)


# Window between payment_paid_at and CAPI fire. 15 min matches the operator
# cancellation grace window — most refunds happen well within it.
CAPI_DELAY_MINUTES = 15


class Command(BaseCommand):
    help = (
        "Send the queued Meta CAPI Purchase events for orders paid more "
        "than 15 minutes ago. Idempotent; safe to run on a 1-minute cron."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help=(
                "Show which orders would be sent without actually calling "
                "Meta. Doesn't flip meta_capi_sent."
            ),
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=200,
            help=(
                "Max orders to process in one tick. Prevents a backlog "
                "after downtime from monopolizing the cron run. Default 200."
            ),
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options["limit"]

        cutoff = timezone.now() - timedelta(minutes=CAPI_DELAY_MINUTES)

        # Eligible orders:
        #   - payment confirmed (paid) and the timestamp is old enough
        #   - Meta event_id is set (from frontend) and CAPI not sent yet
        #   - not in any "this purchase doesn't count" state
        #
        # We exclude all five non-purchase terminal states explicitly
        # rather than relying on status='cancelled' alone, because Payme's
        # state=-2 path sets payment_status=refunded while leaving status
        # at done/cooking. Either signal means "don't count this sale".
        eligible = (
            Order.objects
            .filter(
                payment_status=Order.PAYMENT_STATUS_PAID,
                meta_capi_sent=False,
                payment_paid_at__isnull=False,
                payment_paid_at__lte=cutoff,
            )
            .exclude(meta_event_id="")
            .exclude(
                Q(status=Order.STATUS_CANCELLED)
                | Q(payment_status=Order.PAYMENT_STATUS_REFUNDED)
                | Q(payment_status=Order.PAYMENT_STATUS_CANCELLED)
                | Q(payment_status=Order.PAYMENT_STATUS_EXPIRED)
                | Q(auto_expired=True)
            )
            .order_by("payment_paid_at")[:limit]
        )

        count = eligible.count()
        if count == 0:
            self.stdout.write("[meta-capi] no eligible orders")
            return

        self.stdout.write(
            f"[meta-capi] {count} order(s) ready to send"
            + (" (DRY RUN)" if dry_run else "")
        )

        sent = 0
        errors = 0
        skipped_config = 0

        for order in eligible:
            label = f"#{order.public_order_number or order.id}"

            if dry_run:
                self.stdout.write(
                    f"  [dry-run] {label} would send "
                    f"(event_id={order.meta_event_id[:8]}…, "
                    f"total={order.total_amount})"
                )
                continue

            try:
                send_meta_capi_purchase(order)
            except MetaCapiNotConfigured as e:
                # Permanent — same env config gives same outcome on retry.
                # Log once at WARNING and abort the whole batch; the cron
                # will keep trying next minute but won't fill logs with
                # 200 identical "missing env var" messages per tick.
                self.stdout.write(self.style.ERROR(
                    f"[meta-capi] not configured: {e} — aborting batch"
                ))
                skipped_config = count - sent - errors
                break
            except MetaCapiError as e:
                # Transient — log and continue, leave meta_capi_sent=False
                # so the next tick retries this order.
                errors += 1
                self.stdout.write(self.style.WARNING(
                    f"[meta-capi] {label} transient error: {e}"
                ))
                continue
            except Exception as e:  # noqa: BLE001
                # Unexpected — same logging, but loud. We never want the
                # cron itself to crash and stop processing the rest of
                # the batch.
                errors += 1
                self.stdout.write(self.style.ERROR(
                    f"[meta-capi] {label} unexpected error: {e}"
                ))
                continue

            # Success — set the idempotency flag. update_fields keeps
            # the write minimal and avoids racing with other writers
            # (e.g. operator changing status in another transaction).
            order.meta_capi_sent = True
            order.save(update_fields=["meta_capi_sent", "updated_at"])
            sent += 1
            self.stdout.write(
                self.style.SUCCESS(f"  ✓ {label} sent")
            )

        self.stdout.write("")
        self.stdout.write(
            f"[meta-capi] done: sent={sent} errors={errors}"
            + (f" skipped_config={skipped_config}" if skipped_config else "")
        )
