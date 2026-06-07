"""Меню → «Комбо на главной».

Отдельный модуль (как views_techcards / views_finance): большой views.py не
трогаем, чтобы не ловить поломки сборки. Владелец отмечает блюда — они попадают
в блок «Комбо с фудкорта» на главной сайта через флаг Dish.show_in_combo_block.
Механика — как у «Хитов» (is_featured), но управление вынесено на отдельную
страницу.

Переключение — по одному блюду (POST с dish_id), без массового сохранения,
чтобы поиск/фильтр не мог случайно сбросить отметки у не показанных блюд.
"""

from urllib.parse import urlencode

from django.shortcuts import render, redirect

from .models import Dish, UserProfile
from .views import get_country, require_section_access


def combo_picks(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_DISHES)
    if access_error:
        return access_error

    if request.method == "POST":
        dish = Dish.objects.filter(
            id=request.POST.get("dish_id"), country=country
        ).first()
        if dish is not None:
            dish.show_in_combo_block = not dish.show_in_combo_block
            dish.save(update_fields=["show_in_combo_block"])
        q = (request.POST.get("q") or "").strip()
        suffix = ("?" + urlencode({"q": q})) if q else ""
        return redirect(f"/c/{country.slug}/combo-picks/{suffix}")

    q = (request.GET.get("q") or "").strip()
    dishes = Dish.objects.filter(country=country, is_archived=False)
    if q:
        dishes = dishes.filter(name__icontains=q)
    dishes = dishes.order_by("-show_in_combo_block", "name")

    selected_count = Dish.objects.filter(
        country=country, is_archived=False, show_in_combo_block=True
    ).count()

    return render(request, "foodcost/combo_picks.html", {
        "country": country,
        "dishes": dishes,
        "q": q,
        "selected_count": selected_count,
    })
