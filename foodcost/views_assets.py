# -*- coding: utf-8 -*-
"""
Кабинет клиента → «Активы» (основные средства).
Актив = товар, у категории которого тип учёта «основное средство».
Реестр показывает такие товары и их стоимость (последняя цена закупки).
Без отдельной модели и миграции — выборка по категории.
"""
from decimal import Decimal

from .models import UserProfile, Product
from .models_catalog import ProductCategory
from .views import get_country, require_section_access
from django.shortcuts import render


def assets(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_PRODUCTS)
    if access_error:
        return access_error

    items = (
        Product.objects
        .filter(country=country, category__accounting_type=ProductCategory.TYPE_ASSET)
        .select_related("category")
        .order_by("category__name", "name")
    )

    rows = []
    total = Decimal("0")
    for p in items:
        price = p.get_price()
        value = price.price if price else None
        if value:
            total += value
        rows.append({"product": p, "value": value, "category": p.category})

    return render(request, "foodcost/assets.html", {
        "country": country,
        "rows": rows,
        "total": total,
        "count": len(rows),
    })
