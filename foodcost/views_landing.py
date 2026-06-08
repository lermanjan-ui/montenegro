"""Домашняя страница кабинета.

Раньше домашняя `/c/<slug>/` всегда вела на страницу «Блюда» (dish_list).
Если у пользователя нет доступа к разделу «Блюда», он упирался в «Нет доступа»
и не мог попасть никуда. Теперь:

  • есть доступ к «Блюдам» (или супер-админ) → как раньше, страница блюд;
  • иначе → перенаправляем на ПЕРВУЮ доступную ему страницу из меню.

Большой views.py не трогаем: импортируем оттуда готовые dish_list и get_country.
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect

from .models import UserProfile
from .views import dish_list, get_country


# Порядок «как в меню»: на какую страницу вести, если нет доступа к «Блюдам».
# Первая секция из этого списка, к которой есть доступ, и станет домашней.
_HOME_ORDER = [
    (UserProfile.SECTION_ORDERS, "orders/"),
    (UserProfile.SECTION_ALL_ORDERS, "orders/all/"),
    (UserProfile.SECTION_STOCK, "stock/"),
    (UserProfile.SECTION_INVENTORY, "inventory/"),
    (UserProfile.SECTION_PURCHASES, "purchases/"),
    (UserProfile.SECTION_TRANSFERS, "transfers/"),
    (UserProfile.SECTION_SUPPLIERS, "suppliers/"),
    (UserProfile.SECTION_WRITE_OFFS, "writeoffs/"),
    (UserProfile.SECTION_SHIFT_HANDOVER, "shift-handover/"),
    (UserProfile.SECTION_SCHEDULE, "schedule/"),
    (UserProfile.SECTION_SHIFTS, "shifts/"),
    (UserProfile.SECTION_EMPLOYEES, "employees/"),
    (UserProfile.SECTION_PRODUCTS, "products/"),
    (UserProfile.SECTION_PREPARATIONS, "preparations/"),
    (UserProfile.SECTION_PACKAGING, "packaging/"),
    (UserProfile.SECTION_UTILITIES, "utilities/"),
    (UserProfile.SECTION_FINANCE, "finance/expenses/"),
    (UserProfile.SECTION_CUSTOMERS, "customers/"),
    (UserProfile.SECTION_SETTINGS, "settings/"),
    (UserProfile.SECTION_USERS, "users/"),
]


@login_required(login_url="/login/")
def country_home(request, country_slug):
    # get_country сохраняет проверку доступа к стране (404, если страна не привязана).
    country = get_country(country_slug, request.user)
    user = request.user

    # Супер-пользователь и все, кому доступны «Блюда» — обычная страница блюд.
    if user.is_superuser:
        return dish_list(request, country_slug)

    profile = getattr(user, "profile", None)
    if profile is None:
        # Без профиля dish_list сам вернёт корректный «Нет доступа».
        return dish_list(request, country_slug)

    if profile.can_access_section(UserProfile.SECTION_DISHES):
        return dish_list(request, country_slug)

    # Нет доступа к «Блюдам» → первая доступная страница из меню.
    for section, suffix in _HOME_ORDER:
        if profile.can_access_section(section):
            return redirect(f"/c/{country.slug}/{suffix}")

    # Совсем ничего не доступно — стандартный экран «Нет доступа».
    return dish_list(request, country_slug)
