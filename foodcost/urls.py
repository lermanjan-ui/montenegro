from django.urls import path
from . import views
from . import writeoff_views
from . import shift_views


urlpatterns = [
    
    path("login/", views.login_page, name="login_page"),
    path("logout/", views.logout_page, name="logout_page"),
    
    # выбор страны
    path("", views.country_list, name="country_list"),

    # блюда
    path("c/<slug:country_slug>/", views.dish_list, name="dish_list"),
    path("c/<slug:country_slug>/dish/create/", views.dish_create, name="dish_create"),
    path("c/<slug:country_slug>/dish/<int:dish_id>/", views.dish_detail, name="dish_detail"),

    # продукты
    path("c/<slug:country_slug>/products/", views.product_list, name="product_list"),
    path("c/<slug:country_slug>/products/<int:product_id>/", views.product_detail, name="product_detail"),

    # заготовки
    path("c/<slug:country_slug>/preparations/", views.preparation_list, name="preparation_list"),
    path("c/<slug:country_slug>/preparations/<int:prep_id>/", views.preparation_detail, name="preparation_detail"),

    # сотрудники
    path("c/<slug:country_slug>/employees/", views.employee_list, name="employee_list"),

    # упаковка
    path("c/<slug:country_slug>/packaging/", views.packaging_list, name="packaging_list"),

    # коммуналка
    path("c/<slug:country_slug>/utilities/", views.utilities_list, name="utilities_list"),

    # списания

    path(

        "c/<slug:country_slug>/writeoffs/",

        writeoff_views.writeoff_list,

        name="writeoff_list"

    ),
    
    path(
        "c/<slug:country_slug>/writeoffs/analytics/",
        writeoff_views.writeoff_analytics,
        name="writeoff_analytics",
    ),
    
    # передача смены
    path(
        "c/<slug:country_slug>/shift-handover/",
        shift_views.shift_handover_list,
        name="shift_handover_list"
    ),
    
    path(
        "c/<slug:country_slug>/shift-handover/admin/",
        shift_views.shift_handover_admin,
        name="shift_handover_admin"
    ),

    # 👇 НОВОЕ — пользователи и доступы (ТОЛЬКО ДЛЯ ГЛАВНОГО АДМИНА)
    path("c/<slug:country_slug>/users/", views.user_access_list, name="user_access_list"),

    # live расчёт
    path("c/<slug:country_slug>/live-calculate/", views.live_calculate, name="live_calculate"),
]