# -*- coding: utf-8 -*-
"""
Публичный лендинг продукта (/welcome/).
Оффер + тарифы (из БД) + CTA на регистрацию. Без авторизации.
"""
from django.shortcuts import render

TRIAL_DAYS = 7


def landing(request):
    plans = []
    try:
        from .models_billing import Plan
        plans = [
            {
                "name": p.name,
                "price": p.monthly_price,
                "modules": p.modules.count(),
            }
            for p in Plan.objects.filter(is_active=True).order_by(
                "sort_order", "monthly_price")
        ]
    except Exception:
        plans = []

    return render(request, "foodcost/landing.html", {
        "plans": plans,
        "trial_days": TRIAL_DAYS,
    })
