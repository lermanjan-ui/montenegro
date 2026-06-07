"""
Справочник техкарт для повара (только просмотр).

Вынесено в отдельный модуль, чтобы не трогать большой views.py.
Использует помощники из основного views.py (get_country,
user_can_view_dish_page) и модели.
"""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404, render

from .models import Dish, UserProfile
from .views import get_country, user_can_view_dish_page


def user_can_view_techcards(user):
    """Доступ к справочнику техкарт: общий доступ к странице блюда ИЛИ явно
    выданный раздел «Техкарты» в настройках пользователя."""
    if user_can_view_dish_page(user):
        return True
    profile = getattr(user, "profile", None)
    if profile is None:
        return False
    return profile.can_access_section(UserProfile.SECTION_TECHCARDS)


@login_required(login_url="/login/")
def techcards_list(request, country_slug):
    """Все блюда по разделам + поиск. Только просмотр."""
    country = get_country(country_slug, request.user)
    if not user_can_view_techcards(request.user):
        return HttpResponseForbidden("У вас нет доступа к этому разделу")

    query = (request.GET.get("q") or "").strip()
    dishes = Dish.objects.filter(country=country, is_archived=False, is_combo=False)
    if query:
        dishes = dishes.filter(name__icontains=query)
    dishes = dishes.select_related("category").order_by("category__name", "name")

    groups = {}
    for d in dishes:
        cat = d.category
        key = cat.id if cat else 0
        if key not in groups:
            groups[key] = {"category": cat,
                           "name": cat.name if cat else "Без раздела",
                           "dishes": []}
        groups[key]["dishes"].append(d)
    sections = sorted(groups.values(),
                      key=lambda g: (g["category"] is None, (g["name"] or "").lower()))

    return render(request, "foodcost/techcards_list.html", {
        "country": country,
        "sections": sections,
        "query": query,
    })


@login_required(login_url="/login/")
def techcard_view(request, country_slug, dish_id):
    """Просмотр техкарты блюда (только чтение, без печати/копирования)."""
    country = get_country(country_slug, request.user)
    if not user_can_view_techcards(request.user):
        return HttpResponseForbidden("У вас нет доступа к этому разделу")
    dish = get_object_or_404(Dish, id=dish_id, country=country)

    product_items = [
        {"name": it.product.name, "gross": it.gross, "net": it.net, "unit": it.unit_label()}
        for it in dish.product_items.select_related("product").all()
    ]
    preparation_items = [
        {"name": it.preparation.name, "gross": it.gross, "net": it.net, "unit": it.unit_label()}
        for it in dish.preparation_items.select_related("preparation").all()
    ]
    steps = list(dish.steps.all())

    return render(request, "foodcost/techcard_view.html", {
        "country": country,
        "dish": dish,
        "product_items": product_items,
        "preparation_items": preparation_items,
        "steps": steps,
    })
