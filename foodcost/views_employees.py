from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from .models import (
    Employee,
    Location,
    EmployeeShift,
    EmployeePenaltyType,
    EmployeePenalty,
    EmployeePayment,
)

from .views import get_country


def clean_decimal(value):
    if value is None or value == "":
        return Decimal("0")

    try:
        return Decimal(str(value).replace(",", "."))
    except Exception:
        return Decimal("0")


@login_required(login_url="/login/")
def employee_list(request, country_slug):

    country = get_country(country_slug, request.user)

    if request.method == "POST":

        action = request.POST.get("action")

        if action == "create_employee":

            location = None

            if request.POST.get("location_id"):
                location = Location.objects.filter(
                    id=request.POST.get("location_id"),
                    country=country
                ).first()

            Employee.objects.create(
                country=country,
                name=request.POST.get("name", "").strip(),
                position=request.POST.get("position", "").strip(),
                location=location,
                shift_rate_amount=clean_decimal(
                    request.POST.get("shift_rate_amount")
                ),
                shift_kpi_amount=clean_decimal(
                    request.POST.get("shift_kpi_amount")
                ),
                default_shift_hours=clean_decimal(
                    request.POST.get("default_shift_hours")
                ) or Decimal("12"),
                is_active=bool(request.POST.get("is_active")),
            )

        if action == "update_employee":

            employee = get_object_or_404(
                Employee,
                id=request.POST.get("employee_id"),
                country=country
            )

            location = None

            if request.POST.get("location_id"):
                location = Location.objects.filter(
                    id=request.POST.get("location_id"),
                    country=country
                ).first()

            employee.name = request.POST.get("name", "").strip()
            employee.position = request.POST.get("position", "").strip()
            employee.location = location
            employee.shift_rate_amount = clean_decimal(
                request.POST.get("shift_rate_amount")
            )
            employee.shift_kpi_amount = clean_decimal(
                request.POST.get("shift_kpi_amount")
            )
            employee.default_shift_hours = (
                clean_decimal(request.POST.get("default_shift_hours"))
                or Decimal("12")
            )
            employee.is_active = bool(request.POST.get("is_active"))
            employee.save()

        if action == "delete_employee":

            employee = get_object_or_404(
                Employee,
                id=request.POST.get("employee_id"),
                country=country
            )

            employee.delete()

        if action == "create_shift":

            employee = Employee.objects.filter(
                id=request.POST.get("employee_id"),
                country=country
            ).first()

            if employee:

                location = employee.location

                if request.POST.get("location_id"):
                    location = Location.objects.filter(
                        id=request.POST.get("location_id"),
                        country=country
                    ).first()

                shift = EmployeeShift.objects.create(
    country=country,
    employee=employee,
    location=location,

    shift_date=request.POST.get("shift_date"),

    planned_hours=employee.default_shift_hours,

    actual_hours=clean_decimal(
        request.POST.get("actual_hours")
    ) or employee.default_shift_hours,

    hours=clean_decimal(
        request.POST.get("actual_hours")
    ) or employee.default_shift_hours,

    fixed_amount=employee.shift_rate_amount,

    kpi_amount=employee.shift_kpi_amount,

    kpi_percent=clean_decimal(
        request.POST.get("kpi_percent")
    ) or Decimal("100"),

    hourly_rate_snapshot=Decimal("0"),

    tax_percent_snapshot=employee.tax_percent,

    status=request.POST.get("status"),

    comment=request.POST.get("comment", ""),
)

        if action == "create_penalty_type":

            EmployeePenaltyType.objects.create(
                country=country,
                name=request.POST.get("name", "").strip(),
                default_amount=clean_decimal(
                    request.POST.get("default_amount")
                ),
                is_active=True,
            )

        if action == "create_penalty":

            employee = Employee.objects.filter(
                id=request.POST.get("employee_id"),
                country=country
            ).first()

            penalty_type = None

            if request.POST.get("penalty_type_id"):
                penalty_type = EmployeePenaltyType.objects.filter(
                    id=request.POST.get("penalty_type_id"),
                    country=country
                ).first()

            if employee:

                amount = clean_decimal(
                    request.POST.get("amount")
                )

                if amount == 0 and penalty_type:
                    amount = penalty_type.default_amount

                EmployeePenalty.objects.create(
                    employee=employee,
                    penalty_type=penalty_type,
                    penalty_date=request.POST.get("penalty_date") or timezone.localdate(),
                    amount=amount,
                    reason=request.POST.get("reason", "").strip(),
                    comment=request.POST.get("comment", "").strip(),
                )

        if action == "create_payment":

            employee = Employee.objects.filter(
                id=request.POST.get("employee_id"),
                country=country
            ).first()

            if employee:

                EmployeePayment.objects.create(
                    employee=employee,
                    payment_date=request.POST.get("payment_date") or timezone.localdate(),
                    amount=clean_decimal(request.POST.get("amount")),
                    comment=request.POST.get("comment", "").strip(),
                )

        return redirect(f"/c/{country.slug}/employees/")

    employees = (
        Employee.objects
        .filter(country=country)
        .select_related("user", "location")
        .order_by("name")
    )

    locations = Location.objects.filter(
        country=country,
        is_active=True
    ).order_by("name")

    shifts = (
        EmployeeShift.objects
        .filter(country=country)
        .select_related("employee", "location")
        .order_by("-shift_date")[:50]
    )

    penalty_types = (
        EmployeePenaltyType.objects
        .filter(country=country)
        .order_by("name")
    )

    penalties = (
        EmployeePenalty.objects
        .filter(employee__country=country)
        .select_related("employee", "penalty_type")
        .order_by("-penalty_date")[:50]
    )

    payments = (
        EmployeePayment.objects
        .filter(employee__country=country)
        .select_related("employee")
        .order_by("-payment_date")[:50]
    )

    total_salary = Decimal("0")

    for shift in EmployeeShift.objects.filter(
        country=country,
        status=EmployeeShift.STATUS_DONE
    ):
        total_salary += shift.salary_before_penalties()

    total_penalties = (
        sum(item.amount for item in EmployeePenalty.objects.filter(employee__country=country))
        or Decimal("0")
    )

    total_payments = (
        sum(item.amount for item in EmployeePayment.objects.filter(employee__country=country))
        or Decimal("0")
    )

    total_debt = total_salary - total_penalties - total_payments

    total_taxes = Decimal("0")

    for shift in EmployeeShift.objects.filter(
        country=country,
        status=EmployeeShift.STATUS_DONE
    ):
        total_taxes += shift.tax_amount()

    return render(
        request,
        "foodcost/employee_list.html",
        {
            "country": country,
            "employees": employees,
            "locations": locations,

            "shifts": shifts,
            "penalty_types": penalty_types,
            "penalties": penalties,
            "payments": payments,

            "total_salary": total_salary,
            "total_penalties": total_penalties,
            "total_payments": total_payments,
            "total_debt": total_debt,
            "total_taxes": total_taxes,

            "shift_statuses": EmployeeShift.STATUS_CHOICES,
        }
    )