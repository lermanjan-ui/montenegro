from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from datetime import datetime
from decimal import Decimal, InvalidOperation
import csv
import io

from .models import UserProfile, Location, FinancialExpense, ExpenseDebtor
from .views import get_country, require_section_access, clean_decimal


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_date(value):
    """'YYYY-MM-DD' -> date | None."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _amount(value):
    try:
        return Decimal(clean_decimal(value, "0"))
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _loc(value, country):
    lid = _int(value)
    if not lid:
        return None
    return Location.objects.filter(id=lid, country=country).first()


def _debtor(value, country):
    did = _int(value)
    if not did:
        return None
    return ExpenseDebtor.objects.filter(id=did, country=country).first()


def _redirect_url(request, country):
    return request.POST.get("next") or f"/c/{country.slug}/finance/expenses/"


@login_required
def finance_expenses(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = require_section_access(request.user, UserProfile.SECTION_FINANCE)
    if access_error:
        return access_error

    locations = Location.objects.filter(country=country).order_by(
        "site_sort_order", "name"
    )
    debtors = ExpenseDebtor.objects.filter(
        country=country, is_active=True
    ).order_by("name")

    # ---------- POST: создание / редактирование / удаление ----------
    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "create_expense":
            FinancialExpense.objects.create(
                country=country,
                location=_loc(request.POST.get("location_id"), country),
                expense_type=(
                    request.POST.get("expense_type")
                    or FinancialExpense.EXPENSE_OTHER
                ),
                name=(request.POST.get("name") or "").strip(),
                amount=_amount(request.POST.get("amount")),
                expense_date=(
                    _parse_date(request.POST.get("expense_date"))
                    or timezone.localdate()
                ),
                comment=(request.POST.get("comment") or "").strip(),
                source=request.POST.get("source") or "",
                legal_entity=(request.POST.get("legal_entity") or "").strip(),
                debtor=_debtor(request.POST.get("debtor_id"), country),
                created_by=request.user,
            )
            return redirect(_redirect_url(request, country))

        if action == "update_expense":
            exp = get_object_or_404(
                FinancialExpense,
                id=_int(request.POST.get("expense_id")),
                country=country,
            )
            exp.location = _loc(request.POST.get("location_id"), country)
            exp.expense_type = (
                request.POST.get("expense_type") or exp.expense_type
            )
            exp.name = (request.POST.get("name") or "").strip()
            exp.amount = _amount(request.POST.get("amount"))
            exp.expense_date = (
                _parse_date(request.POST.get("expense_date")) or exp.expense_date
            )
            exp.comment = (request.POST.get("comment") or "").strip()
            exp.source = request.POST.get("source") or ""
            exp.legal_entity = (request.POST.get("legal_entity") or "").strip()
            exp.debtor = _debtor(request.POST.get("debtor_id"), country)
            exp.save()
            return redirect(_redirect_url(request, country))

        if action == "delete_expense":
            FinancialExpense.objects.filter(
                id=_int(request.POST.get("expense_id")), country=country
            ).delete()
            return redirect(_redirect_url(request, country))

        if action == "import_csv":
            f = request.FILES.get("csv_file")
            imported = 0
            if f is not None:
                valid_types = {c for c, _ in FinancialExpense.EXPENSE_TYPES}
                valid_sources = {c for c, _ in FinancialExpense.SOURCE_CHOICES}
                text = f.read().decode("utf-8-sig", errors="replace")
                reader = csv.DictReader(io.StringIO(text))
                debtor_cache = {}

                def _get_debtor(nm):
                    nm = (nm or "").strip()
                    if not nm:
                        return None
                    if nm not in debtor_cache:
                        debtor_cache[nm], _ = ExpenseDebtor.objects.get_or_create(
                            country=country, name=nm
                        )
                    return debtor_cache[nm]

                bulk = []
                for row in reader:
                    d = _parse_date(row.get("expense_date"))
                    if not d:
                        continue
                    et = (row.get("expense_type") or "").strip()
                    if et not in valid_types:
                        et = FinancialExpense.EXPENSE_OTHER
                    src = (row.get("source") or "").strip()
                    if src not in valid_sources:
                        src = ""
                    debtor_obj = _get_debtor(row.get("debtor"))
                    if debtor_obj is not None and not src:
                        src = FinancialExpense.SOURCE_DEBT
                    bulk.append(FinancialExpense(
                        country=country,
                        expense_type=et,
                        name=(row.get("name") or "").strip(),
                        amount=_amount(row.get("amount")),
                        expense_date=d,
                        source=src,
                        legal_entity=(row.get("legal_entity") or "").strip(),
                        debtor=debtor_obj,
                        comment=(row.get("comment") or "").strip(),
                        created_by=request.user,
                    ))
                if bulk:
                    FinancialExpense.objects.bulk_create(bulk)
                    imported = len(bulk)
            return redirect(
                f"/c/{country.slug}/finance/expenses/?imported={imported}"
            )

    # ---------- GET: фильтры ----------
    f_date_from = request.GET.get("date_from", "")
    f_date_to = request.GET.get("date_to", "")
    f_type = request.GET.get("expense_type", "")
    f_location = request.GET.get("location_id", "")
    f_source = request.GET.get("source", "")
    f_debtor = request.GET.get("debtor_id", "")

    qs = FinancialExpense.objects.filter(country=country)
    d_from = _parse_date(f_date_from)
    d_to = _parse_date(f_date_to)
    if d_from:
        qs = qs.filter(expense_date__gte=d_from)
    if d_to:
        qs = qs.filter(expense_date__lte=d_to)
    if f_type:
        qs = qs.filter(expense_type=f_type)
    loc_id = _int(f_location)
    if loc_id:
        qs = qs.filter(location_id=loc_id)
    if f_source:
        qs = qs.filter(source=f_source)
    debtor_id = _int(f_debtor)
    if debtor_id:
        qs = qs.filter(debtor_id=debtor_id)

    qs = qs.select_related("location", "debtor", "purchase_receipt").order_by(
        "-expense_date", "-id"
    )

    type_labels = dict(FinancialExpense.EXPENSE_TYPES)
    source_labels = dict(FinancialExpense.SOURCE_CHOICES)

    expenses = list(qs)
    total_sum = Decimal(0)
    by_type_map = {}
    by_debtor_map = {}
    for e in expenses:
        amt = e.amount or Decimal(0)
        total_sum += amt
        by_type_map[e.expense_type] = by_type_map.get(
            e.expense_type, Decimal(0)
        ) + amt
        if e.debtor_id and e.debtor:
            by_debtor_map[e.debtor.name] = by_debtor_map.get(
                e.debtor.name, Decimal(0)
            ) + amt
        # ярлыки и значения для форм редактирования
        e.type_label = type_labels.get(e.expense_type, e.expense_type)
        e.source_label = source_labels.get(e.source, "") if e.source else ""
        e.date_value = e.expense_date.strftime("%Y-%m-%d") if e.expense_date else ""

    by_type = [
        {"type": t, "label": type_labels.get(t, t), "sum": s}
        for t, s in sorted(
            by_type_map.items(), key=lambda kv: kv[1], reverse=True
        )
    ]
    by_debtor = [
        {"name": n, "sum": s}
        for n, s in sorted(
            by_debtor_map.items(), key=lambda kv: kv[1], reverse=True
        )
    ]

    context = {
        "country": country,
        "expenses": expenses,
        "expense_types": FinancialExpense.EXPENSE_TYPES,
        "source_choices": FinancialExpense.SOURCE_CHOICES,
        "locations": locations,
        "debtors": debtors,
        "total_sum": total_sum,
        "by_type": by_type,
        "by_debtor": by_debtor,
        "expenses_count": len(expenses),
        "f_date_from": f_date_from,
        "f_date_to": f_date_to,
        "f_type": f_type,
        "f_location": f_location,
        "f_source": f_source,
        "f_debtor": f_debtor,
        "SOURCE_DEBT": FinancialExpense.SOURCE_DEBT,
        "today": timezone.localdate().strftime("%Y-%m-%d"),
        "imported": request.GET.get("imported", ""),
    }
    return render(request, "foodcost/finance_expenses.html", context)
