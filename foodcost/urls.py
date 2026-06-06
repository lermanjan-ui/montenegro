from django.urls import path
from . import views
from . import views_employees
from . import order_views
from . import views_settings
from . import promotions_views
from . import views_homepage
from . import writeoff_views
from . import shift_views
from . import views_stock
from . import views_suppliers
from . import views_purchases
from . import views_transfers
from . import views_inventory
from . import views_autowriteoff
from . import public_api
from . import app_auth
from . import app_account
from . import app_push
# TEMPORARY — one-off Tilda CSV import (delete this import + the route below
# once the historical data is loaded). See foodcost/views_tilda_import.py.
from . import views_tilda_import
# TEMPORARY — one-off merge of duplicate "Payme" PaymentMethod rows (delete
# this import + the route below once merged). See foodcost/views_payme_merge.py.
from . import views_payme_merge


urlpatterns = [
    
    # TEMPORARY — Tilda CSV import (superuser-only). After the import
    # is done, REMOVE this route and the views_tilda_import module so
    # the upload endpoint is no longer reachable.
    path(
        "_admin/tilda-import/",
        views_tilda_import.tilda_import_page,
        name="tilda_import_page",
    ),

    # TEMPORARY — merge duplicate "Payme" payment methods (superuser-only).
    # REMOVE this route + views_payme_merge module after the merge is done.
    path(
        "_admin/payme-merge/",
        views_payme_merge.payme_merge_page,
        name="payme_merge_page",
    ),

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
    # Soft-archive a dish: hides it from menu / cart / new orders, keeps it
    # visible in OLD orders. POST-only. Operator must have can_edit.
    path(
        "c/<slug:country_slug>/dish/<int:dish_id>/archive/",
        views.dish_archive,
        name="dish_archive",
    ),
    path(
        "c/<slug:country_slug>/dish/<int:dish_id>/unarchive/",
        views.dish_unarchive,
        name="dish_unarchive",
    ),
    # Переключение видимости блюда на сайте (is_visible_on_site) прямо из
    # списка блюд, без перехода в карточку. POST-only, отвечает JSON.
    path(
        "c/<slug:country_slug>/dish/<int:dish_id>/toggle-visibility/",
        views.dish_toggle_visibility,
        name="dish_toggle_visibility",
    ),
    # Печатная техкарта блюда для повара (состав с граммовками + текст).
    # HTML-страница с window.print() — «Сохранить как PDF» из браузера.
    path(
        "c/<slug:country_slug>/dish/<int:dish_id>/techcard/",
        views.dish_techcard_print,
        name="dish_techcard_print",
    ),

    # продукты
    path("c/<slug:country_slug>/products/", views.product_list, name="product_list"),
    path("c/<slug:country_slug>/products/<int:product_id>/", views.product_detail, name="product_detail"),

    # заготовки
    path("c/<slug:country_slug>/preparations/", views.preparation_list, name="preparation_list"),
    path("c/<slug:country_slug>/preparations/<int:prep_id>/", views.preparation_detail, name="preparation_detail"),

    # сотрудники
    path("c/<slug:country_slug>/employees/", views_employees.employee_list, name="employee_list"),
    path("c/<slug:country_slug>/employees/<int:employee_id>/", views_employees.employee_detail, name="employee_detail"),
    path("c/<slug:country_slug>/schedule/", views_employees.schedule_page, name="schedule_page"),
    path("c/<slug:country_slug>/shifts/", views_employees.shifts_journal, name="shifts_journal"),
    path("c/<slug:country_slug>/employee/me/", views_employees.employee_me, name="employee_me"),

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

    # Лёгкий JSON для звукового уведомления о новых заказах (поллинг со
    # страницы /orders/). Отдаёт {count, latest_id} за сегодня.
    path(
        "c/<slug:country_slug>/orders/count/",
        order_views.orders_count,
        name="orders_count"
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

    # Категории блюд — отдельная страница (раньше блок в настройках).
    path(
        "c/<slug:country_slug>/dish-categories/",
        views_settings.dish_categories_page,
        name="dish_categories_page"
    ),

    # Маркетинговые акции — отдельная страница управления.
    path(
        "c/<slug:country_slug>/promotions/",
        promotions_views.promotions_page,
        name="promotions_page"
    ),

    path(
        "c/<slug:country_slug>/settings/homepage/",
        views_homepage.homepage_settings_page,
        name="homepage_settings",
    ),
    
    

    # =========================================================================
    # 📦 СКЛАД — Остатки (только чтение)
    # =========================================================================
    path(
        "c/<slug:country_slug>/stock/",
        views_stock.stock_list,
        name="stock_list",
    ),
    path(
        "c/<slug:country_slug>/stock/movements/",
        views_stock.stock_movements,
        name="stock_movements",
    ),
    path(
        "c/<slug:country_slug>/stock/product/<int:product_id>/",
        views_stock.stock_product_detail,
        name="stock_product_detail",
    ),
    path(
        "c/<slug:country_slug>/stock/auto-writeoff/",
        views_autowriteoff.auto_writeoff_page,
        name="auto_writeoff_page",
    ),

    # =========================================================================
    # 🏭 СКЛАД — Поставщики
    # =========================================================================
    path(
        "c/<slug:country_slug>/suppliers/",
        views_suppliers.supplier_list,
        name="supplier_list",
    ),
    path(
        "c/<slug:country_slug>/suppliers/<int:supplier_id>/",
        views_suppliers.supplier_detail,
        name="supplier_detail",
    ),

    # =========================================================================
    # 📥 СКЛАД — Приходы
    # =========================================================================
    path(
        "c/<slug:country_slug>/purchases/",
        views_purchases.purchase_list,
        name="purchase_list",
    ),
    path(
        "c/<slug:country_slug>/purchases/<int:receipt_id>/",
        views_purchases.purchase_detail,
        name="purchase_detail",
    ),

    # =========================================================================
    # 🔄 СКЛАД — Перемещения
    # =========================================================================
    path(
        "c/<slug:country_slug>/transfers/",
        views_transfers.transfer_list,
        name="transfer_list",
    ),
    path(
        "c/<slug:country_slug>/transfers/<int:transfer_id>/",
        views_transfers.transfer_detail,
        name="transfer_detail",
    ),

    # =========================================================================
    # 📋 СКЛАД — Инвентаризация
    # =========================================================================
    path(
        "c/<slug:country_slug>/inventory/",
        views_inventory.inventory_list,
        name="inventory_list",
    ),
    path(
        "c/<slug:country_slug>/inventory/<int:inventory_id>/",
        views_inventory.inventory_detail,
        name="inventory_detail",
    ),
    path(
        "c/<slug:country_slug>/inventory/<int:inventory_id>/count/",
        views_inventory.inventory_count,
        name="inventory_count",
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

    # Click payment retry — generates a fresh payment_url for an unpaid order.
    # Trailing slash is required by csrf-exempt + require_POST in Django's
    # default APPEND_SLASH config; we keep it consistent with /orders/create/.
    path(
        "api/public/orders/<str:public_order_number>/pay/",
        public_api.order_pay_retry,
        name="public_order_pay_retry",
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
    # 🔐 APP AUTH — вход по SMS (OTP) + токены для мобильного приложения
    # =========================================================================
    path("api/app/auth/request-code", app_auth.auth_request_code, name="app_auth_request_code"),
    path("api/app/auth/request-code/", app_auth.auth_request_code, name="app_auth_request_code_s"),
    path("api/app/auth/verify-code", app_auth.auth_verify_code, name="app_auth_verify_code"),
    path("api/app/auth/verify-code/", app_auth.auth_verify_code, name="app_auth_verify_code_s"),
    path("api/app/auth/refresh", app_auth.auth_refresh, name="app_auth_refresh"),
    path("api/app/auth/refresh/", app_auth.auth_refresh, name="app_auth_refresh_s"),
    path("api/app/auth/logout", app_auth.auth_logout, name="app_auth_logout"),
    path("api/app/auth/logout/", app_auth.auth_logout, name="app_auth_logout_s"),

    # =========================================================================
    # 👤 APP ACCOUNT — профиль, история заказов, адреса (нужен Bearer-токен)
    # =========================================================================
    path("api/app/profile", app_account.profile, name="app_profile"),
    path("api/app/profile/", app_account.profile, name="app_profile_s"),
    path("api/app/orders", app_account.orders_list, name="app_orders"),
    path("api/app/orders/", app_account.orders_list, name="app_orders_s"),
    path("api/app/orders/<str:public_order_number>", app_account.order_detail, name="app_order_detail"),
    path("api/app/orders/<str:public_order_number>/", app_account.order_detail, name="app_order_detail_s"),
    path("api/app/addresses", app_account.addresses, name="app_addresses"),
    path("api/app/addresses/", app_account.addresses, name="app_addresses_s"),
    path("api/app/addresses/<int:address_id>", app_account.address_detail, name="app_address_detail"),
    path("api/app/addresses/<int:address_id>/", app_account.address_detail, name="app_address_detail_s"),

    # =========================================================================
    # 🔔 APP PUSH — регистрация токена устройства (FCM), нужен Bearer-токен
    # =========================================================================
    path("api/app/push/register", app_push.push_register, name="app_push_register"),
    path("api/app/push/register/", app_push.push_register, name="app_push_register_s"),
    path("api/app/push/unregister", app_push.push_unregister, name="app_push_unregister"),
    path("api/app/push/unregister/", app_push.push_unregister, name="app_push_unregister_s"),

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

    # =========================================================================
    # 🏠 PUBLIC API (Part 11) — homepage CMS: banners, bestsellers, blocks
    # =========================================================================
    path(
        "api/public/home/banners",
        public_api.home_banners,
        name="public_home_banners",
    ),
    path(
        "api/public/home/bestsellers",
        public_api.home_bestsellers,
        name="public_home_bestsellers",
    ),
    path(
        "api/public/home/frequently-bought",
        public_api.home_frequently_bought,
        name="public_home_frequently_bought",
    ),
    path(
        "api/public/home/compact-upsell",
        public_api.home_compact_upsell,
        name="public_home_compact_upsell",
    ),

    # Two-banner CTA strip in the homepage "Комбо и акции" section.
    # Separate from /home/banners (hero) — narrower, paired, promo-code-aware.
    # Trailing slash variant included because Django's APPEND_SLASH redirects
    # POST callers; better to register both up front.
    path(
        "api/public/home/combo-banners",
        public_api.home_combo_banners,
        name="public_home_combo_banners",
    ),
    path(
        "api/public/home/combo-banners/",
        public_api.home_combo_banners,
        name="public_home_combo_banners_slash",
    ),

    # Cart-page upsell strip ("Добавить к заказу"). Reuses the
    # HomepageCompactUpsellBlock model with placement="cart"; the cabinet
    # has separate sections for home / cart placements.
    path(
        "api/public/cart/upsell",
        public_api.cart_upsell,
        name="public_cart_upsell",
    ),
    path(
        "api/public/cart/upsell/",
        public_api.cart_upsell,
        name="public_cart_upsell_slash",
    ),

    # =========================================================================
    # 💳 PAYMENTS (Part 2) — Click callback
    # =========================================================================
    # Click pings ONE URL twice per order: action=0 (Prepare) and action=1
    # (Complete). Signature is verified server-side; secret_key never leaves
    # the backend. Configure this URL in the Click merchant cabinet:
    #
    #   https://<your-backend-host>/api/payments/click/callback/
    #
    path(
        "api/payments/click/callback/",
        public_api.click_callback,
        name="payments_click_callback",
    ),
    path(
        "api/payments/click/callback",
        public_api.click_callback,
        name="payments_click_callback_noslash",
    ),

    # =========================================================================
    # 💳 PAYMENTS (Part 3) — Payme (Paycom) JSON-RPC callback
    # =========================================================================
    # ONE endpoint receives all 6 Merchant API methods (CheckPerformTransaction,
    # CreateTransaction, PerformTransaction, CancelTransaction, CheckTransaction,
    # GetStatement). Auth is HTTP Basic ("Paycom:<SECRET_KEY>") — verified
    # server-side. Configure this URL in the Payme merchant cabinet:
    #
    #   https://<your-backend-host>/api/payments/payme/callback/
    #
    path(
        "api/payments/payme/callback/",
        public_api.payme_callback,
        name="payments_payme_callback",
    ),
    path(
        "api/payments/payme/callback",
        public_api.payme_callback,
        name="payments_payme_callback_noslash",
    ),
]
