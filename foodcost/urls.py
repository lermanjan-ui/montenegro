from django.urls import path
from . import views
from . import views_employees
from . import order_views
from . import views_settings
from . import writeoff_views
from . import shift_views
from . import public_api


urlpatterns = [
    
    path(
        "tilda/webhook/",
        views.tilda_webhook,
        name="tilda_webhook",
    ),
    
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
    path("c/<slug:country_slug>/employees/", views_employees.employee_list, name="employee_list"),

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
    
    path(
        "c/<slug:country_slug>/orders/",
        order_views.order_list,
        name="order_list"
    ),
    
    path(
        "c/<slug:country_slug>/orders/all/",
        order_views.order_all_list,
        name="order_all_list"
    ),
    
    path(
        "c/<slug:country_slug>/customers/",
        order_views.customer_list,
        name="customer_list"
    ),
    
    path(
        "c/<slug:country_slug>/orders/analytics/",
        order_views.order_analytics,
        name="order_analytics"
    ),
    
    path(
        "c/<slug:country_slug>/orders/create/",
        order_views.order_create,
        name="order_create"
    ),
    
    path(
        "c/<slug:country_slug>/orders/<int:order_id>/",
        order_views.order_detail,
        name="order_detail"
    ),
    
    path(
        "c/<slug:country_slug>/orders/customer-lookup/",
        order_views.customer_lookup,
        name="customer_lookup"
    ),
    
    path(
        "c/<slug:country_slug>/customers/<int:customer_id>/",
        order_views.customer_detail,
        name="customer_detail"
    ),
    
    path(
        "c/<slug:country_slug>/settings/",
        views_settings.settings_page,
        name="settings_page"
    ),
    
    

    # 👇 НОВОЕ — пользователи и доступы (ТОЛЬКО ДЛЯ ГЛАВНОГО АДМИНА)
    path("c/<slug:country_slug>/users/", views.user_access_list, name="user_access_list"),

    # live расчёт
    path("c/<slug:country_slug>/live-calculate/", views.live_calculate, name="live_calculate"),

    # =========================================================================
    # 🌐 PUBLIC API (Part 2) — read-only menu / catalog for the website
    # =========================================================================
    path(
        "api/public/locations",
        public_api.locations,
        name="public_locations",
    ),
    path(
        "api/public/categories",
        public_api.categories,
        name="public_categories",
    ),
    path(
        "api/public/products",
        public_api.products,
        name="public_products",
    ),
    path(
        "api/public/products/<slug:slug>",
        public_api.product_detail,
        name="public_product_detail",
    ),
    path(
        "api/public/search",
        public_api.search,
        name="public_search",
    ),

    # =========================================================================
    # 🛒 PUBLIC API (Part 4) — cart calculation + order create + tracking
    # =========================================================================
    path(
        "api/public/cart/calculate/",
        public_api.cart_calculate,
        name="public_cart_calculate",
    ),
    path(
        "api/public/orders/create/",
        public_api.order_create,
        name="public_order_create",
    ),
    path(
        "api/public/orders/<str:public_order_number>/",
        public_api.order_tracking,
        name="public_order_tracking",
    ),

    # =========================================================================
    # 🧾 PUBLIC API (Part 5) — checkout support: pickup points, promo, lookup
    # =========================================================================
    path(
        "api/public/pickup-points",
        public_api.pickup_points,
        name="public_pickup_points",
    ),
    path(
        "api/public/promo/check",
        public_api.promo_check,
        name="public_promo_check",
    ),
    path(
        "api/public/customers/lookup",
        public_api.customers_lookup,
        name="public_customers_lookup",
    ),

    # =========================================================================
    # 🗺  PUBLIC API (Part 8) — delivery zone check by coordinates
    # =========================================================================
    path(
        "api/public/delivery/check",
        public_api.delivery_check,
        name="public_delivery_check",
    ),
    path(
        "api/public/delivery/check/",
        public_api.delivery_check,
        name="public_delivery_check_slash",
    ),
]
