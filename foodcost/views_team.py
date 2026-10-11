# -*- coding: utf-8 -*-
"""
Кабинет компании → «Сотрудники».

Админ клиента создаёт сотрудников и выдаёт им доступ к разделам —
НО только к тем, что доступны компании по тарифу (tenant_has_module).
Всё строго в рамках своего арендатора (Country): чужих пользователей и
чужие компании не видно и не затронуть.
"""
from django.contrib.auth.models import User
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404

from .models import Country, Location, UserProfile
from .module_access import tenant_has_module
from .views import get_country, require_section_access, user_can_edit


# Роли, которые админ компании может назначать сотрудникам.
# Платформенные роли (super_admin, platform_manager) здесь недоступны —
# их раздаёт только платформа.
_EMPLOYEE_ROLE_CODES = (
    UserProfile.ROLE_ADMIN,
    UserProfile.ROLE_VIEWER,
    UserProfile.ROLE_KITCHEN_STAFF,
)


def _available_sections(country):
    """Разделы, доступные компании по тарифу (пересечение со SECTION_CHOICES)."""
    return [
        (code, label)
        for code, label in UserProfile.SECTION_CHOICES
        if tenant_has_module(country, code)
    ]


def _grouped_sections(available):
    """Группировка доступных разделов для UI (как в сайдбаре)."""
    codes = {c for c, _ in available}
    groups = [
        ("Касса", [UserProfile.SECTION_ORDERS, UserProfile.SECTION_ALL_ORDERS]),
        ("Кухня", [UserProfile.SECTION_WRITE_OFFS, UserProfile.SECTION_SHIFT_HANDOVER,
                   UserProfile.SECTION_SHIFTS, UserProfile.SECTION_SCHEDULE]),
        ("CRM и аналитика", [UserProfile.SECTION_CUSTOMERS, UserProfile.SECTION_ORDER_ANALYTICS,
                             UserProfile.SECTION_WRITE_OFF_ANALYTICS,
                             UserProfile.SECTION_SHIFT_HANDOVER_ADMIN]),
        ("Меню", [UserProfile.SECTION_DISHES, UserProfile.SECTION_TECHCARDS,
                  UserProfile.SECTION_PRODUCTS, UserProfile.SECTION_PREPARATIONS,
                  UserProfile.SECTION_PACKAGING]),
        ("Склад", [UserProfile.SECTION_STOCK, UserProfile.SECTION_PURCHASES,
                   UserProfile.SECTION_TRANSFERS, UserProfile.SECTION_SUPPLIERS,
                   UserProfile.SECTION_INVENTORY]),
        ("Финансы", [UserProfile.SECTION_FINANCE, UserProfile.SECTION_UTILITIES]),
        ("Персонал", [UserProfile.SECTION_EMPLOYEES]),
        ("Админка", [UserProfile.SECTION_USERS, UserProfile.SECTION_SETTINGS,
                     UserProfile.SECTION_SITE]),
    ]
    label_by_code = dict(UserProfile.SECTION_CHOICES)
    out = []
    seen = set()
    for title, items in groups:
        present = [(c, label_by_code.get(c, c)) for c in items if c in codes]
        if present:
            out.append({"label": title, "items": present})
            seen.update(c for c, _ in present)
    rest = [(c, l) for c, l in available if c not in seen]
    if rest:
        out.append({"label": "Прочее", "items": rest})
    return out


def _is_company_member(profile, country):
    """Сотрудник этой компании (не платформенный аккаунт)."""
    if profile is None:
        return False
    if profile.role in (UserProfile.ROLE_SUPER_ADMIN, UserProfile.ROLE_PLATFORM_MANAGER):
        return False
    return profile.countries.filter(id=country.id).exists()


def company_team(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_USERS)
    if access_error:
        return access_error

    available = _available_sections(country)
    available_codes = {c for c, _ in available}
    error = None

    if request.method == "POST":
        if not user_can_edit(request.user):
            return HttpResponseForbidden("У вас нет прав на управление сотрудниками")

        action = request.POST.get("action")

        # выдаём только разделы, доступные компании по тарифу
        posted = request.POST.getlist("allowed_sections")
        sections = [c for c in posted if c in available_codes]

        role = request.POST.get("role")
        if role not in _EMPLOYEE_ROLE_CODES:
            role = UserProfile.ROLE_VIEWER

        location_id = request.POST.get("location_id") or None
        if location_id and not Location.objects.filter(
                id=location_id, country=country).exists():
            location_id = None

        if action == "create_employee":
            username = (request.POST.get("username") or "").strip()
            password = request.POST.get("password") or ""
            if not username or not password:
                error = "Укажи логин и пароль"
            elif User.objects.filter(username=username).exists():
                error = f"Пользователь «{username}» уже существует"
            else:
                u = User.objects.create_user(
                    username=username, password=password,
                    is_superuser=False, is_staff=False)
                prof = UserProfile.objects.create(
                    user=u, role=role, allowed_sections=sections,
                    location_id=location_id)
                prof.countries.set([country])
                return redirect(f"/c/{country.slug}/team/")

        elif action == "update_employee":
            u = get_object_or_404(User, id=request.POST.get("user_id"))
            prof, _ = UserProfile.objects.get_or_create(user=u)
            # защита: редактируем только сотрудника ЭТОЙ компании
            if not _is_company_member(prof, country):
                return HttpResponseForbidden("Нельзя редактировать этого пользователя")
            new_username = (request.POST.get("username") or "").strip()
            if new_username and new_username != u.username:
                if User.objects.filter(username=new_username).exclude(id=u.id).exists():
                    error = f"Логин «{new_username}» занят"
                else:
                    u.username = new_username
            password = request.POST.get("password") or ""
            if password:
                u.set_password(password)
            u.save()
            if not error:
                prof.role = role
                prof.allowed_sections = sections
                prof.location_id = location_id
                prof.save()
                prof.countries.add(country)
                return redirect(f"/c/{country.slug}/team/")

        elif action == "remove_employee":
            u = get_object_or_404(User, id=request.POST.get("user_id"))
            prof = getattr(u, "profile", None)
            if _is_company_member(prof, country) and u.id != request.user.id:
                prof.countries.remove(country)
                if not prof.countries.exists():
                    u.is_active = False
                    u.save(update_fields=["is_active"])
            return redirect(f"/c/{country.slug}/team/")

    # список сотрудников компании
    employees = (
        User.objects
        .filter(profile__countries=country)
        .exclude(is_superuser=True)
        .exclude(profile__role__in=[UserProfile.ROLE_SUPER_ADMIN,
                                    UserProfile.ROLE_PLATFORM_MANAGER])
        .select_related("profile")
        .distinct()
        .order_by("username")
    )

    employee_roles = [
        (c, l) for c, l in UserProfile.ROLE_CHOICES if c in _EMPLOYEE_ROLE_CODES
    ]

    return render(request, "foodcost/company_team.html", {
        "country": country,
        "employees": employees,
        "section_groups": _grouped_sections(available),
        "available_count": len(available),
        "total_sections": len(UserProfile.SECTION_CHOICES),
        "employee_roles": employee_roles,
        "locations": Location.objects.filter(country=country).order_by("name"),
        "can_edit": user_can_edit(request.user),
        "error": error,
    })
