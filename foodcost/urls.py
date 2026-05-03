from django.urls import path
from .views import (
    dish_list,
    dish_detail,
    live_calculate,
    product_list,
    product_detail,
    preparation_list,
    preparation_detail,
    employee_list,
    packaging_list,
    utilities_list,
)

urlpatterns = [
    path("", dish_list),
    path("dishes/<int:pk>/", dish_detail),
    path("live-calculate/", live_calculate),
    path("products/", product_list),
    path("products/<int:pk>/", product_detail),
    path("preparations/", preparation_list),
    path("preparations/<int:pk>/", preparation_detail),
    path("employees/", employee_list),
    path("packaging/", packaging_list),
    path("utilities/", utilities_list),
]