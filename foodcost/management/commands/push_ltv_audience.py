"""
Push the Value-Based LTV audience to Meta. Run on a cron (e.g. daily).

    python manage.py push_ltv_audience
    python manage.py push_ltv_audience --country uzbekistan

Exit codes:
    0 — pushed (or nothing to push)
    1 — not configured (missing env vars) — cron should not retry-spam
    2 — transient error talking to Meta — safe to retry next tick
"""

from django.core.management.base import BaseCommand

from foodcost.meta_audience import (
    push_ltv_audience,
    MetaAudienceNotConfigured,
    MetaAudienceError,
)


class Command(BaseCommand):
    help = "Push the value-based LTV custom audience to Meta Marketing API."

    def add_arguments(self, parser):
        parser.add_argument(
            "--country",
            dest="country_slug",
            default=None,
            help="Limit to a single country by slug (e.g. uzbekistan).",
        )

    def handle(self, *args, **options):
        country = None
        country_slug = options.get("country_slug")
        if country_slug:
            # Lazy import to keep command import cheap.
            from foodcost.models import Country
            country = Country.objects.filter(slug=country_slug).first()
            if country is None:
                self.stderr.write(f"Country '{country_slug}' not found")
                return

        try:
            result = push_ltv_audience(country=country)
        except MetaAudienceNotConfigured as e:
            self.stderr.write(f"Not configured: {e}")
            raise SystemExit(1)
        except MetaAudienceError as e:
            self.stderr.write(f"Transient error: {e}")
            raise SystemExit(2)

        self.stdout.write(
            self.style.SUCCESS(
                f"Pushed {result['sent']} users in {result['batches']} batch(es)."
            )
        )
