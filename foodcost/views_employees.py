from decimal import Decimal
from datetime import date

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Sum, Q
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from .models import (
    Employee,
    Location,
    EmployeeShift,
    EmployeePenaltyType,
    EmployeePenalty,
    EmployeePayment,
    EmployeePayrollEntry,
    UserProfile,
)

from .views import get_country, require_section_access


def clean_decimal(value):
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value).replace(",", ".").replace(" ", ""))
    except Exception:
        return Decimal("0")


def _parse_date(value):
    if not value:
        return None
    if hasattr(value, "year"):
        return value
    from datetime import datetime
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(str(value), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _resolve_user(user_id):
    """Пользователь по id (или None)."""
    if not user_id:
        return None
    return User.objects.filter(id=user_id).first()


def _set_employee_user(employee, user):
    """Безопасно привязывает пользователя к сотруднику (1 пользователь — 1 сотрудник).
    Снимает связь у других сотрудников, если этот пользователь был привязан к ним."""
    if user is not None:
        Employee.objects.filter(user=user).exclude(id=employee.id).update(user=None)
    employee.user = user


def _country_users(country, current_user=None):
    """Пользователи, доступные для привязки: имеющие доступ к этой стране,
    суперпользователи и уже привязанные сотрудники этой страны. Плюс текущий."""
    q = Q(profile__countries=country) | Q(is_superuser=True) | Q(employee_profile__country=country)
    qs = User.objects.filter(q)
    if current_user is not None:
        qs = qs | User.objects.filter(id=current_user.id)
    return qs.distinct().order_by("username")


GEOFENCE_RADIUS_M = 200


def _distance_m(lat1, lng1, lat2, lng2):
    """Расстояние между двумя точками в метрах (формула гаверсинуса)."""
    import math
    r = 6371000.0
    p1 = math.radians(float(lat1))
    p2 = math.radians(float(lat2))
    dp = math.radians(float(lat2) - float(lat1))
    dl = math.radians(float(lng2) - float(lng1))
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _check_geofence(employee, request):
    """Проверяет, что сотрудник в пределах радиуса от своей точки.
    Возвращает (ok, message, distance_m). Если у точки нет координат —
    проверку пропускаем (ok=True), чтобы не блокировать."""
    loc = employee.location
    if not loc or loc.latitude is None or loc.longitude is None:
        return True, None, None
    try:
        lat = float(request.POST.get("lat"))
        lng = float(request.POST.get("lng"))
    except (TypeError, ValueError):
        return False, "Не удалось определить геолокацию. Разрешите доступ к геопозиции.", None
    dist = _distance_m(lat, lng, loc.latitude, loc.longitude)
    if dist > GEOFENCE_RADIUS_M:
        return False, f"Вы вне зоны точки (≈{int(dist)} м).", dist
    return True, None, dist


def _fmt_money(value):
    try:
        value = Decimal(value or 0)
    except Exception:
        value = Decimal(0)
    neg = value < 0
    value = abs(value).quantize(Decimal("1"))
    s = f"{int(value):,}".replace(",", " ")
    return f"-{s}" if neg else s


def _fmt_qty(value):
    try:
        value = Decimal(value or 0)
    except Exception:
        value = Decimal(0)
    if value == value.to_integral_value():
        return str(int(value))
    return str(value.normalize())


@login_required(login_url="/login/")
def employee_list(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_EMPLOYEES)
    if access_error:
        return access_error

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create_employee":
            location = None
            if request.POST.get("location_id"):
                location = Location.objects.filter(
                    id=request.POST.get("location_id"), country=country
                ).first()
            emp = Employee.objects.create(
                country=country,
                name=request.POST.get("name", "").strip(),
                phone=request.POST.get("phone", "").strip(),
                position=request.POST.get("position", "").strip(),
                location=location,
                pay_type=request.POST.get("pay_type") or Employee.PAY_TYPE_HOURLY,
                role=request.POST.get("role") or "",
                hourly_rate_amount=clean_decimal(request.POST.get("hourly_rate_amount")),
                shift_rate_amount=clean_decimal(request.POST.get("shift_rate_amount")),
                shift_kpi_amount=clean_decimal(request.POST.get("shift_kpi_amount")),
                monthly_salary=clean_decimal(request.POST.get("monthly_salary")),
                default_shift_hours=clean_decimal(request.POST.get("default_shift_hours")) or Decimal("12"),
                hire_date=_parse_date(request.POST.get("hire_date")),
                is_active=bool(request.POST.get("is_active")),
            )
            user = _resolve_user(request.POST.get("user_id"))
            if user:
                _set_employee_user(emp, user)
                emp.save(update_fields=["user"])

        if action == "update_employee":
            employee = get_object_or_404(Employee, id=request.POST.get("employee_id"), country=country)
            location = None
            if request.POST.get("location_id"):
                location = Location.objects.filter(
                    id=request.POST.get("location_id"), country=country
                ).first()
            employee.name = request.POST.get("name", "").strip()
            employee.phone = request.POST.get("phone", "").strip()
            employee.position = request.POST.get("position", "").strip()
            employee.location = location
            employee.pay_type = request.POST.get("pay_type") or Employee.PAY_TYPE_HOURLY
            employee.role = request.POST.get("role") or ""
            employee.hourly_rate_amount = clean_decimal(request.POST.get("hourly_rate_amount"))
            employee.shift_rate_amount = clean_decimal(request.POST.get("shift_rate_amount"))
            employee.shift_kpi_amount = clean_decimal(request.POST.get("shift_kpi_amount"))
            employee.monthly_salary = clean_decimal(request.POST.get("monthly_salary"))
            employee.default_shift_hours = clean_decimal(request.POST.get("default_shift_hours")) or Decimal("12")
            employee.hire_date = _parse_date(request.POST.get("hire_date"))
            employee.is_active = bool(request.POST.get("is_active"))
            _set_employee_user(employee, _resolve_user(request.POST.get("user_id")))
            employee.save()

        if action == "archive_employee":
            employee = get_object_or_404(Employee, id=request.POST.get("employee_id"), country=country)
            employee.is_active = not employee.is_active
            employee.save(update_fields=["is_active"])

        return redirect(f"/c/{country.slug}/employees/")

    # ---------- GET ----------
    today = timezone.localdate()
    month_start = date(today.year, today.month, 1)

    search = (request.GET.get("search") or "").strip()
    status = (request.GET.get("status") or "").strip()
    position = (request.GET.get("position") or "").strip()
    role = (request.GET.get("role") or "").strip()
    location_id = (request.GET.get("location") or "").strip()

    qs = Employee.objects.filter(country=country).select_related("location", "user")
    if search:
        qs = qs.filter(Q(name__icontains=search) | Q(phone__icontains=search))
    if status == "active":
        qs = qs.filter(is_active=True)
    elif status == "inactive":
        qs = qs.filter(is_active=False)
    if position:
        qs = qs.filter(position=position)
    if role:
        qs = qs.filter(role=role)
    if location_id:
        qs = qs.filter(location_id=location_id)
    qs = qs.order_by("-is_active", "name")
    employees = list(qs)

    worked_statuses = [EmployeeShift.STATUS_DONE, EmployeeShift.STATUS_LATE]
    hours_map = {}
    for row in (
        EmployeeShift.objects
        .filter(country=country, shift_date__gte=month_start, shift_date__lte=today, status__in=worked_statuses)
        .values("employee_id").annotate(h=Sum("hours"))
    ):
        hours_map[row["employee_id"]] = row["h"] or Decimal(0)

    month_by_emp = {}
    for row in (
        EmployeePayrollEntry.objects
        .filter(country=country, status=EmployeePayrollEntry.STATUS_DONE,
                entry_date__gte=month_start, entry_date__lte=today)
        .values("employee_id", "entry_type").annotate(s=Sum("amount"))
    ):
        d = month_by_emp.setdefault(row["employee_id"], {})
        d[row["entry_type"]] = row["s"] or Decimal(0)

    all_by_emp = {}
    for row in (
        EmployeePayrollEntry.objects
        .filter(country=country, status=EmployeePayrollEntry.STATUS_DONE)
        .values("employee_id", "entry_type").annotate(s=Sum("amount"))
    ):
        d = all_by_emp.setdefault(row["employee_id"], {})
        d[row["entry_type"]] = row["s"] or Decimal(0)

    T = EmployeePayrollEntry

    def _accrued(d):
        return (d.get(T.TYPE_SALARY, 0) or 0) + (d.get(T.TYPE_BONUS, 0) or 0)

    def _paid(d):
        return (d.get(T.TYPE_PAYOUT, 0) or 0) + (d.get(T.TYPE_ADVANCE, 0) or 0)

    def _balance(d):
        bal = Decimal(0)
        for et, s in d.items():
            s = s or Decimal(0)
            if et == T.TYPE_CORRECTION:
                bal += s
            elif et in T.NEGATIVE_TYPES:
                bal -= abs(s)
            elif et in T.POSITIVE_TYPES:
                bal += abs(s)
        return bal

    rows = []
    kpi_hours = kpi_accrued = kpi_paid = kpi_balance = Decimal(0)
    active_count = 0
    for e in employees:
        m = month_by_emp.get(e.id, {})
        a = all_by_emp.get(e.id, {})
        h = hours_map.get(e.id, Decimal(0))
        accrued = _accrued(m)
        paid = _paid(m)
        balance = _balance(a)
        if e.is_active:
            active_count += 1
        kpi_hours += h
        kpi_accrued += accrued
        kpi_paid += paid
        kpi_balance += balance
        if e.pay_type == Employee.PAY_TYPE_HOURLY:
            rate_val, rate_unit = e.hourly_rate_amount, "сум/ч"
        elif e.pay_type == Employee.PAY_TYPE_SALARY:
            rate_val, rate_unit = e.monthly_salary, "сум/мес"
        else:
            rate_val, rate_unit = e.shift_rate_amount, "сум/смена"
        rows.append({
            "e": e,
            "initials": (e.name[:2].upper() if e.name else "—"),
            "role_label": e.get_role_display() if e.role else "",
            "rate_display": _fmt_money(rate_val),
            "rate_unit": rate_unit,
            "hours_display": _fmt_qty(h),
            "accrued_display": _fmt_money(accrued),
            "paid_display": _fmt_money(paid),
            "balance_display": _fmt_money(balance),
            "balance_negative": balance < 0,
        })

    positions = sorted({e.position for e in Employee.objects.filter(country=country) if e.position})

    return render(request, "foodcost/employee_list.html", {
        "country": country,
        "rows": rows,
        "total_count": len(employees),
        "active_count": active_count,
        "kpi_hours_display": _fmt_qty(kpi_hours),
        "kpi_accrued_display": _fmt_money(kpi_accrued),
        "kpi_paid_display": _fmt_money(kpi_paid),
        "kpi_balance_display": _fmt_money(kpi_balance),
        "locations": Location.objects.filter(country=country, is_active=True).order_by("name"),
        "positions": positions,
        "pay_types": Employee.PAY_TYPE_CHOICES,
        "roles": Employee.ROLE_CHOICES,
        "users": _country_users(country),
        "search": search, "status": status, "position": position, "role": role, "location_id": location_id,
    })


def _shift_hours(start, end, break_min, default_hours):
    """Часы смены по времени начала/окончания минус перерыв; иначе дефолт."""
    if start and end:
        from datetime import datetime, date as _d
        s = datetime.combine(_d(2000, 1, 1), start)
        e = datetime.combine(_d(2000, 1, 1), end)
        diff = (e - s).total_seconds() / 3600.0
        if diff < 0:
            diff += 24  # ночная смена через полночь
        diff -= (break_min or 0) / 60.0
        return Decimal(str(round(diff, 2))) if diff > 0 else Decimal(0)
    return default_hours or Decimal(0)


def _parse_time(value):
    if not value:
        return None
    from datetime import datetime
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(str(value), fmt).time()
        except (ValueError, TypeError):
            continue
    return None


@login_required(login_url="/login/")
def employee_detail(request, country_slug, employee_id):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_EMPLOYEES)
    if access_error:
        return access_error

    from .views import user_can_edit
    can_edit = user_can_edit(request.user)
    profile = getattr(request.user, "profile", None)
    is_super = bool(profile and profile.is_super_admin())

    employee = get_object_or_404(Employee, id=employee_id, country=country)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "update_employee" and can_edit:
            location = None
            if request.POST.get("location_id"):
                location = Location.objects.filter(id=request.POST.get("location_id"), country=country).first()
            employee.name = request.POST.get("name", "").strip()
            employee.phone = request.POST.get("phone", "").strip()
            employee.position = request.POST.get("position", "").strip()
            employee.location = location
            employee.pay_type = request.POST.get("pay_type") or Employee.PAY_TYPE_HOURLY
            employee.role = request.POST.get("role") or ""
            employee.hourly_rate_amount = clean_decimal(request.POST.get("hourly_rate_amount"))
            employee.shift_rate_amount = clean_decimal(request.POST.get("shift_rate_amount"))
            employee.shift_kpi_amount = clean_decimal(request.POST.get("shift_kpi_amount"))
            employee.monthly_salary = clean_decimal(request.POST.get("monthly_salary"))
            employee.default_shift_hours = clean_decimal(request.POST.get("default_shift_hours")) or Decimal("12")
            employee.hire_date = _parse_date(request.POST.get("hire_date"))
            employee.is_active = bool(request.POST.get("is_active"))
            _set_employee_user(employee, _resolve_user(request.POST.get("user_id")))
            employee.save()
            return redirect(f"/c/{country.slug}/employees/{employee.id}/")

        if action == "add_shift" and can_edit:
            location = employee.location
            if request.POST.get("location_id"):
                location = Location.objects.filter(id=request.POST.get("location_id"), country=country).first()
            start = _parse_time(request.POST.get("start_time"))
            end = _parse_time(request.POST.get("end_time"))
            break_min = 0
            try:
                break_min = int(request.POST.get("break_minutes") or 0)
            except (TypeError, ValueError):
                break_min = 0
            hours = _shift_hours(start, end, break_min, employee.default_shift_hours)
            status = request.POST.get("status") or EmployeeShift.STATUS_PLANNED
            EmployeeShift.objects.create(
                country=country, employee=employee, location=location,
                shift_date=_parse_date(request.POST.get("shift_date")) or timezone.localdate(),
                start_time=start, end_time=end, break_minutes=break_min,
                shift_type=request.POST.get("shift_type") or EmployeeShift.SHIFT_TYPE_DAY,
                planned_hours=hours, actual_hours=hours, hours=hours,
                fixed_amount=employee.shift_rate_amount, kpi_amount=employee.shift_kpi_amount,
                kpi_percent=Decimal("100"), hourly_rate_snapshot=employee.hourly_rate_amount,
                tax_percent_snapshot=employee.tax_percent,
                status=status, comment=request.POST.get("comment", "").strip(),
            )
            return redirect(f"/c/{country.slug}/employees/{employee.id}/?tab=schedule")

        if action == "delete_shift" and can_edit:
            EmployeeShift.objects.filter(id=request.POST.get("shift_id"), employee=employee).delete()
            return redirect(f"/c/{country.slug}/employees/{employee.id}/?tab=schedule")

        if action == "add_payroll" and can_edit:
            EmployeePayrollEntry.objects.create(
                country=country, employee=employee,
                entry_type=request.POST.get("entry_type") or EmployeePayrollEntry.TYPE_SALARY,
                entry_date=_parse_date(request.POST.get("entry_date")) or timezone.localdate(),
                period=request.POST.get("period", "").strip(),
                amount=clean_decimal(request.POST.get("amount")),
                status=request.POST.get("status") or EmployeePayrollEntry.STATUS_DONE,
                comment=request.POST.get("comment", "").strip(),
                created_by=request.user,
            )
            return redirect(f"/c/{country.slug}/employees/{employee.id}/?tab=payroll")

        if action == "delete_payroll" and is_super:
            EmployeePayrollEntry.objects.filter(id=request.POST.get("payroll_id"), employee=employee).delete()
            return redirect(f"/c/{country.slug}/employees/{employee.id}/?tab=payroll")

        return redirect(f"/c/{country.slug}/employees/{employee.id}/")

    # ---------- GET ----------
    today = timezone.localdate()
    month_start = date(today.year, today.month, 1)

    # выбранный месяц для графика
    mp = request.GET.get("month")
    sel_y, sel_m = today.year, today.month
    if mp:
        try:
            sel_y, sel_m = int(mp[:4]), int(mp[5:7])
        except (ValueError, IndexError):
            pass
    sel_start = date(sel_y, sel_m, 1)
    if sel_m == 12:
        nxt = date(sel_y + 1, 1, 1)
    else:
        nxt = date(sel_y, sel_m + 1, 1)
    prev = (sel_start.replace(day=1) - __import__("datetime").timedelta(days=1)).replace(day=1)
    months_ru = ["", "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
                 "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

    worked_statuses = [EmployeeShift.STATUS_DONE, EmployeeShift.STATUS_LATE]

    # KPI за текущий месяц
    month_hours = (
        EmployeeShift.objects.filter(
            employee=employee, shift_date__gte=month_start, shift_date__lte=today,
            status__in=worked_statuses
        ).aggregate(s=Sum("hours"))["s"] or Decimal(0)
    )
    plan_hours = (
        EmployeeShift.objects.filter(
            employee=employee, shift_date__gte=month_start
        ).exclude(status=EmployeeShift.STATUS_CANCELLED).aggregate(s=Sum("planned_hours"))["s"] or Decimal(0)
    )

    T = EmployeePayrollEntry
    # суммы за месяц по типам
    by_type_month = {}
    for row in (
        T.objects.filter(employee=employee, status=T.STATUS_DONE,
                         entry_date__gte=month_start, entry_date__lte=today)
        .values("entry_type").annotate(s=Sum("amount"))
    ):
        by_type_month[row["entry_type"]] = row["s"] or Decimal(0)
    # совокупно по типам (для остатка и вкладки «Общее»)
    by_type_all = {}
    for row in (
        T.objects.filter(employee=employee, status=T.STATUS_DONE)
        .values("entry_type").annotate(s=Sum("amount"))
    ):
        by_type_all[row["entry_type"]] = row["s"] or Decimal(0)

    def g(d, k):
        return d.get(k, Decimal(0)) or Decimal(0)

    accrued_m = g(by_type_month, T.TYPE_SALARY) + g(by_type_month, T.TYPE_BONUS)
    paid_m = g(by_type_month, T.TYPE_PAYOUT) + g(by_type_month, T.TYPE_ADVANCE)

    # остаток (совокупно)
    balance = (
        g(by_type_all, T.TYPE_SALARY) + g(by_type_all, T.TYPE_BONUS)
        - g(by_type_all, T.TYPE_PAYOUT) - g(by_type_all, T.TYPE_ADVANCE)
        - g(by_type_all, T.TYPE_PENALTY) + g(by_type_all, T.TYPE_CORRECTION)
    )

    # смены выбранного месяца
    shift_objs = list(
        EmployeeShift.objects.filter(employee=employee, shift_date__gte=sel_start, shift_date__lt=nxt)
        .select_related("location").order_by("-shift_date")
    )
    shifts = []
    for s in shift_objs:
        shifts.append({
            "id": s.id,
            "date": s.shift_date,
            "start": s.start_time.strftime("%H:%M") if s.start_time else "—",
            "end": s.end_time.strftime("%H:%M") if s.end_time else "—",
            "location": s.location.name if s.location else "—",
            "hours": _fmt_qty(s.hours or 0),
            "status": s.status,
            "status_label": s.get_status_display(),
        })

    # начисления (журнал, последние 200 — фильтрация на клиенте)
    payroll_objs = list(
        T.objects.filter(employee=employee).select_related("created_by").order_by("-entry_date", "-id")[:200]
    )
    payroll = []
    for p in payroll_objs:
        payroll.append({
            "id": p.id,
            "date": p.entry_date,
            "type": p.entry_type,
            "type_label": p.get_entry_type_display(),
            "comment": p.comment,
            "amount_display": _fmt_money(abs(p.amount or 0)),
            "signed": p.signed_amount(),
            "is_negative": p.signed_amount() < 0,
            "status": p.status,
            "status_label": p.get_status_display(),
            "author": (p.created_by.username if p.created_by else "—"),
        })

    last_shifts = shifts[:5]
    last_payroll = payroll[:5]

    if employee.pay_type == Employee.PAY_TYPE_HOURLY:
        rate_display, rate_unit = _fmt_money(employee.hourly_rate_amount), "сум/ч"
    elif employee.pay_type == Employee.PAY_TYPE_SALARY:
        rate_display, rate_unit = _fmt_money(employee.monthly_salary), "сум/мес"
    else:
        rate_display, rate_unit = _fmt_money(employee.shift_rate_amount), "сум/смена"

    return render(request, "foodcost/employee_detail.html", {
        "country": country,
        "employee": employee,
        "can_edit": can_edit,
        "is_super": is_super,
        "active_tab": request.GET.get("tab") or "general",
        "initials": (employee.name[:2].upper() if employee.name else "—"),
        "rate_display": rate_display, "rate_unit": rate_unit,
        "pay_type_label": employee.get_pay_type_display(),
        # KPI
        "month_hours_display": _fmt_qty(month_hours),
        "plan_hours_display": _fmt_qty(plan_hours),
        "accrued_month_display": _fmt_money(accrued_m),
        "paid_month_display": _fmt_money(paid_m),
        "balance_display": _fmt_money(balance),
        "balance_date": today,
        # вкладка Начисления — KPI по типам (за месяц)
        "k_accrued": _fmt_money(g(by_type_month, T.TYPE_SALARY)),
        "k_paid": _fmt_money(g(by_type_month, T.TYPE_PAYOUT)),
        "k_advance": _fmt_money(g(by_type_month, T.TYPE_ADVANCE)),
        "k_penalty": _fmt_money(g(by_type_month, T.TYPE_PENALTY)),
        "k_bonus": _fmt_money(g(by_type_month, T.TYPE_BONUS)),
        # расчёт остатка (вкладка Общее) — совокупно
        "calc_accrued": _fmt_money(g(by_type_all, T.TYPE_SALARY)),
        "calc_payout": _fmt_money(g(by_type_all, T.TYPE_PAYOUT)),
        "calc_advance": _fmt_money(g(by_type_all, T.TYPE_ADVANCE)),
        "calc_penalty": _fmt_money(g(by_type_all, T.TYPE_PENALTY)),
        "calc_bonus": _fmt_money(g(by_type_all, T.TYPE_BONUS)),
        # данные
        "shifts": shifts, "payroll": payroll,
        "last_shifts": last_shifts, "last_payroll": last_payroll,
        "locations": Location.objects.filter(country=country, is_active=True).order_by("name"),
        "pay_types": Employee.PAY_TYPE_CHOICES,
        "roles": Employee.ROLE_CHOICES,
        "users": _country_users(country, current_user=employee.user),
        "payroll_types": T.TYPE_CHOICES,
        "payroll_statuses": T.STATUS_CHOICES,
        "shift_statuses": EmployeeShift.STATUS_CHOICES,
        "shift_types": EmployeeShift.SHIFT_TYPE_CHOICES,
        # месяц
        "sel_month_label": f"{months_ru[sel_m]} {sel_y}",
        "prev_month": f"{prev.year}-{prev.month:02d}",
        "next_month": f"{nxt.year}-{nxt.month:02d}",
    })


@login_required(login_url="/login/")
def schedule_page(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_SCHEDULE)
    if access_error:
        return access_error

    from .views import user_can_edit
    can_edit = user_can_edit(request.user)

    if request.method == "POST" and can_edit:
        action = request.POST.get("action")

        if action == "add_shift":
            employee = Employee.objects.filter(id=request.POST.get("employee_id"), country=country).first()
            if employee:
                location = employee.location
                if request.POST.get("location_id"):
                    location = Location.objects.filter(id=request.POST.get("location_id"), country=country).first()
                start = _parse_time(request.POST.get("start_time"))
                end = _parse_time(request.POST.get("end_time"))
                try:
                    break_min = int(request.POST.get("break_minutes") or 0)
                except (TypeError, ValueError):
                    break_min = 0
                hours = _shift_hours(start, end, break_min, employee.default_shift_hours)
                EmployeeShift.objects.create(
                    country=country, employee=employee, location=location,
                    shift_date=_parse_date(request.POST.get("shift_date")) or timezone.localdate(),
                    start_time=start, end_time=end, break_minutes=break_min,
                    shift_type=request.POST.get("shift_type") or EmployeeShift.SHIFT_TYPE_DAY,
                    planned_hours=hours, actual_hours=hours, hours=hours,
                    fixed_amount=employee.shift_rate_amount, kpi_amount=employee.shift_kpi_amount,
                    kpi_percent=Decimal("100"), hourly_rate_snapshot=employee.hourly_rate_amount,
                    tax_percent_snapshot=employee.tax_percent,
                    status=request.POST.get("status") or EmployeeShift.STATUS_PLANNED,
                    comment=request.POST.get("comment", "").strip(),
                )

        if action == "delete_shift":
            EmployeeShift.objects.filter(id=request.POST.get("shift_id"), country=country).delete()

        wk = request.POST.get("week") or ""
        loc = request.POST.get("flt_location") or ""
        suffix = []
        if wk:
            suffix.append(f"week={wk}")
        if loc:
            suffix.append(f"location={loc}")
        q = ("?" + "&".join(suffix)) if suffix else ""
        return redirect(f"/c/{country.slug}/schedule/{q}")

    # ---------- GET ----------
    import datetime as _dt
    today = timezone.localdate()
    ref = _parse_date(request.GET.get("week")) or today
    monday = ref - _dt.timedelta(days=ref.weekday())
    days = [monday + _dt.timedelta(days=i) for i in range(7)]
    day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    location_id = (request.GET.get("location") or "").strip()

    emp_qs = Employee.objects.filter(country=country, is_active=True).select_related("location")
    if location_id:
        emp_qs = emp_qs.filter(location_id=location_id)
    employees = list(emp_qs.order_by("position", "name"))

    # смены недели
    week_end = monday + _dt.timedelta(days=6)
    shift_qs = EmployeeShift.objects.filter(
        country=country, shift_date__gte=monday, shift_date__lte=week_end
    ).exclude(status=EmployeeShift.STATUS_CANCELLED).select_related("location")
    if location_id:
        shift_qs = shift_qs.filter(location_id=location_id)

    shift_map = {}  # (emp_id, iso) -> list of shift dicts
    day_hours = {d.isoformat(): Decimal(0) for d in days}
    for s in shift_qs:
        key = (s.employee_id, s.shift_date.isoformat())
        shift_map.setdefault(key, []).append({
            "id": s.id,
            "time": (f"{s.start_time.strftime('%H:%M')}–{s.end_time.strftime('%H:%M')}" if s.start_time and s.end_time else _fmt_qty(s.hours or 0) + " ч"),
            "location": s.location.name if s.location else "",
            "status": s.status,
        })
        day_hours[s.shift_date.isoformat()] = day_hours.get(s.shift_date.isoformat(), Decimal(0)) + (s.hours or Decimal(0))

    # строки сетки
    rows = []
    for e in employees:
        cells = []
        for d in days:
            cells.append({
                "date": d,
                "iso": d.isoformat(),
                "is_weekend": d.weekday() >= 5,
                "shifts": shift_map.get((e.id, d.isoformat()), []),
            })
        rows.append({
            "id": e.id,
            "name": e.name,
            "position": e.position or "—",
            "initials": (e.name[:2].upper() if e.name else "—"),
            "cells": cells,
        })

    header_days = []
    for i, d in enumerate(days):
        header_days.append({
            "label": day_names[i],
            "day": d.day,
            "is_weekend": d.weekday() >= 5,
            "hours": _fmt_qty(day_hours.get(d.isoformat(), Decimal(0))),
        })

    months_ru = ["", "января", "февраля", "марта", "апреля", "мая", "июня", "июля",
                 "августа", "сентября", "октября", "ноября", "декабря"]
    week_label = f"{monday.day} {months_ru[monday.month]} – {week_end.day} {months_ru[week_end.month]} {week_end.year}"
    prev_week = (monday - _dt.timedelta(days=7)).isoformat()
    next_week = (monday + _dt.timedelta(days=7)).isoformat()

    return render(request, "foodcost/schedule.html", {
        "country": country,
        "can_edit": can_edit,
        "rows": rows,
        "header_days": header_days,
        "week_label": week_label,
        "week_value": monday.isoformat(),
        "prev_week": prev_week,
        "next_week": next_week,
        "today_week": today.isoformat(),
        "location_id": location_id,
        "locations": Location.objects.filter(country=country, is_active=True).order_by("name"),
        "employees": employees,
        "shift_types": EmployeeShift.SHIFT_TYPE_CHOICES,
        "shift_statuses": EmployeeShift.STATUS_CHOICES,
    })


@login_required(login_url="/login/")
def shifts_journal(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_SHIFTS)
    if access_error:
        return access_error

    from .views import user_can_edit
    can_edit = user_can_edit(request.user)

    if request.method == "POST" and can_edit:
        action = request.POST.get("action")
        now = timezone.localtime()

        if action == "start_new":
            employee = Employee.objects.filter(id=request.POST.get("employee_id"), country=country).first()
            if employee:
                location = employee.location
                if request.POST.get("location_id"):
                    location = Location.objects.filter(id=request.POST.get("location_id"), country=country).first()
                EmployeeShift.objects.create(
                    country=country, employee=employee, location=location,
                    shift_date=timezone.localdate(),
                    start_time=now.time().replace(second=0, microsecond=0),
                    break_minutes=0, shift_type=EmployeeShift.SHIFT_TYPE_DAY,
                    planned_hours=employee.default_shift_hours,
                    actual_hours=Decimal(0), hours=Decimal(0),
                    fixed_amount=employee.shift_rate_amount, kpi_amount=employee.shift_kpi_amount,
                    kpi_percent=Decimal("100"), hourly_rate_snapshot=employee.hourly_rate_amount,
                    tax_percent_snapshot=employee.tax_percent,
                    status=EmployeeShift.STATUS_IN_PROGRESS, comment="",
                )

        if action == "start_shift":
            sh = EmployeeShift.objects.filter(id=request.POST.get("shift_id"), country=country).first()
            if sh:
                sh.start_time = now.time().replace(second=0, microsecond=0)
                sh.status = EmployeeShift.STATUS_IN_PROGRESS
                sh.save(update_fields=["start_time", "status"])

        if action == "finish_shift":
            sh = EmployeeShift.objects.filter(id=request.POST.get("shift_id"), country=country).first()
            if sh:
                end_t = now.time().replace(second=0, microsecond=0)
                hours = _shift_hours(sh.start_time, end_t, sh.break_minutes, sh.planned_hours)
                sh.end_time = end_t
                sh.hours = hours
                sh.actual_hours = hours
                sh.status = EmployeeShift.STATUS_DONE
                sh.save(update_fields=["end_time", "hours", "actual_hours", "status"])

        return redirect(f"/c/{country.slug}/shifts/")

    # ---------- GET ----------
    today = timezone.localdate()
    date_from = _parse_date(request.GET.get("date_from")) or today
    date_to = _parse_date(request.GET.get("date_to")) or today
    location_id = (request.GET.get("location") or "").strip()
    emp_id = (request.GET.get("employee") or "").strip()

    qs = (
        EmployeeShift.objects
        .filter(country=country, shift_date__gte=date_from, shift_date__lte=date_to)
        .select_related("employee", "location")
        .order_by("-shift_date", "-id")
    )
    if location_id:
        qs = qs.filter(location_id=location_id)
    if emp_id:
        qs = qs.filter(employee_id=emp_id)

    rows = []
    total_hours = Decimal(0)
    in_progress = 0
    for s in qs[:300]:
        if s.status in (EmployeeShift.STATUS_DONE, EmployeeShift.STATUS_LATE):
            total_hours += (s.hours or Decimal(0))
        if s.status == EmployeeShift.STATUS_IN_PROGRESS:
            in_progress += 1
        rows.append({
            "id": s.id,
            "employee": s.employee.name if s.employee else "—",
            "initials": (s.employee.name[:2].upper() if s.employee and s.employee.name else "—"),
            "location": s.location.name if s.location else "—",
            "date": s.shift_date,
            "start": s.start_time.strftime("%H:%M") if s.start_time else "—",
            "end": s.end_time.strftime("%H:%M") if s.end_time else "—",
            "hours": _fmt_qty(s.hours or 0),
            "status": s.status,
            "status_label": s.get_status_display(),
        })

    return render(request, "foodcost/shifts.html", {
        "country": country,
        "can_edit": can_edit,
        "rows": rows,
        "count": len(rows),
        "in_progress": in_progress,
        "total_hours_display": _fmt_qty(total_hours),
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "location_id": location_id,
        "emp_id": emp_id,
        "locations": Location.objects.filter(country=country, is_active=True).order_by("name"),
        "employees": Employee.objects.filter(country=country, is_active=True).order_by("name"),
    })


@login_required(login_url="/login/")
def employee_me(request, country_slug):
    """Личный кабинет сотрудника: свои смены, начисления, остаток."""
    country = get_country(country_slug, request.user)

    employee = Employee.objects.filter(user=request.user, country=country).select_related("location").first()
    if not employee:
        return render(request, "foodcost/employee_me.html", {
            "country": country,
            "employee": None,
        })

    now = timezone.localtime()

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "start_new":
            ok, msg, _d = _check_geofence(employee, request)
            if not ok:
                return redirect(f"/c/{country.slug}/employee/me/?geo_error=1")
            EmployeeShift.objects.create(
                country=country, employee=employee, location=employee.location,
                shift_date=timezone.localdate(),
                start_time=now.time().replace(second=0, microsecond=0),
                break_minutes=0, shift_type=EmployeeShift.SHIFT_TYPE_DAY,
                planned_hours=employee.default_shift_hours,
                actual_hours=Decimal(0), hours=Decimal(0),
                fixed_amount=employee.shift_rate_amount, kpi_amount=employee.shift_kpi_amount,
                kpi_percent=Decimal("100"), hourly_rate_snapshot=employee.hourly_rate_amount,
                tax_percent_snapshot=employee.tax_percent,
                status=EmployeeShift.STATUS_IN_PROGRESS, comment="",
            )
        elif action == "start_shift":
            ok, msg, _d = _check_geofence(employee, request)
            if not ok:
                return redirect(f"/c/{country.slug}/employee/me/?geo_error=1")
            sh = EmployeeShift.objects.filter(id=request.POST.get("shift_id"), employee=employee).first()
            if sh:
                sh.start_time = now.time().replace(second=0, microsecond=0)
                sh.status = EmployeeShift.STATUS_IN_PROGRESS
                sh.save(update_fields=["start_time", "status"])
        elif action == "finish_shift":
            sh = EmployeeShift.objects.filter(id=request.POST.get("shift_id"), employee=employee).first()
            if sh:
                end_t = now.time().replace(second=0, microsecond=0)
                hours = _shift_hours(sh.start_time, end_t, sh.break_minutes, sh.planned_hours)
                sh.end_time = end_t
                sh.hours = hours
                sh.actual_hours = hours
                sh.status = EmployeeShift.STATUS_DONE
                sh.save(update_fields=["end_time", "hours", "actual_hours", "status"])
        return redirect(f"/c/{country.slug}/employee/me/")

    today = timezone.localdate()
    month_start = date(today.year, today.month, 1)
    worked_statuses = [EmployeeShift.STATUS_DONE, EmployeeShift.STATUS_LATE]

    month_hours = (
        EmployeeShift.objects.filter(employee=employee, shift_date__gte=month_start,
                                     shift_date__lte=today, status__in=worked_statuses)
        .aggregate(s=Sum("hours"))["s"] or Decimal(0)
    )

    T = EmployeePayrollEntry
    by_type_month = {}
    for row in (T.objects.filter(employee=employee, status=T.STATUS_DONE,
                                 entry_date__gte=month_start, entry_date__lte=today)
                .values("entry_type").annotate(s=Sum("amount"))):
        by_type_month[row["entry_type"]] = row["s"] or Decimal(0)
    by_type_all = {}
    for row in (T.objects.filter(employee=employee, status=T.STATUS_DONE)
                .values("entry_type").annotate(s=Sum("amount"))):
        by_type_all[row["entry_type"]] = row["s"] or Decimal(0)

    def g(d, k):
        return d.get(k, Decimal(0)) or Decimal(0)

    accrued_m = g(by_type_month, T.TYPE_SALARY) + g(by_type_month, T.TYPE_BONUS)
    paid_m = g(by_type_month, T.TYPE_PAYOUT) + g(by_type_month, T.TYPE_ADVANCE)
    balance = (g(by_type_all, T.TYPE_SALARY) + g(by_type_all, T.TYPE_BONUS)
               - g(by_type_all, T.TYPE_PAYOUT) - g(by_type_all, T.TYPE_ADVANCE)
               - g(by_type_all, T.TYPE_PENALTY) + g(by_type_all, T.TYPE_CORRECTION))

    # текущая смена (идёт) + сегодняшние запланированные
    active_shift = EmployeeShift.objects.filter(
        employee=employee, status=EmployeeShift.STATUS_IN_PROGRESS
    ).order_by("-shift_date").first()
    today_planned = list(EmployeeShift.objects.filter(
        employee=employee, shift_date=today, status=EmployeeShift.STATUS_PLANNED
    ).select_related("location"))

    # ближайшие смены (от сегодня вперёд)
    upcoming = list(
        EmployeeShift.objects.filter(employee=employee, shift_date__gte=today)
        .exclude(status=EmployeeShift.STATUS_CANCELLED)
        .select_related("location").order_by("shift_date")[:10]
    )
    # последние смены месяца
    recent_shifts = list(
        EmployeeShift.objects.filter(employee=employee, shift_date__gte=month_start, shift_date__lte=today)
        .select_related("location").order_by("-shift_date")[:10]
    )

    def shift_row(s):
        return {
            "id": s.id, "date": s.shift_date,
            "start": s.start_time.strftime("%H:%M") if s.start_time else "—",
            "end": s.end_time.strftime("%H:%M") if s.end_time else "—",
            "location": s.location.name if s.location else "—",
            "hours": _fmt_qty(s.hours or 0),
            "status": s.status, "status_label": s.get_status_display(),
        }

    payroll = []
    for p in T.objects.filter(employee=employee).order_by("-entry_date", "-id")[:15]:
        payroll.append({
            "date": p.entry_date, "type_label": p.get_entry_type_display(),
            "comment": p.comment, "amount_display": _fmt_money(abs(p.amount or 0)),
            "is_negative": p.signed_amount() < 0,
            "status": p.status, "status_label": p.get_status_display(),
        })

    return render(request, "foodcost/employee_me.html", {
        "country": country,
        "employee": employee,
        "geo_error": request.GET.get("geo_error"),
        "initials": (employee.name[:2].upper() if employee.name else "—"),
        "role_label": employee.get_role_display() if employee.role else "",
        "today": today,
        "month_hours_display": _fmt_qty(month_hours),
        "accrued_month_display": _fmt_money(accrued_m),
        "paid_month_display": _fmt_money(paid_m),
        "balance_display": _fmt_money(balance),
        "active_shift": shift_row(active_shift) if active_shift else None,
        "today_planned": [shift_row(s) for s in today_planned],
        "upcoming": [shift_row(s) for s in upcoming],
        "recent_shifts": [shift_row(s) for s in recent_shifts],
        "payroll": payroll,
    })
