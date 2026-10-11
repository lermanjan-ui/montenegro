# -*- coding: utf-8 -*-
"""
Самостоятельная регистрация клиента (публичная, без авторизации).

Новый ресторан создаёт себя сам: компания (Country) + первый филиал + админ
компании + триал-подписка. Админ сразу логинится и попадает в свой кабинет.
"""
import datetime

from django.contrib.auth import login
from django.contrib.auth.models import User
from django.shortcuts import render, redirect
from django.utils import timezone
from django.utils.text import slugify

from .models import Country, Location, UserProfile
from .models_billing import Plan, TenantSubscription

TRIAL_DAYS = 7
DEFAULT_TRIAL_PLAN_CODE = "start"   # с каким тарифом стартует триал


def _unique_slug(base):
    base = slugify(base, allow_unicode=False) or "company"
    slug = base
    i = 2
    while Country.objects.filter(slug=slug).exists():
        slug = f"{base}-{i}"
        i += 1
    return slug


def signup(request):
    if request.user.is_authenticated:
        # уже вошедший — не регистрируем повторно
        return redirect("/")

    error = None
    form = {}

    if request.method == "POST":
        name = (request.POST.get("company_name") or "").strip()
        admin_login = (request.POST.get("admin_login") or "").strip()
        password = request.POST.get("password") or ""
        password2 = request.POST.get("password2") or ""
        phone = (request.POST.get("phone") or "").strip()
        email = (request.POST.get("email") or "").strip()
        form = {"company_name": name, "admin_login": admin_login,
                "phone": phone, "email": email}

        if not name:
            error = "Укажи название компании"
        elif not admin_login or not password:
            error = "Укажи логин и пароль администратора"
        elif len(password) < 6:
            error = "Пароль слишком короткий (минимум 6 символов)"
        elif password != password2:
            error = "Пароли не совпадают"
        elif User.objects.filter(username=admin_login).exists():
            error = f"Логин «{admin_login}» уже занят"
        else:
            slug = _unique_slug(name)
            country = Country.objects.create(name=name, slug=slug)
            Location.objects.create(country=country, name="Основной филиал")

            user = User.objects.create_user(
                username=admin_login, password=password,
                is_superuser=False, is_staff=False)
            prof = UserProfile.objects.create(
                user=user, role=UserProfile.ROLE_ADMIN,
                allowed_sections=[c for c, _ in UserProfile.SECTION_CHOICES])
            prof.countries.set([country])

            sub = TenantSubscription.objects.create(
                country=country,
                status=TenantSubscription.STATUS_TRIAL,
                trial_ends_at=timezone.now() + datetime.timedelta(days=TRIAL_DAYS),
            )
            plan = Plan.objects.filter(
                code=DEFAULT_TRIAL_PLAN_CODE, is_active=True).first()
            if plan:
                sub.plan = plan
                sub.save()

            # контакты — в реквизиты компании, если модуль подключён
            try:
                from .models_company import CompanyProfile
                CompanyProfile.objects.get_or_create(
                    country=country,
                    defaults={"phone": phone, "email": email,
                              "legal_name": name})
            except Exception:
                pass

            login(request, user)
            return redirect(f"/c/{country.slug}/")

    return render(request, "foodcost/signup.html", {
        "error": error,
        "form": form,
        "trial_days": TRIAL_DAYS,
    })
