"""Финансы → Расходы.

Отдельный модуль (как views_techcards): большой views.py не трогаем, чтобы
случайно не сломать сборку. Здесь:
  - finance_expenses: список расходов с фильтрами + форма добавления;
  - управление списком должников (только супер-админ).

Расходы хранятся в модели FinancialExpense. Закупки из приходов попадают сюда
автоматически через сигнал в models.py (тип «Закупка»), здесь они просто
показываются в общем списке с пометкой «авто».
"""

from datetime import date

from django.shortcuts import render, redirect
from django.http import HttpResponseForbidden
from django.db.models import Sum

from .models import FinancialExpense, ExpenseDebtor, Location, UserProfile
from .views import get_country, require_section_access


def _is_super(user):
    """Супер-админ: суперпользователь Django или роль super_admin в профиле."""
    if getattr(user, "is_superuser", False):
        return True
    profile = getattr(user, "profile", None)
    try:
        return bool(profile and profile.is_super_admin())
    except Exception:
        return False


def finance_expenses(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_FINANCE)
    if access_error:
        return access_error

    is_super = _is_super(request.user)
    back = f"/c/{country.slug}/finance/expenses/"

    if request.method == "POST":
        action = request.POST.get("action") or "add_expense"

        # ---- Управление должниками (только супер-админ) ----
        if action == "add_debtor":
            if not is_super:
                return HttpResponseForbidden("Только супер-админ")
            name = (request.POST.get("debtor_name") or "").strip()
            if name:
                ExpenseDebtor.objects.get_or_create(country=country, name=name)
            return redirect(back)

        if action == "toggle_debtor":
            if not is_super:
                return HttpResponseForbidden("Только супер-админ")
            d = ExpenseDebtor.objects.filter(
                id=request.POST.get("debtor_id"), country=country
            ).first()
            if d:
                d.is_active = not d.is_active
                d.save(update_fields=["is_active"])
            return redirect(back)

        # ---- Добавить расход ----
        # Филиал: "general" (или пусто) = общий (location=None)
        location = None
        loc_raw = request.POST.get("location") or ""
        if loc_raw and loc_raw != "general":
            location = Location.objects.filter(id=loc_raw, country=country).first()

        source = request.POST.get("source") or ""

        debtor = None
        if source == FinancialExpense.SOURCE_DEBT:
            debtor = ExpenseDebtor.objects.filter(
                id=request.POST.get("debtor") or 0, country=country
            ).first()

        legal_entity = ""
        if source == FinancialExpense.SOURCE_COMPANY:
            legal_entity = (request.POST.get("legal_entity") or "").strip()

        expense_date = request.POST.get("expense_date") or date.today().isoformat()
        expense_type = request.POST.get("expense_type") or FinancialExpense.EXPENSE_OTHER

        FinancialExpense.objects.create(
            country=country,
            location=location,
            expense_type=expense_type,
            amount=request.POST.get("amount") or 0,
            expense_date=expense_date,
            source=source,
            legal_entity=legal_entity,
            debtor=debtor,
            comment=(request.POST.get("comment") or "").strip(),
            created_by=request.user if getattr(request.user, "is_authenticated", False) else None,
        )
        return redirect(back)

    # ---- GET: фильтры + список ----
    qs = FinancialExpense.objects.filter(country=country).select_related(
        "location", "debtor", "purchase_receipt", "created_by"
    )

    f_from = (request.GET.get("date_from") or "").strip()
    f_to = (request.GET.get("date_to") or "").strip()
    f_type = (request.GET.get("type") or "").strip()
    f_location = (request.GET.get("location") or "").strip()
    f_source = (request.GET.get("source") or "").strip()

    if f_from:
        qs = qs.filter(expense_date__gte=f_from)
    if f_to:
        qs = qs.filter(expense_date__lte=f_to)
    if f_type:
        qs = qs.filter(expense_type=f_type)
    if f_location == "general":
        qs = qs.filter(location__isnull=True)
    elif f_location:
        qs = qs.filter(location_id=f_location)
    if f_source:
        qs = qs.filter(source=f_source)

    qs = qs.order_by("-expense_date", "-id")
    total = qs.aggregate(s=Sum("amount"))["s"] or 0

    type_labels = dict(FinancialExpense.EXPENSE_TYPES)
    source_labels = dict(FinancialExpense.SOURCE_CHOICES)

    rows = []
    for e in qs[:1000]:
        if e.location:
            loc = e.location.name
        else:
            loc = "Общий"
        if e.debtor:
            extra = "Долг: " + e.debtor.name
        elif e.legal_entity:
            extra = e.legal_entity
        else:
            extra = ""
        rows.append({
            "obj": e,
            "type_label": type_labels.get(e.expense_type, e.expense_type),
            "source_label": source_labels.get(e.source, ""),
            "location_label": loc,
            "extra": extra,
            "is_auto": e.purchase_receipt_id is not None,
        })

    # В форме добавления не показываем legacy-тип «Коммуналка (старое)»
    form_types = [
        (k, v) for k, v in FinancialExpense.EXPENSE_TYPES
        if k != FinancialExpense.EXPENSE_UTILITIES
    ]

    context = {
        "country": country,
        "rows": rows,
        "total": total,
        "count": len(rows),
        "locations": Location.objects.filter(
            country=country, is_active=True
        ).order_by("name"),
        "form_types": form_types,
        "all_types": FinancialExpense.EXPENSE_TYPES,
        "sources": FinancialExpense.SOURCE_CHOICES,
        "active_debtors": ExpenseDebtor.objects.filter(
            country=country, is_active=True
        ),
        "all_debtors": ExpenseDebtor.objects.filter(country=country),
        "is_super": is_super,
        "today": date.today().isoformat(),
        "f_from": f_from, "f_to": f_to, "f_type": f_type,
        "f_location": f_location, "f_source": f_source,
        "SOURCE_DEBT": FinancialExpense.SOURCE_DEBT,
        "SOURCE_COMPANY": FinancialExpense.SOURCE_COMPANY,
    }
    return render(request, "foodcost/finance_expenses.html", context)
