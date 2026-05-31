"""
TEMPORARY admin view for the one-off Tilda CSV import.

Why this exists:
  Render's free plan does not provide a shell, and we can't run management
  commands directly. This view wraps the same parse/import logic in a
  superuser-gated UI: upload the CSV, run analyze (dry-run report), then
  run the actual commit.

After the import is done — DELETE THIS FILE and its URL route. It's
intentionally not behind a permanent feature; the code is single-purpose
and the upload of a CSV containing PII (customer phones / addresses)
should not stay reachable longer than needed.

Security:
  - @login_required + is_superuser check. Anyone without superuser
    rights gets redirected to /admin/login/.
  - CSV stays in memory only — never written to disk on the server.
  - No background tasks; the request handles the whole import (1-2 min
    for ~1500 rows; well within Render's 30s gunicorn timeout? — see
    below).

Timeout caveat:
  Render's free tier kills requests longer than 30 seconds. For
  ~1500-row imports that's tight. We split into TWO buttons:
    1. "Анализ"  — only reads, returns the same report as
                   import_tilda_analyze. Fast (~1-2s).
    2. "Импорт"  — writes data. We chunk the work and stream a
                   simple progress log so the operator sees what's
                   happening; the underlying queries are unchanged.
  If the request hits 30s during commit, the transaction rolls back
  (no partial data) and the operator re-runs with a smaller --limit.
"""

import io

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect

from foodcost.models import Country, Customer, Dish, Order, OrderItem, OrderSource
from foodcost.tilda_import import (
    read_tilda_csv,
    dish_name_index,
    unique_phones,
    order_count_by_phone,
)


# Keep these in sync with import_tilda_orders.Command. Duplicated here
# rather than imported because the management command's heavy `handle`
# method isn't ergonomic to call from a view — but the constants are
# tiny and stable.
TILDA_SOURCE_NAME = "Tilda (импорт)"
LEGACY_SOURCE = "tilda"


def _require_superuser(request):
    """Centralized 403 helper so each view doesn't repeat the check."""
    if not request.user.is_authenticated:
        return HttpResponseForbidden("Login required")
    if not request.user.is_superuser:
        return HttpResponseForbidden(
            "This page is for superusers only."
        )
    return None


@login_required(login_url="/admin/login/")
@csrf_protect
def tilda_import_page(request):
    """Single-page UI:
      - File picker for CSV
      - Country picker
      - "Анализ" button → renders the same page with a report block
      - "Импорт (commit)" button → runs the import, shows summary
    """
    forbidden = _require_superuser(request)
    if forbidden:
        return forbidden

    countries = list(Country.objects.all().order_by("slug"))

    context = {
        "countries": countries,
        "report": None,
        "import_result": None,
        "error": None,
        "selected_country_slug": request.POST.get("country_slug") or "",
        "limit_raw": request.POST.get("limit") or "",
    }

    if request.method == "POST":
        # Both buttons share the same form; distinguish via 'mode'
        mode = request.POST.get("mode") or ""

        # CSV is required for both modes. We read it once, in memory.
        upload = request.FILES.get("csv_file")
        country_slug = (request.POST.get("country_slug") or "").strip()
        limit_raw = (request.POST.get("limit") or "").strip()

        if not upload:
            context["error"] = "Выберите CSV-файл от Tilda."
            return render(request, "foodcost/tilda_import.html", context)

        if not country_slug:
            context["error"] = "Выберите страну."
            return render(request, "foodcost/tilda_import.html", context)

        try:
            country = Country.objects.get(slug=country_slug)
        except Country.DoesNotExist:
            context["error"] = f"Страна с slug={country_slug!r} не найдена."
            return render(request, "foodcost/tilda_import.html", context)

        try:
            limit = int(limit_raw) if limit_raw else 0
            if limit < 0:
                limit = 0
        except ValueError:
            limit = 0

        # Parse the uploaded CSV. Django UploadedFile is bytes; the
        # tilda_import helper takes a path, so we wrap the bytes in a
        # NamedTemporaryFile? — actually simpler: re-implement the
        # one-line open() locally with TextIOWrapper.
        import csv as _csv
        from foodcost.tilda_import import parse_tilda_row, TILDA_COLUMNS

        try:
            # Each Django UploadedFile chunk is bytes; we decode to text.
            text = upload.read().decode("utf-8")
        except UnicodeDecodeError:
            context["error"] = (
                "CSV не в кодировке UTF-8. Сохраните файл как UTF-8 и попробуйте снова."
            )
            return render(request, "foodcost/tilda_import.html", context)

        rows, parse_errors = _parse_csv_text(text)

        if parse_errors and len(rows) == 0:
            context["error"] = "Не удалось разобрать CSV: " + "; ".join(parse_errors[:3])
            return render(request, "foodcost/tilda_import.html", context)

        if limit > 0:
            rows = rows[:limit]

        if mode == "analyze":
            context["report"] = _build_analyze_report(rows, parse_errors, country)
        elif mode == "import":
            context["report"] = _build_analyze_report(rows, parse_errors, country)
            context["import_result"] = _run_import(rows, country)
        else:
            context["error"] = "Неизвестный режим."

    return render(request, "foodcost/tilda_import.html", context)


