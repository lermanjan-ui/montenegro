# -*- coding: utf-8 -*-
"""
Кабинет клиента → «Категории товаров».
Админ задаёт категории и их тип учёта. Товары потом привязываются к категории
на своей карточке. Всё в рамках своего арендатора.
"""
from django.db.models import Count
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404

from .models import UserProfile
from .models_catalog import ProductCategory
from .views import get_country, require_section_access, user_can_edit


def product_categories(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_PRODUCTS)
    if access_error:
        return access_error

    error = None
    if request.method == "POST":
        if not user_can_edit(request.user):
            return HttpResponseForbidden("У вас нет прав на изменение категорий")

        action = request.POST.get("action")
        valid_types = {c for c, _ in ProductCategory.TYPE_CHOICES}

        if action == "create":
            name = (request.POST.get("name") or "").strip()
            atype = request.POST.get("accounting_type")
            if not name:
                error = "Укажи название категории"
            elif atype not in valid_types:
                error = "Выбери тип учёта"
            else:
                ProductCategory.objects.create(
                    country=country, name=name, accounting_type=atype,
                    allocate_evenly=bool(request.POST.get("allocate_evenly")))
                return redirect(f"/c/{country.slug}/product-categories/")

        elif action == "update":
            cat = get_object_or_404(
                ProductCategory, id=request.POST.get("cat_id"), country=country)
            name = (request.POST.get("name") or "").strip()
            atype = request.POST.get("accounting_type")
            if name:
                cat.name = name
            if atype in valid_types:
                cat.accounting_type = atype
            cat.allocate_evenly = bool(request.POST.get("allocate_evenly"))
            cat.save()
            return redirect(f"/c/{country.slug}/product-categories/")

        elif action == "delete":
            cat = get_object_or_404(
                ProductCategory, id=request.POST.get("cat_id"), country=country)
            # товары останутся без категории (SET_NULL) — не удаляем их
            cat.delete()
            return redirect(f"/c/{country.slug}/product-categories/")

    categories = (
        ProductCategory.objects.filter(country=country)
        .annotate(product_count=Count("products"))
    )

    return render(request, "foodcost/product_categories.html", {
        "country": country,
        "categories": categories,
        "type_choices": ProductCategory.TYPE_CHOICES,
        "can_edit": user_can_edit(request.user),
        "error": error,
    })
