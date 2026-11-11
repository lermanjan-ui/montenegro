"""URL-ы приложения pos (касса).

Подключаются в config/urls.py строкой `path('', include('pos.urls'))`
ПЕРЕД include('foodcost.urls'). Пути в формате `c/<slug:country_slug>/...`.

Пункты «Настройки кассы» — подпункты левого меню (см. MENU_SNIPPET.html).
Готовые страницы отдают контент, ещё не сделанные — заглушку «скоро»
(coming_soon с заголовком), чтобы меню не вело в 404.
"""

from django.urls import path

from . import views
from . import cashier_views
from . import kds_views
from . import payment_views
from . import split_views

urlpatterns = [
    path("c/<slug:country_slug>/pos/", views.pos_root, name="pos_root"),

    # Касса (зал) — живой экран заказа
    path("c/<slug:country_slug>/pos/order/", cashier_views.cashier, name="pos_cashier"),
    path("c/<slug:country_slug>/pos/order/api/open/", cashier_views.api_open, name="pos_order_open"),
    path("c/<slug:country_slug>/pos/order/api/add/", cashier_views.api_add, name="pos_order_add"),
    path("c/<slug:country_slug>/pos/order/api/qty/", cashier_views.api_qty, name="pos_order_qty"),
    path("c/<slug:country_slug>/pos/order/api/remove/", cashier_views.api_remove, name="pos_order_remove"),
    path("c/<slug:country_slug>/pos/order/api/send/", cashier_views.api_send, name="pos_order_send"),
    # Оплата и закрытие счёта (B3)
    path("c/<slug:country_slug>/pos/order/api/quote/", payment_views.api_quote, name="pos_order_quote"),
    path("c/<slug:country_slug>/pos/order/api/loyalty/", payment_views.api_loyalty, name="pos_order_loyalty"),
    path("c/<slug:country_slug>/pos/order/api/pay/", payment_views.api_pay, name="pos_order_pay"),
    # Деление счёта (B3.1)
    path("c/<slug:country_slug>/pos/order/api/split/set/", split_views.api_split_set, name="pos_split_set"),
    path("c/<slug:country_slug>/pos/order/api/split/state/", split_views.api_split_state, name="pos_split_state"),
    path("c/<slug:country_slug>/pos/order/api/split/clear/", split_views.api_split_clear, name="pos_split_clear"),
    path("c/<slug:country_slug>/pos/order/api/split/pay/", split_views.api_split_pay, name="pos_split_pay"),

    # Кухня (KDS)
    path("c/<slug:country_slug>/pos/kds/", kds_views.kds, name="pos_kds"),
    path("c/<slug:country_slug>/pos/kds/api/board/", kds_views.api_board, name="pos_kds_board"),
    path("c/<slug:country_slug>/pos/kds/api/ready/", kds_views.api_ready, name="pos_kds_ready"),
    path("c/<slug:country_slug>/pos/kds/api/undo/", kds_views.api_undo, name="pos_kds_undo"),

    # Настройка кассы (подпункты меню)
    path("c/<slug:country_slug>/pos/kitchen/", views.kitchen_settings, name="pos_kitchen"),
    path("c/<slug:country_slug>/pos/service/", views.service_tips, name="pos_service"),
    path("c/<slug:country_slug>/pos/loyalty/", views.loyalty, name="pos_loyalty"),
    path("c/<slug:country_slug>/pos/payments/", views.payments, name="pos_payments"),
    path("c/<slug:country_slug>/pos/floor/", views.floor_plan, name="pos_floor"),
    path("c/<slug:country_slug>/pos/floor/api/move/", views.floor_table_move, name="pos_floor_move"),
    path("c/<slug:country_slug>/pos/access/", views.access_roles, name="pos_access"),
]