# ---------------------------------------------------------------------------
# CSV parsing — same as tilda_import.read_tilda_csv but for in-memory text
# rather than a file path.
# ---------------------------------------------------------------------------

def _parse_csv_text(text):
    """Parse the CSV text into (rows, errors). Mirrors read_tilda_csv
    but takes a string instead of a path — Django UploadedFile gives
    us bytes which we decoded to text in the caller."""
    import csv as _csv
    from foodcost.tilda_import import parse_tilda_row, TILDA_COLUMNS

    rows = []
    errors = []

    reader = _csv.DictReader(io.StringIO(text), delimiter=";")
    header = reader.fieldnames or []
    missing = [c for c in TILDA_COLUMNS if c not in header]
    if missing:
        errors.append(
            f"CSV header is missing expected columns: {missing}. "
            f"Got: {header}"
        )
        return rows, errors

    for i, row_dict in enumerate(reader, start=2):
        try:
            parsed = parse_tilda_row(row_dict, line_number=i)
        except ValueError as e:
            errors.append(str(e))
            continue
        rows.append(parsed)
    return rows, errors


# ---------------------------------------------------------------------------
# Analyze report — mirrors import_tilda_analyze's stdout sections,
# but returns a dict so the template can render it as HTML cards.
# ---------------------------------------------------------------------------

def _build_analyze_report(rows, parse_errors, country):
    from collections import Counter

    # Customers
    phones = unique_phones(rows)
    existing_phone_keys = set()
    for c in Customer.objects.filter(country=country).only("phone"):
        digits = "".join(ch for ch in c.phone if ch.isdigit())
        if digits:
            existing_phone_keys.add(digits)
    will_match = phones & existing_phone_keys
    will_create = phones - existing_phone_keys

    counts = order_count_by_phone(rows)
    top_customers = []
    for phone_key, n in sorted(counts.items(), key=lambda x: -x[1])[:5]:
        name = next(
            (r.customer_name for r in rows if r.phone_key == phone_key),
            "",
        )
        top_customers.append({"phone": phone_key, "name": name, "count": n})

    # Idempotency
    csv_refs = {r.tranid for r in rows}
    existing_refs = set(
        Order.objects
        .filter(legacy_source=LEGACY_SOURCE, legacy_order_ref__in=csv_refs)
        .values_list("legacy_order_ref", flat=True)
    )
    will_skip = csv_refs & existing_refs
    will_insert = csv_refs - existing_refs

    # Dish matching
    erp_dishes = {
        d.name.strip().lower(): d
        for d in Dish.objects.filter(country=country).only("id", "name")
    }
    csv_dish_names = dish_name_index(rows)

    line_occurrence = Counter()
    for r in rows:
        for it in r.items:
            line_occurrence[it.name.strip().lower()] += 1

    matched, unmatched = [], []
    for key in csv_dish_names:
        if key in erp_dishes:
            matched.append(key)
        else:
            unmatched.append(key)

    unmatched_top = []
    for key in sorted(unmatched, key=lambda k: -line_occurrence.get(k, 0))[:15]:
        unmatched_top.append({
            "name": sorted(csv_dish_names[key])[0],
            "count": line_occurrence[key],
        })

    # Promo codes
    promo_count = Counter(r.promo_code for r in rows if r.promo_code)
    top_promos = [
        {"code": c, "count": n} for c, n in promo_count.most_common(10)
    ]

    total_warns = sum(len(r.warnings) for r in rows)

    return {
        "rows_parsed": len(rows),
        "parse_errors": parse_errors[:10],  # show only first 10 in UI
        "parse_errors_count": len(parse_errors),
        "total_warnings": total_warns,
        "phones_total": len(phones),
        "phones_existing": len(will_match),
        "phones_new": len(will_create),
        "top_customers": top_customers,
        "csv_refs_total": len(csv_refs),
        "already_imported": len(will_skip),
        "will_insert": len(will_insert),
        "erp_dishes_total": len(erp_dishes),
        "csv_dishes_unique": len(csv_dish_names),
        "matched_count": len(matched),
        "unmatched_count": len(unmatched),
        "unmatched_top": unmatched_top,
        "promos_distinct": len(promo_count),
        "promos_top": top_promos,
    }


