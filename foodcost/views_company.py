# -*- coding: utf-8 -*-
"""
Кабинет клиента → «Реквизиты компании».
Заполняет админ компании (доступ по разделу «Настройки»). Сохраняет узбекские
реквизиты в CompanyProfile. Поля необязательные — можно заполнять постепенно.
"""
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect

from .models import UserProfile
from .models_company import CompanyProfile
from .views import get_country, require_section_access, user_can_edit


# Текстовые поля, которые просто переносятся из POST как есть.
_TEXT_FIELDS = [
    "legal_name", "short_name", "ownership_form",
    "inn", "vat_code", "oked", "okpo",
    "bank_account", "bank_name", "mfo",
    "legal_address", "actual_address",
    "director_name", "director_basis", "accountant_name",
    "phone", "email",
]


def company_profile(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_SETTINGS)
    if access_error:
        return access_error

    profile, _ = CompanyProfile.objects.get_or_create(country=country)
    saved = False

    if request.method == "POST":
        if not user_can_edit(request.user):
            return HttpResponseForbidden("У вас нет прав на изменение реквизитов")

        for field in _TEXT_FIELDS:
            setattr(profile, field, (request.POST.get(field) or "").strip())

        profile.vat_payer = bool(request.POST.get("vat_payer"))

        if request.POST.get("logo_clear") and profile.logo:
            profile.logo.delete(save=False)
            profile.logo = None
        if request.FILES.get("logo"):
            profile.logo = request.FILES["logo"]

        profile.save()
        saved = True

    return render(request, "foodcost/company_profile.html", {
        "country": country,
        "profile": profile,
        "saved": saved,
        "can_edit": user_can_edit(request.user),
    })
