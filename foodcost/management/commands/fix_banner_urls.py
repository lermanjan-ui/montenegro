"""
Repair banner action_value fields that contain absolute URLs.

Run once before deploying the action_value validation (which will then
reject any new bad data at form-submit time):

    # Preview first — shows what would change, doesn't touch the DB
    python manage.py fix_banner_urls --dry-run

    # Apply
    python manage.py fix_banner_urls

What it fixes (per row):
  - HomepageBanner.action_value
  - HomeComboBanner.cta_action_value

What it does:
  - For each row with action_type in {category, product}, if the value
    is an absolute URL ("http://localhost:3000/category/pasta",
    "https://raccoon.uz/product/x"), strips host+scheme and keeps only
    the path ("/category/pasta", "/product/x").
  - For promo_code fields that got a URL by mistake, takes the last
    path segment as the code (best-effort; manager should verify).
  - For external_url fields pointing at localhost — does NOT auto-fix
    (we don't know the real production URL). Lists those at the end
    so a manager can edit them by hand.
  - Anything already correct is left alone.

Why a one-off command and not a data migration:
  - Data migrations run automatically on every deploy. We want a manager
    to see the report before flipping the switch — auto-fix is best-effort
    and edge cases need eyeballs.
  - --dry-run support gives a clear safety net.
"""

from django.core.management.base import BaseCommand
from django.db import transaction

from foodcost.models import HomepageBanner, HomeComboBanner
from foodcost.banner_validation import (
    autofix_action_value,
    validate_action_value,
)


class Command(BaseCommand):
    help = (
        "Repair banner action_value / cta_action_value fields that "
        "contain absolute URLs (e.g. http://localhost:3000/...). "
        "Use --dry-run first to preview."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show planned changes without touching the database.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        if dry_run:
            self.stdout.write(self.style.WARNING("[fix-banner-urls] DRY RUN — nothing will be saved"))
        else:
            self.stdout.write("[fix-banner-urls] applying fixes…")

        # ---- HomepageBanner ----
        homepage_fixed = []
        homepage_manual = []
        for banner in HomepageBanner.objects.all().order_by("id"):
            new_value, changed = autofix_action_value(
                banner.action_type, banner.action_value,
            )
            if changed:
                homepage_fixed.append((banner, banner.action_value, new_value))
                if not dry_run:
                    banner.action_value = new_value
                    banner.save(update_fields=["action_value"])
                continue
            # Even after autofix, the value might still fail validation —
            # that means we couldn't repair it programmatically and a
            # manager has to step in. Collect for the report.
            ok, err = validate_action_value(banner.action_type, banner.action_value)
            if not ok:
                homepage_manual.append((banner, banner.action_value, err))

        # ---- HomeComboBanner ----
        combo_fixed = []
        combo_manual = []
        for banner in HomeComboBanner.objects.all().order_by("id"):
            new_value, changed = autofix_action_value(
                banner.cta_action_type, banner.cta_action_value,
            )
            if changed:
                combo_fixed.append((banner, banner.cta_action_value, new_value))
                if not dry_run:
                    banner.cta_action_value = new_value
                    banner.save(update_fields=["cta_action_value"])
                continue
            ok, err = validate_action_value(
                banner.cta_action_type, banner.cta_action_value,
            )
            if not ok:
                combo_manual.append((banner, banner.cta_action_value, err))

        # ---- Report ----
        self.stdout.write("")
        self._report_section(
            "HomepageBanner.action_value", homepage_fixed, homepage_manual,
        )
        self._report_section(
            "HomeComboBanner.cta_action_value", combo_fixed, combo_manual,
        )

        # ---- Summary ----
        total_fixed = len(homepage_fixed) + len(combo_fixed)
        total_manual = len(homepage_manual) + len(combo_manual)
        self.stdout.write("")
        if dry_run:
            self.stdout.write(self.style.WARNING(
                f"[fix-banner-urls] DRY RUN — would auto-fix {total_fixed}, "
                f"need manual review: {total_manual}"
            ))
            self.stdout.write("Re-run without --dry-run to apply.")
        else:
            self.stdout.write(self.style.SUCCESS(
                f"[fix-banner-urls] done — auto-fixed {total_fixed}, "
                f"need manual review: {total_manual}"
            ))

    # -- helpers --

    def _report_section(self, title, fixed, manual):
        self.stdout.write(self.style.NOTICE(f"=== {title} ==="))
        if not fixed and not manual:
            self.stdout.write("  Nothing to do.")
            return

        if fixed:
            self.stdout.write(self.style.SUCCESS(
                f"  Auto-fixed ({len(fixed)}):"
            ))
            for banner, old, new in fixed:
                # Show banner identity in a readable way — title is the
                # field operators recognize fastest.
                ident = getattr(banner, "title", None) or f"id={banner.id}"
                self.stdout.write(
                    f"    #{banner.id} «{ident}»\n"
                    f"      before: {old!r}\n"
                    f"      after:  {new!r}"
                )

        if manual:
            self.stdout.write(self.style.WARNING(
                f"  Needs manual fix ({len(manual)}):"
            ))
            for banner, value, err in manual:
                ident = getattr(banner, "title", None) or f"id={banner.id}"
                self.stdout.write(
                    f"    #{banner.id} «{ident}»\n"
                    f"      value:  {value!r}\n"
                    f"      reason: {err}"
                )