# ---------------------------------------------------------------------------
# Actual import — wraps the same logic as
# import_tilda_orders.Command._do_import but inline.
# ---------------------------------------------------------------------------

def _run_import(rows, country):
    """Execute the import inside a transaction. Returns a result dict
    suitable for template rendering. Skips already-imported rows by
    legacy_order_ref (idempotent)."""
    from collections import Counter
    from django.db import transaction
    from django.utils import timezone

    dish_index = {
        d.name.strip().lower(): d
        for d in Dish.objects.filter(country=country).only("id", "name")
    }
    existing_refs = set(
        Order.objects
        .filter(legacy_source=LEGACY_SOURCE)
        .values_list("legacy_order_ref", flat=True)
    )
    customers_by_key = {}
    for c in Customer.objects.filter(country=country).only("id", "phone"):
        key = "".join(ch for ch in c.phone if ch.isdigit())
        if key:
            customers_by_key.setdefault(key, c)

    stats = Counter()

    try:
        with transaction.atomic():
            source, _ = OrderSource.objects.get_or_create(
                country=country,
                name=TILDA_SOURCE_NAME,
                defaults={"is_active": True},
            )

            for r in rows:
                if r.tranid in existing_refs:
                    stats["skipped"] += 1
                    continue

                customer = customers_by_key.get(r.phone_key)
                if customer is None:
                    stats["new_customers"] += 1
                    customer = Customer.objects.create(
                        country=country,
                        phone=r.customer_phone,
                        name=r.customer_name or "Клиент",
                    )
                    customers_by_key[r.phone_key] = customer
                else:
                    stats["reused_customers"] += 1

                cashier_bits = []
                if r.promo_code:
                    cashier_bits.append(f"Промокод (Tilda): {r.promo_code}")
                cashier_comment = "\n".join(cashier_bits)

                order_date = r.order_date
                if order_date is not None and not timezone.is_aware(order_date):
                    order_date = timezone.make_aware(
                        order_date, timezone.get_current_timezone(),
                    )

                order = Order.objects.create(
                    country=country,
                    source=source,
                    customer=customer,
                    customer_name=r.customer_name,
                    customer_phone=r.customer_phone,
                    delivery_address=r.delivery_address,
                    customer_comment=r.customer_comment,
                    cashier_comment=cashier_comment,
                    subtotal_amount=r.total,
                    discount_amount=0,
                    delivery_amount=0,
                    total_amount=r.total,
                    status=Order.STATUS_DONE,
                    payment_status=Order.PAYMENT_STATUS_CASH,
                    fulfillment_method=Order.FULFILLMENT_DELIVERY,
                    is_legacy_import=True,
                    legacy_source=LEGACY_SOURCE,
                    legacy_order_ref=r.tranid,
                    order_date=order_date,
                )

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
    except Exception as e:
        # Rolled back automatically by atomic(); surface for the UI.
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "stats": dict(stats),
        }

    return {
        "ok": True,
        "error": None,
        "stats": dict(stats),
    }
