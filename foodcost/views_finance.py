from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.db.models import Sum
from datetime import datetime
from decimal import Decimal, InvalidOperation
import csv
import io

from .models import (
    UserProfile, Location, FinancialExpense, ExpenseDebtor, FinancialIncome,
)
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
        raw = clean_decimal(value, "0").replace(" ", "").replace("\u00a0", "")
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return Decimal(0)


def _money(value):
    """Деньги целыми, с пробелом-разделителем тысяч: 100 000 000."""
    try:
        n = int(round(float(value or 0)))
    except (TypeError, ValueError):
        return "0"
    s = f"{abs(n):,}".replace(",", " ")
    return f"-{s}" if n < 0 else s


# Отображаемые варианты источника для РАСХОДОВ:
# «Расчётный счёт» и «Счёт компании» объединены в один «Расчётный счёт»,
# «Долг» переименован в «Заём». Значения в БД не меняем (миграция не нужна).
EXPENSE_SOURCE_CHOICES = [
    (FinancialExpense.SOURCE_CASH, "Касса точки"),
    (FinancialExpense.SOURCE_SETTLEMENT, "Расчётный счёт"),
    (FinancialExpense.SOURCE_DEBT, "Заём"),
]
# Метки для показа (включая старое значение company_account -> Расчётный счёт).
EXPENSE_SOURCE_LABELS = {
    FinancialExpense.SOURCE_CASH: "Касса точки",
    FinancialExpense.SOURCE_SETTLEMENT: "Расчётный счёт",
    FinancialExpense.SOURCE_COMPANY: "Расчётный счёт",
    FinancialExpense.SOURCE_DEBT: "Заём",
}


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
    f_debtor_ids = request.GET.getlist("debtor_id")

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
        if f_source == FinancialExpense.SOURCE_SETTLEMENT:
            # «Расчётный счёт» теперь включает и старое «Счёт компании».
            qs = qs.filter(source__in=[
                FinancialExpense.SOURCE_SETTLEMENT,
                FinancialExpense.SOURCE_COMPANY,
            ])
        else:
            qs = qs.filter(source=f_source)
    debtor_ids = [int(x) for x in f_debtor_ids if str(x).isdigit()]
    if debtor_ids:
        qs = qs.filter(debtor_id__in=debtor_ids)

    qs = qs.select_related("location", "debtor", "purchase_receipt").order_by(
        "-expense_date", "-id"
    )

    type_labels = dict(FinancialExpense.EXPENSE_TYPES)
    source_labels = EXPENSE_SOURCE_LABELS

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
        e.amount_fmt = _money(amt)

    by_type = [
        {"type": t, "label": type_labels.get(t, t), "sum": s, "sum_fmt": _money(s)}
        for t, s in sorted(
            by_type_map.items(), key=lambda kv: kv[1], reverse=True
        )
    ]

    context = {
        "country": country,
        "expenses": expenses,
        "expense_types": FinancialExpense.EXPENSE_TYPES,
        "source_choices": EXPENSE_SOURCE_CHOICES,
        "locations": locations,
        "debtors": debtors,
        "total_sum": total_sum,
        "total_sum_fmt": _money(total_sum),
        "by_type": by_type,
        "expenses_count": len(expenses),
        "f_date_from": f_date_from,
        "f_date_to": f_date_to,
        "f_type": f_type,
        "f_location": f_location,
        "f_source": f_source,
        "f_debtor_ids": f_debtor_ids,
        "SOURCE_DEBT": FinancialExpense.SOURCE_DEBT,
        "today": timezone.localdate().strftime("%Y-%m-%d"),
        "imported": request.GET.get("imported", ""),
        "active": "expenses",
    }
    return render(request, "foodcost/finance_expenses.html", context)


# ============================================================================
#  ПОСТУПЛЕНИЕ ДЕНЕГ (приход)
# ============================================================================

def _income_url(request, country):
    return request.POST.get("next") or f"/c/{country.slug}/finance/income/"


