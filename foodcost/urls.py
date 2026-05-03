from django.urls import path
from . import views


urlpatterns = [
    # выбор страны
    path("", views.country_list, name="country_list"),

    # ВСЕ внутри страны
    path("c/<slug:country_slug>/", views.dish_list, name="dish_list"),

    path("c/<slug:country_slug>/products/", views.product_list, name="product_list"),
    path("c/<slug:country_slug>/products/<int:product_id>/", views.product_detail, name="product_detail"),

    path("c/<slug:country_slug>/preparations/", views.preparation_list, name="preparation_list"),
    path("c/<slug:country_slug>/preparations/<int:prep_id>/", views.preparation_detail, name="preparation_detail"),

    path("c/<slug:country_slug>/employees/", views.employee_list, name="employee_list"),
    path("c/<slug:country_slug>/packaging/", views.packaging_list, name="packaging_list"),
    path("c/<slug:country_slug>/utilities/", views.utilities_list, name="utilities_list"),

    path("c/<slug:country_slug>/dish/<int:dish_id>/", views.dish_detail, name="dish_detail"),
]