from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from datetime import datetime
from decimal import Decimal, InvalidOperation

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

    # ---------- GET: фильтры ----------
    f_date_from = request.GET.get("date_from", "")
    f_date_to = request.GET.get("date_to", "")
    f_type = request.GET.get("expense_type", "")
    f_location = request.GET.get("location_id", "")
    f_source = request.GET.get("source", "")

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

    qs = qs.select_related("location", "debtor", "purchase_receipt").order_by(
        "-expense_date", "-id"
    )

    type_labels = dict(FinancialExpense.EXPENSE_TYPES)
    source_labels = dict(FinancialExpense.SOURCE_CHOICES)

    expenses = list(qs)
    total_sum = Decimal(0)
    by_type_map = {}
    for e in expenses:
        amt = e.amount or Decimal(0)
        total_sum += amt
        by_type_map[e.expense_type] = by_type_map.get(
            e.expense_type, Decimal(0)
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

    context = {
        "country": country,
        "expenses": expenses,
        "expense_types": FinancialExpense.EXPENSE_TYPES,
        "source_choices": FinancialExpense.SOURCE_CHOICES,
        "locations": locations,
        "debtors": debtors,
        "total_sum": total_sum,
        "by_type": by_type,
        "expenses_count": len(expenses),
        "f_date_from": f_date_from,
        "f_date_to": f_date_to,
        "f_type": f_type,
        "f_location": f_location,
        "f_source": f_source,
        "SOURCE_DEBT": FinancialExpense.SOURCE_DEBT,
        "today": timezone.localdate().strftime("%Y-%m-%d"),
    }
    return render(request, "foodcost/finance_expenses.html", context)