@login_required
def finance_income(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = require_section_access(
        request.user, UserProfile.SECTION_FINANCE
    )
    if access_error:
        return access_error

    locations = Location.objects.filter(country=country).order_by(
        "site_sort_order", "name"
    )

    if request.method == "POST":
        action = request.POST.get("action", "")

        if action == "create_income":
            FinancialIncome.objects.create(
                country=country,
                location=_loc(request.POST.get("location_id"), country),
                source=request.POST.get("source") or "",
                name=(request.POST.get("name") or "").strip(),
                amount=_amount(request.POST.get("amount")),
                income_date=(
                    _parse_date(request.POST.get("income_date"))
                    or timezone.localdate()
                ),
                comment=(request.POST.get("comment") or "").strip(),
                created_by=request.user,
            )
            return redirect(_income_url(request, country))

        if action == "update_income":
            inc = get_object_or_404(
                FinancialIncome,
                id=_int(request.POST.get("income_id")),
                country=country,
            )
            inc.location = _loc(request.POST.get("location_id"), country)
            inc.source = request.POST.get("source") or ""
            inc.name = (request.POST.get("name") or "").strip()
            inc.amount = _amount(request.POST.get("amount"))
            inc.income_date = (
                _parse_date(request.POST.get("income_date")) or inc.income_date
            )
            inc.comment = (request.POST.get("comment") or "").strip()
            inc.save()
            return redirect(_income_url(request, country))

        if action == "delete_income":
            FinancialIncome.objects.filter(
                id=_int(request.POST.get("income_id")), country=country
            ).delete()
            return redirect(_income_url(request, country))

    f_date_from = request.GET.get("date_from", "")
    f_date_to = request.GET.get("date_to", "")
    f_source = request.GET.get("source", "")

    qs = FinancialIncome.objects.filter(country=country)
    d_from = _parse_date(f_date_from)
    d_to = _parse_date(f_date_to)
    if d_from:
        qs = qs.filter(income_date__gte=d_from)
    if d_to:
        qs = qs.filter(income_date__lte=d_to)
    if f_source:
        qs = qs.filter(source=f_source)
    qs = qs.select_related("location").order_by("-income_date", "-id")

    source_labels = dict(FinancialIncome.SOURCE_CHOICES)
    incomes = list(qs)
    total_sum = Decimal(0)
    by_source_map = {}
    for e in incomes:
        amt = e.amount or Decimal(0)
        total_sum += amt
        e.source_label = source_labels.get(e.source, "") if e.source else ""
        e.date_value = e.income_date.strftime("%Y-%m-%d") if e.income_date else ""
        e.amount_fmt = _money(amt)
        if e.source:
            by_source_map[e.source_label] = by_source_map.get(
                e.source_label, Decimal(0)
            ) + amt

    by_source = [
        {"label": k, "sum": v, "sum_fmt": _money(v)}
        for k, v in sorted(
            by_source_map.items(), key=lambda kv: kv[1], reverse=True
        )
    ]

    context = {
        "country": country,
        "incomes": incomes,
        "source_choices": FinancialIncome.SOURCE_CHOICES,
        "locations": locations,
        "total_sum": total_sum,
        "total_sum_fmt": _money(total_sum),
        "by_source": by_source,
        "incomes_count": len(incomes),
        "f_date_from": f_date_from,
        "f_date_to": f_date_to,
        "f_source": f_source,
        "today": timezone.localdate().strftime("%Y-%m-%d"),
        "active": "income",
    }
    return render(request, "foodcost/finance_income.html", context)


# ============================================================================
#  ПРИХОД / РАСХОД ПО МЕСЯЦАМ (график)
# ============================================================================

_MONTHS_RU = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн",
              "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]


@login_required
def finance_chart(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = require_section_access(
        request.user, UserProfile.SECTION_FINANCE
    )
    if access_error:
        return access_error

    # Список годов, где есть данные (приход или расход).
    yrs = set()
    for d in FinancialExpense.objects.filter(country=country).dates(
        "expense_date", "year"
    ):
        yrs.add(d.year)
    for d in FinancialIncome.objects.filter(country=country).dates(
        "income_date", "year"
    ):
        yrs.add(d.year)

    requested = _int(request.GET.get("year"))
    if requested:
        year = requested
    elif yrs:
        year = max(yrs)          # по умолчанию — последний год с данными
    else:
        year = timezone.localdate().year
    yrs.add(year)
    years = sorted(yrs, reverse=True)

    exp_rows = (
        FinancialExpense.objects.filter(
            country=country, expense_date__year=year
        )
        .values("expense_date__month")
        .annotate(s=Sum("amount"))
    )
    inc_rows = (
        FinancialIncome.objects.filter(
            country=country, income_date__year=year
        )
        .values("income_date__month")
        .annotate(s=Sum("amount"))
    )
    exp_by = {r["expense_date__month"]: (r["s"] or Decimal(0)) for r in exp_rows}
    inc_by = {r["income_date__month"]: (r["s"] or Decimal(0)) for r in inc_rows}

    max_val = Decimal(0)
    for m in range(1, 13):
        max_val = max(
            max_val, inc_by.get(m, Decimal(0)), exp_by.get(m, Decimal(0))
        )

    # Геометрия SVG (viewBox 0 0 760 260): базовая линия y=210, высота столбца до 170.
    base_y = 210
    plot_h = 170
    col_w = 59
    months = []
    total_income = Decimal(0)
    total_expense = Decimal(0)
    for i in range(12):
        m = i + 1
        inc = inc_by.get(m, Decimal(0))
        exp = exp_by.get(m, Decimal(0))
        total_income += inc
        total_expense += exp
        in_h = int(float(inc) / float(max_val) * plot_h) if max_val else 0
        ex_h = int(float(exp) / float(max_val) * plot_h) if max_val else 0
        gx = 45 + i * col_w
        months.append({
            "m": m,
            "name": _MONTHS_RU[i],
            "income": inc,
            "expense": exp,
            "profit": inc - exp,
            "income_fmt": _money(inc),
            "expense_fmt": _money(exp),
            "profit_fmt": _money(inc - exp),
            "in_x": gx,
            "in_y": base_y - in_h,
            "in_h": in_h,
            "ex_x": gx + 22,
            "ex_y": base_y - ex_h,
            "ex_h": ex_h,
            "lbl_x": gx + 20,
        })

    context = {
        "country": country,
        "year": year,
        "years": years,
        "months": months,
        "total_income": total_income,
        "total_expense": total_expense,
        "total_profit": total_income - total_expense,
        "total_income_fmt": _money(total_income),
        "total_expense_fmt": _money(total_expense),
        "total_profit_fmt": _money(total_income - total_expense),
        "base_y": base_y,
        "active": "chart",
    }
    return render(request, "foodcost/finance_chart.html", context)
