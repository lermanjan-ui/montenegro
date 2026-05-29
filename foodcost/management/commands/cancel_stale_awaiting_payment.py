"""
Cancel online-payment orders that have been sitting in awaiting_payment
past their TTL. Run via cron / Render Scheduler every 5–60 minutes:

    python manage.py cancel_stale_awaiting_payment

Idempotent — running it twice in a row is a no-op on the second pass.

What it does:
  - finds orders with status=awaiting_payment, auto_expired=False, and
    created_at older than settings.ORDER_AWAITING_PAYMENT_TTL_HOURS
  - inside an atomic + select_for_update transaction (per order), flips:
      auto_expired = True
      payment_status = expired
      status = payment_failed
  - prints a summary to stdout for cron logs

What it does NOT do:
  - does NOT actively call Payme/Click to cancel the gateway-side tx
    (Payme has its own 12h timeout that handles that; Click rarely leaves
    transactions in limbo). The auto_expired flag is what prevents zombie
    revival via late callbacks — see payme_callback / click_callback
    handlers.
  - does NOT touch orders that have already been paid or cancelled by
    other means (cash orders, operator-cancelled, etc.). The filter on
    status=awaiting_payment is exclusive.
  - does NOT delete the order or its Payme transactions — they stay in
    the DB for audit and for `GET /api/public/orders/<n>/` to show the
    user a meaningful "оплата истекла" page.

Run frequency:
  Every 5–15 minutes is fine. The lazy-expire path (in
  order_tracking / order_pay_retry views) catches the common case where
  someone returns to the order page, so this command is only a safety net
  for orders nobody ever revisits.
"""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from foodcost.models import Order


class Command(BaseCommand):
    help = (
        "Mark online-payment orders auto_expired if they have been in "
        "awaiting_payment longer than ORDER_AWAITING_PAYMENT_TTL_HOURS."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would be expired without writing anything.",
        )

    def handle(self, *args, **options):
        ttl_hours = getattr(settings, "ORDER_AWAITING_PAYMENT_TTL_HOURS", 24)
        if not isinstance(ttl_hours, int) or ttl_hours <= 0:
            ttl_hours = 24

        cutoff = timezone.now() - timedelta(hours=ttl_hours)

        # Eligible queryset. We use exclude on PAID just in case a callback
        # raced with us and flipped payment_status without flipping status
        # (shouldn't happen — the handlers do both atomically — but
        # defensive query.)
        qs = (
            Order.objects
            .filter(
                status=Order.STATUS_AWAITING_PAYMENT,
                auto_expired=False,
                created_at__lt=cutoff,
            )
            .exclude(payment_status=Order.PAYMENT_STATUS_PAID)
            .order_by("created_at")
        )

        total = qs.count()
        if total == 0:
            self.stdout.write(
                f"No stale awaiting_payment orders (TTL = {ttl_hours}h). "
                f"Cutoff = {cutoff.isoformat()}"
            )
            return

        dry = options.get("dry_run", False)
        self.stdout.write(
            f"{'[DRY RUN] ' if dry else ''}"
            f"Found {total} stale awaiting_payment orders "
            f"(TTL = {ttl_hours}h, cutoff = {cutoff.isoformat()})."
        )

        expired_count = 0
        for order_pk in list(qs.values_list("pk", flat=True)):
            if dry:
                expired_count += 1
                continue
            # Per-row atomic block so a hang on one row doesn't stall the
            # rest. select_for_update + re-check guards against concurrent
            # callback promoting the order to paid while we were waiting.
            with transaction.atomic():
                try:
                    order = Order.objects.select_for_update().get(pk=order_pk)
                except Order.DoesNotExist:
                    continue
                # Re-check under the lock.
                if order.auto_expired:
                    continue
                if order.payment_status == Order.PAYMENT_STATUS_PAID:
                    continue
                if order.status != Order.STATUS_AWAITING_PAYMENT:
                    continue
                if order.created_at is None or order.created_at >= cutoff:
                    continue

                order.auto_expired = True
                order.payment_status = Order.PAYMENT_STATUS_EXPIRED
                order.status = Order.STATUS_PAYMENT_FAILED
                order.save(update_fields=[
                    "auto_expired", "payment_status", "status",
                ])
                expired_count += 1

        verb = "would expire" if dry else "expired"
        self.stdout.write(self.style.SUCCESS(
            f"{verb.capitalize()} {expired_count} of {total} stale orders."
        ))
