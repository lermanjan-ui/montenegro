# -*- coding: utf-8 -*-
"""
Кабинет клиента → «Моя подписка» (только чтение).
Клиент видит свой тариф, статус, срок, доступные модули и свои счета.
Оплату отмечает платформа (в консоли) — здесь клиент только наблюдает.
"""
from django.shortcuts import render

from .models import UserProfile
from .models_billing import TenantSubscription
from .module_access import tenant_allowed_modules
from .views import get_country, require_section_access


def my_subscription(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_SETTINGS)
    if access_error:
        return access_error

    try:
        sub = country.subscription
    except TenantSubscription.DoesNotExist:
        sub = None

    allowed = tenant_allowed_modules(country)  # None = без ограничений
    label_by_code = dict(UserProfile.SECTION_CHOICES)
    if allowed is None:
        modules = ["Все модули"]
    else:
        modules = sorted(label_by_code.get(c, c) for c in allowed)

    invoices = []
    try:
        from .models_invoices import SubscriptionInvoice
        invoices = list(SubscriptionInvoice.objects.filter(country=country)[:50])
    except Exception:
        invoices = []

    return render(request, "foodcost/subscription.html", {
        "country": country,
        "sub": sub,
        "modules": modules,
        "invoices": invoices,
    })
