# -*- coding: utf-8 -*-
"""
Личный кабинет пользователя (/c/<slug>/account/).
Профиль (логин, роль, филиал, доступы) + задать логин и пароль.
Аккаунты «только по коду» (без пароля) могут задать пароль без ввода старого.
"""
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.models import User
from django.shortcuts import render

from .models import UserProfile
from .views import get_country


def my_account(request, country_slug):
    country = get_country(country_slug, request.user)
    profile = getattr(request.user, "profile", None)

    error = None
    saved = None
    has_password = request.user.has_usable_password()

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "set_login":
            new_login = (request.POST.get("login") or "").strip()
            if not new_login:
                error = "Укажи логин"
            elif User.objects.filter(
                    username=new_login).exclude(id=request.user.id).exists():
                error = "Этот логин уже занят"
            else:
                request.user.username = new_login
                request.user.save(update_fields=["username"])
                saved = "login"

        elif action == "change_password":
            old = request.POST.get("old_password") or ""
            new1 = request.POST.get("new_password") or ""
            new2 = request.POST.get("new_password2") or ""
            if has_password and not request.user.check_password(old):
                error = "Текущий пароль неверный"
            elif len(new1) < 6:
                error = "Новый пароль слишком короткий (минимум 6 символов)"
            elif new1 != new2:
                error = "Новый пароль и подтверждение не совпадают"
            else:
                request.user.set_password(new1)
                request.user.save()
                update_session_auth_hash(request, request.user)
                has_password = True
                saved = "password"

    label_by_code = dict(UserProfile.SECTION_CHOICES)
    if request.user.is_superuser:
        role_label = "Главный админ"
        sections = ["Все разделы"]
    elif profile is not None:
        role_label = profile.get_role_display()
        sections = [label_by_code.get(c, c) for c in (profile.allowed_sections or [])]
    else:
        role_label = "Пользователь"
        sections = []

    branch = profile.location.name if (profile and profile.location_id) else None

    return render(request, "foodcost/my_account.html", {
        "country": country,
        "profile": profile,
        "role_label": role_label,
        "sections": sections,
        "branch": branch,
        "saved": saved,
        "error": error,
        "has_password": has_password,
    })
