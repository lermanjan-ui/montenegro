# -*- coding: utf-8 -*-
"""Активация языка интерфейса залогиненного пользователя (UserProfile.language).

Ставится в MIDDLEWARE ПОСЛЕ AuthenticationMiddleware (нужен request.user).
Переопределяет язык, выбранный LocaleMiddleware по браузеру/сессии, на
сохранённый у пользователя. Пустой language → не трогаем (работает дефолт).
"""
from django.utils import translation


class UserLanguageMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        lang = None
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            try:
                lang = (user.profile.language or "").strip() or None
            except Exception:
                lang = None
        if lang:
            translation.activate(lang)
            request.LANGUAGE_CODE = lang
        try:
            return self.get_response(request)
        finally:
            if lang:
                translation.deactivate()
