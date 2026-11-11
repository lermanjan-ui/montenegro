"""Тесты приложения pos.

Проверяют, что новые таблицы создаются, FK на foodcost резолвятся, значения
по умолчанию и связи работают. Никакой логики кассы здесь ещё нет — это
фундамент (модели настроек).

Запуск:  python manage.py test pos
"""

import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from django.utils import timezone

from foodcost.models import (
    Tenant, Location, DishCategory, Dish, Customer, PaymentMethod,
    Employee, Order, OrderItem, UserProfile,
)
from pos.models import (
    KitchenScreen, KitchenTimeThreshold, ServiceTipSettings,
    LoyaltyProgram, LoyaltyTier, CustomerPoints,
    FloorZone, FloorTable, TableBooking,
    FiscalizationSettings, PaymentMethodLocationConfig,
    PosOrder, PosOrderItem, PosPayment, PosBill,
)


class PosFoundationBase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Нигора Фуд", slug="nigora")
        cls.location = Location.objects.create(country=cls.tenant, name="Амир Темур 12")
        cls.category = DishCategory.objects.create(country=cls.tenant, name="Горячее")
        cls.dish = Dish.objects.create(
            country=cls.tenant, name="Плов",
            final_weight=Decimal("0.300"), selling_price=Decimal("42000"),
        )
        cls.customer = Customer.objects.create(
            country=cls.tenant, phone="+998901234567", name="Нигора",
        )
        cls.payment = PaymentMethod.objects.create(country=cls.tenant, name="Наличные", is_cash=True)


class KitchenTests(PosFoundationBase):
    def test_screen_with_categories_and_threshold(self):
        screen = KitchenScreen.objects.create(
            tenant=self.tenant, location=self.location, name="Горячий цех",
            pairing_code="4821",
        )
        screen.categories.add(self.category)
        screen.dishes.add(self.dish)

        self.assertEqual(screen.categories.count(), 1)
        self.assertEqual(screen.dishes.count(), 1)
        self.assertFalse(screen.is_connected)
        self.assertEqual(str(screen), "Горячий цех")

        thr = KitchenTimeThreshold.objects.create(
            tenant=self.tenant, screen=screen, category=self.category,
            warn_minutes=12, late_minutes=22,
        )
        self.assertEqual(thr.warn_minutes, 12)
        self.assertIn("12/22", str(thr))

    def test_global_threshold_defaults(self):
        thr = KitchenTimeThreshold.objects.create(tenant=self.tenant)
        self.assertEqual(thr.warn_minutes, 8)
        self.assertEqual(thr.late_minutes, 15)
        self.assertIsNone(thr.screen_id)


class ServiceTipTests(PosFoundationBase):
    def test_defaults_and_presets(self):
        s = ServiceTipSettings.objects.create(
            tenant=self.tenant, location=self.location,
            service_enabled=True, service_percent=Decimal("10"),
            tips_enabled=True, tips_presets=[5, 10, 15],
        )
        self.assertTrue(s.service_dine_in)
        self.assertFalse(s.service_delivery)
        self.assertEqual(s.tips_presets, [5, 10, 15])
        self.assertEqual(s.tips_recipient, ServiceTipSettings.TIPS_RECIPIENT_WAITER)

    def test_all_locations_scope(self):
        s = ServiceTipSettings.objects.create(tenant=self.tenant)
        self.assertIn("все точки", str(s))


class LoyaltyTests(PosFoundationBase):
    def test_program_tier_and_points(self):
        program = LoyaltyProgram.objects.create(
            tenant=self.tenant, enabled=True, earn_percent=Decimal("5"),
            tiers_enabled=True,
        )
        self.assertEqual(program.redeem_max_percent, Decimal("50"))
        self.assertTrue(program.require_sms_on_redeem)

        LoyaltyTier.objects.create(
            program=program, name="Серебро",
            min_turnover=Decimal("1000000"), earn_percent=Decimal("5"), sort_order=1,
        )
        self.assertEqual(program.tiers.count(), 1)

        points = CustomerPoints.objects.create(customer=self.customer, balance=Decimal("12400"))
        self.assertEqual(self.customer.loyalty_points.balance, Decimal("12400"))
        self.assertIn("12400", str(points))

    def test_one_program_per_tenant(self):
        LoyaltyProgram.objects.create(tenant=self.tenant)
        from django.db import IntegrityError, transaction
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                LoyaltyProgram.objects.create(tenant=self.tenant)


class FloorTests(PosFoundationBase):
    def test_zone_table_booking(self):
        zone = FloorZone.objects.create(
            tenant=self.tenant, location=self.location, name="Основной зал",
        )
        table = FloorTable.objects.create(
            zone=zone, number="5", seats=4, shape=FloorTable.SHAPE_SQUARE,
            pos_x=Decimal("70"), pos_y=Decimal("26"),
        )
        self.assertTrue(table.is_mergeable)
        self.assertEqual(str(table), "Стол 5")

        booking = TableBooking.objects.create(
            table=table, guest_name="Дильноза", phone="+998905551234",
            booking_date=datetime.date(2026, 10, 24),
            booking_time=datetime.time(19, 30), guests=4,
        )
        self.assertEqual(booking.status, TableBooking.STATUS_ACTIVE)
        self.assertEqual(booking.duration_minutes, 120)
        self.assertEqual(table.bookings.count(), 1)


class PaymentFiscalTests(PosFoundationBase):
    def test_fiscalization_and_payment_config(self):
        fiscal = FiscalizationSettings.objects.create(
            tenant=self.tenant, location=self.location,
        )
        self.assertEqual(fiscal.mode, FiscalizationSettings.MODE_TEST)
        self.assertEqual(fiscal.vat_mode, FiscalizationSettings.VAT_INCLUDED_12)

        cfg = PaymentMethodLocationConfig.objects.create(
            payment_method=self.payment, location=self.location,
            fiscalization=PaymentMethodLocationConfig.FISCAL_CASHBOX,
        )
        self.assertTrue(cfg.is_visible)
        self.assertEqual(self.payment.location_configs.count(), 1)


class KitchenViewTests(PosFoundationBase):
    """Страницы «Настройка кассы» (хаб + экраны/пороги кухни)."""

    def setUp(self):
        self.admin = User.objects.create_superuser("mgr", "m@x.co", "x")
        self.client.force_login(self.admin)

    def test_root_redirects_to_kitchen(self):
        r = self.client.get(f"/c/{self.tenant.slug}/pos/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/pos/kitchen/", r["Location"])

    def test_kitchen_opens(self):
        r = self.client.get(f"/c/{self.tenant.slug}/pos/kitchen/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Экраны кухни")

    def test_add_screen(self):
        r = self.client.post(f"/c/{self.tenant.slug}/pos/kitchen/", {
            "action": "add_screen", "name": "Горячий цех",
            "location": str(self.location.id), "categories": [str(self.category.id)],
        })
        self.assertEqual(r.status_code, 302)
        s = KitchenScreen.objects.get(tenant=self.tenant, name="Горячий цех")
        self.assertEqual(s.categories.count(), 1)
        self.assertTrue(s.pairing_code)

    def test_delete_screen(self):
        s = KitchenScreen.objects.create(tenant=self.tenant, name="Бар")
        self.client.post(f"/c/{self.tenant.slug}/pos/kitchen/", {
            "action": "delete_screen", "screen_id": str(s.id),
        })
        self.assertFalse(KitchenScreen.objects.filter(id=s.id).exists())

    def test_add_and_edit_threshold(self):
        self.client.post(f"/c/{self.tenant.slug}/pos/kitchen/", {
            "action": "add_threshold", "category": str(self.category.id),
            "warn": "12", "late": "22",
        })
        t = KitchenTimeThreshold.objects.get(tenant=self.tenant, category=self.category)
        self.assertEqual(t.warn_minutes, 12)
        self.client.post(f"/c/{self.tenant.slug}/pos/kitchen/", {
            "action": "save_threshold", "threshold_id": str(t.id),
            "warn": "5", "late": "9",
        })
        t.refresh_from_db()
        self.assertEqual(t.warn_minutes, 5)

    def test_access_denied_without_section(self):
        from foodcost.models import UserProfile
        weak = User.objects.create_user("weak", password="x")
        prof = UserProfile.objects.create(
            user=weak, role=UserProfile.ROLE_VIEWER, allowed_sections=[],
        )
        prof.countries.add(self.tenant)
        self.client.force_login(weak)
        r = self.client.get(f"/c/{self.tenant.slug}/pos/kitchen/")
        self.assertEqual(r.status_code, 403)


class ServiceTipsViewTests(PosFoundationBase):
    """Страница «Обслуживание и чаевые»."""

    def setUp(self):
        self.admin = User.objects.create_superuser("mgr2", "m2@x.co", "x")
        self.client.force_login(self.admin)

    def test_page_opens(self):
        r = self.client.get(f"/c/{self.tenant.slug}/pos/service/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Сервисный сбор")

    def test_save(self):
        r = self.client.post(f"/c/{self.tenant.slug}/pos/service/", {
            "service_enabled": "on", "service_percent": "10",
            "service_dine_in": "on",
            "tips_enabled": "on", "tips_presets": "5, 10, 15",
            "tips_allow_custom": "on",
            "tips_recipient": ServiceTipSettings.TIPS_RECIPIENT_POOL,
        })
        self.assertEqual(r.status_code, 302)
        cfg = ServiceTipSettings.objects.get(tenant=self.tenant, location=None)
        self.assertTrue(cfg.service_enabled)
        self.assertEqual(cfg.service_percent, Decimal("10"))
        self.assertTrue(cfg.service_dine_in)
        self.assertFalse(cfg.service_delivery)
        self.assertEqual(cfg.tips_presets, [5, 10, 15])
        self.assertEqual(cfg.tips_recipient, ServiceTipSettings.TIPS_RECIPIENT_POOL)


class LoyaltyViewTests(PosFoundationBase):
    """Страница «Лояльность и баллы»."""

    def setUp(self):
        self.admin = User.objects.create_superuser("mgr3", "m3@x.co", "x")
        self.client.force_login(self.admin)

    def test_page_opens(self):
        r = self.client.get(f"/c/{self.tenant.slug}/pos/loyalty/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Начисление баллов")

    def test_save_program(self):
        r = self.client.post(f"/c/{self.tenant.slug}/pos/loyalty/", {
            "action": "save_program", "enabled": "on",
            "identify_by_phone": "on", "require_sms_on_redeem": "on",
            "earn_percent": "5", "redeem_max_percent": "50",
            "points_expiry_months": "12", "tiers_enabled": "on",
        })
        self.assertEqual(r.status_code, 302)
        p = LoyaltyProgram.objects.get(tenant=self.tenant)
        self.assertTrue(p.enabled)
        self.assertEqual(p.earn_percent, Decimal("5"))
        self.assertEqual(p.points_expiry_months, 12)
        self.assertTrue(p.tiers_enabled)
        self.assertFalse(p.identify_by_qr)

    def test_tier_crud(self):
        self.client.post(f"/c/{self.tenant.slug}/pos/loyalty/", {
            "action": "add_tier", "name": "Серебро",
            "min_turnover": "1000000", "tier_earn": "5",
        })
        p = LoyaltyProgram.objects.get(tenant=self.tenant)
        tier = p.tiers.get(name="Серебро")
        self.assertEqual(tier.earn_percent, Decimal("5"))
        self.client.post(f"/c/{self.tenant.slug}/pos/loyalty/", {
            "action": "save_tier", "tier_id": str(tier.id),
            "name": "Золото", "min_turnover": "5000000", "tier_earn": "7",
        })
        tier.refresh_from_db()
        self.assertEqual(tier.name, "Золото")
        self.assertEqual(tier.earn_percent, Decimal("7"))
        self.client.post(f"/c/{self.tenant.slug}/pos/loyalty/", {
            "action": "delete_tier", "tier_id": str(tier.id),
        })
        self.assertFalse(LoyaltyTier.objects.filter(id=tier.id).exists())


class PaymentsViewTests(PosFoundationBase):
    """Страница «Оплаты и фискализация»."""

    def setUp(self):
        self.admin = User.objects.create_superuser("mgr4", "m4@x.co", "x")
        self.client.force_login(self.admin)

    def test_page_opens(self):
        r = self.client.get(f"/c/{self.tenant.slug}/pos/payments/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Фискализация")
        self.assertContains(r, self.payment.name)

    def test_save_fiscal(self):
        r = self.client.post(f"/c/{self.tenant.slug}/pos/payments/", {
            "action": "save_fiscal",
            "mode": FiscalizationSettings.MODE_LIVE,
            "vat_mode": FiscalizationSettings.VAT_ZERO,
        })
        self.assertEqual(r.status_code, 302)
        f = FiscalizationSettings.objects.get(tenant=self.tenant, location=None)
        self.assertEqual(f.mode, FiscalizationSettings.MODE_LIVE)
        self.assertEqual(f.vat_mode, FiscalizationSettings.VAT_ZERO)

    def test_save_methods(self):
        self.client.get(f"/c/{self.tenant.slug}/pos/payments/")  # создаёт конфиг
        r = self.client.post(f"/c/{self.tenant.slug}/pos/payments/", {
            "action": "save_methods",
            f"visible_{self.payment.id}": "on",
            f"fiscal_{self.payment.id}": PaymentMethodLocationConfig.FISCAL_PROVIDER,
        })
        self.assertEqual(r.status_code, 302)
        cfg = PaymentMethodLocationConfig.objects.get(payment_method=self.payment, location=None)
        self.assertTrue(cfg.is_visible)
        self.assertEqual(cfg.fiscalization, PaymentMethodLocationConfig.FISCAL_PROVIDER)


class FloorViewTests(PosFoundationBase):
    """Страница «Расстановка столов»."""

    def setUp(self):
        self.admin = User.objects.create_superuser("mgr5", "m5@x.co", "x")
        self.client.force_login(self.admin)

    def test_page_opens(self):
        r = self.client.get(f"/c/{self.tenant.slug}/pos/floor/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Расстановка столов")

    def test_zone_and_table_crud(self):
        self.client.post(f"/c/{self.tenant.slug}/pos/floor/", {
            "action": "add_zone", "name": "Основной зал",
            "location": str(self.location.id),
        })
        zone = FloorZone.objects.get(tenant=self.tenant, name="Основной зал")
        self.assertEqual(zone.location_id, self.location.id)

        self.client.post(f"/c/{self.tenant.slug}/pos/floor/", {
            "action": "add_table", "zone_id": str(zone.id),
            "number": "5", "seats": "4", "shape": FloorTable.SHAPE_SQUARE,
        })
        table = FloorTable.objects.get(zone=zone, number="5")
        self.assertEqual(table.seats, 4)

        self.client.post(f"/c/{self.tenant.slug}/pos/floor/", {
            "action": "delete_table", "table_id": str(table.id),
        })
        self.assertFalse(FloorTable.objects.filter(id=table.id).exists())

        self.client.post(f"/c/{self.tenant.slug}/pos/floor/", {
            "action": "delete_zone", "zone_id": str(zone.id),
        })
        self.assertFalse(FloorZone.objects.filter(id=zone.id).exists())


# =========================================================================
# B0: расширение Order / OrderItem (PosOrder / PosOrderItem)
# =========================================================================

class PosOrderTests(PosFoundationBase):
    """Аддитивная надстройка над foodcost.Order не трогает сам Order."""

    def _make_order(self):
        # Минимальный реальный Order (как в проде: order_date обязателен,
        # текстовые поля по умолчанию ''; суммы — 0).
        return Order.objects.create(country=self.tenant, order_date=timezone.now())

    def test_pos_order_extends_order_one_to_one(self):
        order = self._make_order()
        waiter = Employee.objects.create(country=self.tenant, name="Азиз")
        zone = FloorZone.objects.create(tenant=self.tenant, name="Зал")
        table = FloorTable.objects.create(zone=zone, number="7", seats=4)

        pos = PosOrder.objects.create(
            order=order, is_dine_in=True, table=table, guests=3, waiter=waiter,
        )
        # обратная связь и дефолты
        self.assertTrue(pos.is_dine_in)
        self.assertEqual(order.pos, pos)
        self.assertEqual(table.orders.first(), pos)
        self.assertEqual(pos.service_charge_amount, Decimal("0"))
        self.assertEqual(pos.tips_amount, Decimal("0"))
        self.assertIn("стол 7", str(pos))

    def test_delete_table_keeps_order(self):
        order = self._make_order()
        zone = FloorZone.objects.create(tenant=self.tenant, name="Зал")
        table = FloorTable.objects.create(zone=zone, number="7", seats=4)
        pos = PosOrder.objects.create(order=order, table=table)
        table.delete()
        pos.refresh_from_db()
        # стол отвязался (SET_NULL), заказ жив
        self.assertIsNone(pos.table_id)
        self.assertTrue(Order.objects.filter(id=order.id).exists())


class PosOrderItemTests(PosFoundationBase):
    def _make_order_item(self):
        order = Order.objects.create(country=self.tenant, order_date=timezone.now())
        return OrderItem.objects.create(
            order=order, dish=self.dish, quantity=Decimal("1"),
            total_price=Decimal("42000"), dish_name_snapshot="Плов",
        )

    def test_kitchen_status_defaults_and_screen(self):
        oi = self._make_order_item()
        screen = KitchenScreen.objects.create(tenant=self.tenant, name="Горячий цех")
        pi = PosOrderItem.objects.create(order_item=oi, screen=screen, course=2)

        self.assertEqual(pi.kitchen_status, PosOrderItem.KS_ACCEPTED)
        self.assertEqual(pi.course, 2)
        self.assertEqual(oi.pos, pi)
        self.assertEqual(screen.items.first(), pi)
        self.assertEqual(pi.discount_percent, Decimal("0"))

    def test_cook_seconds(self):
        oi = self._make_order_item()
        pi = PosOrderItem.objects.create(order_item=oi)
        # не отправлена на кухню → None
        self.assertIsNone(pi.cook_seconds)
        # отправлена и готова → разница в секундах
        now = timezone.now()
        pi.sent_to_kitchen_at = now - datetime.timedelta(minutes=5)
        pi.ready_at = now
        pi.save()
        self.assertEqual(pi.cook_seconds, 300)

    def test_delete_order_item_cascades_pos(self):
        oi = self._make_order_item()
        pi = PosOrderItem.objects.create(order_item=oi)
        oi.delete()
        self.assertFalse(PosOrderItem.objects.filter(id=pi.id).exists())


# =========================================================================
# B1: экран кассира (живой заказ зала)
# =========================================================================

import json as _json


class CashierViewTests(PosFoundationBase):
    """Создание и ведение заказа стола через AJAX-эндпоинты."""

    def setUp(self):
        self.admin = User.objects.create_superuser("cash", "c@x.co", "x")
        self.client.force_login(self.admin)
        self.zone = FloorZone.objects.create(tenant=self.tenant, location=self.location, name="Зал")
        self.table = FloorTable.objects.create(zone=self.zone, number="3", seats=4)
        # второе блюдо в той же категории
        self.dish.category = self.category
        self.dish.save()
        self.dish2 = Dish.objects.create(
            country=self.tenant, name="Лагман", category=self.category,
            final_weight=Decimal("0.350"), selling_price=Decimal("38000"),
        )
        self.screen = KitchenScreen.objects.create(tenant=self.tenant, name="Горячий цех")
        self.screen.categories.add(self.category)

    def _url(self, suffix):
        return f"/c/{self.tenant.slug}/pos/order/{suffix}"

    def _json(self, r):
        self.assertEqual(r.status_code, 200, r.content[:300])
        return _json.loads(r.content)

    def test_page_opens(self):
        r = self.client.get(self._url(""))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Касса")
        self.assertContains(r, "Лагман")  # блюдо отрисовано

    def test_open_creates_order(self):
        r = self.client.post(self._url("api/open/"), {"table_id": self.table.id, "guests": "2"})
        data = self._json(r)
        self.assertTrue(data["order_id"])
        self.assertEqual(data["table_number"], "3")
        self.assertEqual(data["guests"], 2)
        self.assertEqual(data["items"], [])
        pos = PosOrder.objects.get(order_id=data["order_id"])
        self.assertTrue(pos.is_dine_in)
        self.assertEqual(pos.table_id, self.table.id)
        # штатный Order создан с dine-in дефолтами
        self.assertEqual(pos.order.fulfillment_method, Order.FULFILLMENT_PICKUP)

    def test_open_reuses_active_order(self):
        d1 = self._json(self.client.post(self._url("api/open/"), {"table_id": self.table.id}))
        d2 = self._json(self.client.post(self._url("api/open/"), {"table_id": self.table.id}))
        self.assertEqual(d1["order_id"], d2["order_id"])  # тот же открытый счёт

    def test_add_and_merge_same_dish(self):
        oid = self._json(self.client.post(self._url("api/open/"), {"table_id": self.table.id}))["order_id"]
        self.client.post(self._url("api/add/"), {"order_id": oid, "dish_id": self.dish.id})
        data = self._json(self.client.post(self._url("api/add/"), {"order_id": oid, "dish_id": self.dish.id}))
        # то же блюдо, ещё не отправлено -> одна строка, qty=2
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["items"][0]["qty"], 2)
        self.assertEqual(data["subtotal"], 2 * 42000)
        self.assertTrue(data["has_unsent"])

    def test_qty_and_remove(self):
        oid = self._json(self.client.post(self._url("api/open/"), {"table_id": self.table.id}))["order_id"]
        self.client.post(self._url("api/add/"), {"order_id": oid, "dish_id": self.dish.id})
        item_id = self._json(self.client.post(self._url("api/add/"), {"order_id": oid, "dish_id": self.dish2.id}))["items"][-1]["id"]
        # qty=3 по лагману
        data = self._json(self.client.post(self._url("api/qty/"), {"item_id": item_id, "qty": "3"}))
        lag = [i for i in data["items"] if i["id"] == item_id][0]
        self.assertEqual(lag["qty"], 3)
        self.assertEqual(data["subtotal"], 42000 + 3 * 38000)
        # удалить лагман
        data = self._json(self.client.post(self._url("api/remove/"), {"item_id": item_id}))
        self.assertEqual(len(data["items"]), 1)
        self.assertEqual(data["subtotal"], 42000)

    def test_qty_zero_deletes(self):
        oid = self._json(self.client.post(self._url("api/open/"), {"table_id": self.table.id}))["order_id"]
        item_id = self._json(self.client.post(self._url("api/add/"), {"order_id": oid, "dish_id": self.dish.id}))["items"][0]["id"]
        data = self._json(self.client.post(self._url("api/qty/"), {"item_id": item_id, "qty": "0"}))
        self.assertEqual(data["items"], [])

    def test_send_assigns_screen_and_locks(self):
        oid = self._json(self.client.post(self._url("api/open/"), {"table_id": self.table.id}))["order_id"]
        item_id = self._json(self.client.post(self._url("api/add/"), {"order_id": oid, "dish_id": self.dish.id}))["items"][0]["id"]
        data = self._json(self.client.post(self._url("api/send/"), {"order_id": oid}))
        self.assertEqual(data["sent_count"], 1)
        self.assertTrue(data["items"][0]["sent"])
        self.assertFalse(data["has_unsent"])
        pi = PosOrderItem.objects.get(order_item_id=item_id)
        self.assertIsNotNone(pi.sent_to_kitchen_at)
        self.assertEqual(pi.screen_id, self.screen.id)  # подобран по категории
        self.assertEqual(Order.objects.get(id=oid).status, Order.STATUS_COOKING)
        # повторный тап того же блюда -> НЕ мёржится в отправленную строку, новая строка
        data2 = self._json(self.client.post(self._url("api/add/"), {"order_id": oid, "dish_id": self.dish.id}))
        self.assertEqual(len(data2["items"]), 2)
        # менять отправленную позицию нельзя
        r = self.client.post(self._url("api/qty/"), {"item_id": item_id, "qty": "5"})
        self.assertEqual(r.status_code, 400)

    def test_requires_orders_section(self):
        weak = User.objects.create_user("weak2", "w2@x.co", "x")
        from foodcost.models import UserProfile
        prof = UserProfile.objects.create(user=weak, role=UserProfile.ROLE_VIEWER, allowed_sections=[])
        prof.countries.add(self.tenant)
        self.client.force_login(weak)
        r = self.client.get(self._url(""))
        self.assertEqual(r.status_code, 403)


# =========================================================================
# B2: экран KDS (кухня)
# =========================================================================

class KdsViewTests(PosFoundationBase):
    def setUp(self):
        self.admin = User.objects.create_superuser("kds", "k@x.co", "x")
        self.client.force_login(self.admin)
        self.zone = FloorZone.objects.create(tenant=self.tenant, name="Зал")
        self.table = FloorTable.objects.create(zone=self.zone, number="5", seats=4)
        self.dish.category = self.category
        self.dish.save()
        self.screen = KitchenScreen.objects.create(tenant=self.tenant, name="Горячий цех")
        self.screen.categories.add(self.category)

    def _url(self, suffix):
        return f"/c/{self.tenant.slug}/pos/kds/{suffix}"

    def _make_sent_order(self, minutes_ago=3, screen=None, n_items=1):
        """Заказ с n позициями, отправленными на кухню minutes_ago минут назад."""
        order = Order.objects.create(country=self.tenant, order_date=timezone.now())
        PosOrder.objects.create(order=order, is_dine_in=True, table=self.table, guests=2)
        sent_at = timezone.now() - datetime.timedelta(minutes=minutes_ago)
        items = []
        for i in range(n_items):
            oi = OrderItem.objects.create(
                order=order, dish=self.dish, quantity=Decimal("1"),
                price_snapshot=Decimal("42000"), total_price=Decimal("42000"),
                dish_name_snapshot="Плов",
            )
            pi = PosOrderItem.objects.create(
                order_item=oi, screen=(screen if screen is not None else self.screen),
                sent_to_kitchen_at=sent_at,
            )
            items.append(pi)
        return order, items

    def _board(self, screen=0):
        r = self.client.post(self._url("api/board/"), {"screen": screen})
        self.assertEqual(r.status_code, 200, r.content[:300])
        return _json.loads(r.content)["tickets"]

    def test_page_opens(self):
        r = self.client.get(self._url(""))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "KDS")
        self.assertContains(r, "Горячий цех")  # экран в селекторе

    def test_board_shows_sent_ticket(self):
        order, items = self._make_sent_order(minutes_ago=3)
        tickets = self._board()
        self.assertEqual(len(tickets), 1)
        t = tickets[0]
        self.assertEqual(t["order_id"], order.id)
        self.assertEqual(t["table"], "5")
        self.assertEqual(len(t["items"]), 1)
        self.assertFalse(t["items"][0]["ready"])
        # таймер ~3 мин (180с), с запасом на выполнение
        self.assertGreaterEqual(t["items"][0]["cook_seconds"], 170)

    def test_threshold_seconds_from_category(self):
        KitchenTimeThreshold.objects.create(
            tenant=self.tenant, screen=self.screen, category=self.category,
            warn_minutes=2, late_minutes=5,
        )
        self._make_sent_order(minutes_ago=1)
        it = self._board()[0]["items"][0]
        self.assertEqual(it["warn_seconds"], 120)
        self.assertEqual(it["late_seconds"], 300)

    def test_ready_single_item_clears_ticket_and_marks_order(self):
        order, items = self._make_sent_order(n_items=1)
        pid = items[0].id
        r = self.client.post(self._url("api/ready/"), {"item_id": pid, "screen": 0})
        tickets = _json.loads(r.content)["tickets"]
        self.assertEqual(tickets, [])  # весь заказ готов -> тикет пропал
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_READY)
        items[0].refresh_from_db()
        self.assertEqual(items[0].kitchen_status, PosOrderItem.KS_READY)
        self.assertIsNotNone(items[0].ready_at)

    def test_ready_partial_keeps_ticket(self):
        order, items = self._make_sent_order(n_items=2)
        # готова одна из двух
        self.client.post(self._url("api/ready/"), {"item_id": items[0].id, "screen": 0})
        tickets = self._board()
        self.assertEqual(len(tickets), 1)  # тикет остаётся
        order.refresh_from_db()
        self.assertNotEqual(order.status, Order.STATUS_READY)
        # готова вторая -> тикет уходит, заказ готов
        self.client.post(self._url("api/ready/"), {"item_id": items[1].id, "screen": 0})
        self.assertEqual(self._board(), [])
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_READY)

    def test_undo_returns_item_and_order(self):
        order, items = self._make_sent_order(n_items=1)
        self.client.post(self._url("api/ready/"), {"item_id": items[0].id, "screen": 0})
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_READY)
        self.client.post(self._url("api/undo/"), {"item_id": items[0].id, "screen": 0})
        tickets = self._board()
        self.assertEqual(len(tickets), 1)  # снова на доске
        order.refresh_from_db()
        self.assertEqual(order.status, Order.STATUS_COOKING)

    def test_screen_filter(self):
        other = KitchenScreen.objects.create(tenant=self.tenant, name="Холодный цех")
        self._make_sent_order(screen=self.screen)
        # на своём экране виден
        self.assertEqual(len(self._board(screen=self.screen.id)), 1)
        # на чужом — нет
        self.assertEqual(self._board(screen=other.id), [])

    def test_requires_orders_section(self):
        weak = User.objects.create_user("weakk", "wk@x.co", "x")
        from foodcost.models import UserProfile
        prof = UserProfile.objects.create(user=weak, role=UserProfile.ROLE_VIEWER, allowed_sections=[])
        prof.countries.add(self.tenant)
        self.client.force_login(weak)
        r = self.client.get(self._url(""))
        self.assertEqual(r.status_code, 403)


# =========================================================================
# B3: оплата и закрытие счёта (ядро)
# =========================================================================

class PaymentFlowTests(PosFoundationBase):
    def setUp(self):
        self.admin = User.objects.create_superuser("pay", "p@x.co", "x")
        self.client.force_login(self.admin)
        self.zone = FloorZone.objects.create(tenant=self.tenant, name="Зал")
        self.table = FloorTable.objects.create(zone=self.zone, number="7", seats=4)
        self.card = PaymentMethod.objects.create(country=self.tenant, name="Карта", is_cash=False)

    def _u(self, s):
        return f"/c/{self.tenant.slug}/pos/order/{s}"

    def _json(self, r):
        self.assertEqual(r.status_code, 200, r.content[:300])
        return _json.loads(r.content)

    def _order_with_plov(self, n=1):
        oid = self._json(self.client.post(self._u("api/open/"), {"table_id": self.table.id}))["order_id"]
        for _ in range(n):
            self.client.post(self._u("api/add/"), {"order_id": oid, "dish_id": self.dish.id})
        return oid

    def _quote(self, oid, **params):
        params["order_id"] = oid
        return self._json(self.client.post(self._u("api/quote/"), params))

    def test_quote_basic(self):
        oid = self._order_with_plov()
        q = self._quote(oid)
        self.assertEqual(q["subtotal"], 42000)
        self.assertEqual(q["total"], 42000)

    def test_quote_discount_percent(self):
        oid = self._order_with_plov()
        q = self._quote(oid, discount_type="percent", discount_value="10")
        self.assertEqual(q["discount"], 4200)
        self.assertEqual(q["total"], 37800)

    def test_service_charge_added(self):
        ServiceTipSettings.objects.create(
            tenant=self.tenant, location=None,
            service_enabled=True, service_percent=Decimal("10"), service_dine_in=True,
        )
        oid = self._order_with_plov()
        q = self._quote(oid)
        self.assertEqual(q["service"], 4200)
        self.assertEqual(q["total"], 46200)

    def test_bonus_capped_by_redeem_percent(self):
        LoyaltyProgram.objects.create(
            tenant=self.tenant, enabled=True,
            redeem_max_percent=Decimal("50"), combine_with_discounts=True,
        )
        CustomerPoints.objects.create(customer=self.customer, balance=Decimal("100000"))
        oid = self._order_with_plov()
        # запрос лимита
        r = self._json(self.client.post(self._u("api/loyalty/"), {"order_id": oid, "phone": self.customer.phone}))
        self.assertTrue(r["found"])
        self.assertEqual(r["max_redeem"], 21000)  # 50% от 42000
        # пробуем списать больше лимита -> режется
        q = self._quote(oid, bonus_points="30000")
        self.assertEqual(q["bonus"], 21000)
        self.assertEqual(q["total"], 21000)

    def test_pay_single_cash_closes_order(self):
        oid = self._order_with_plov()
        r = self.client.post(self._u("api/pay/"), {
            "order_id": oid, "method_id": self.payment.id, "amount": "42000",
        })
        res = self._json(r)
        self.assertTrue(res["ok"])
        order = Order.objects.get(id=oid)
        self.assertEqual(order.status, Order.STATUS_DONE)
        self.assertEqual(order.payment_status, Order.PAYMENT_STATUS_CASH)
        self.assertEqual(order.total_amount, Decimal("42000"))
        self.assertEqual(PosPayment.objects.filter(order=order).count(), 1)

    def test_pay_multi_tender(self):
        oid = self._order_with_plov()
        body = f"order_id={oid}&method_id={self.payment.id}&amount=20000&method_id={self.card.id}&amount=22000"
        r = self.client.post(self._u("api/pay/"), data=body, content_type="application/x-www-form-urlencoded")
        res = self._json(r)
        self.assertTrue(res["ok"])
        order = Order.objects.get(id=oid)
        self.assertEqual(order.payment_status, Order.PAYMENT_STATUS_PAID)  # есть безнал
        self.assertEqual(PosPayment.objects.filter(order=order).count(), 2)

    def test_pay_sum_mismatch_rejected(self):
        oid = self._order_with_plov()
        r = self.client.post(self._u("api/pay/"), {
            "order_id": oid, "method_id": self.payment.id, "amount": "40000",
        })
        self.assertEqual(r.status_code, 400)
        self.assertEqual(Order.objects.get(id=oid).status, Order.STATUS_NEW)

    def test_pay_accrues_points(self):
        LoyaltyProgram.objects.create(
            tenant=self.tenant, enabled=True,
            earn_percent=Decimal("5"), earn_exclude_discounted=False,
            redeem_max_percent=Decimal("50"), combine_with_discounts=True,
        )
        CustomerPoints.objects.create(customer=self.customer, balance=Decimal("0"))
        oid = self._order_with_plov()
        # привязать гостя
        self.client.post(self._u("api/loyalty/"), {"order_id": oid, "phone": self.customer.phone})
        res = self._json(self.client.post(self._u("api/pay/"), {
            "order_id": oid, "method_id": self.payment.id, "amount": "42000",
        }))
        self.assertEqual(res["points_earned"], 2100)  # 5% от 42000
        self.customer.loyalty_points.refresh_from_db()
        self.assertEqual(self.customer.loyalty_points.balance, Decimal("2100"))

    def test_paid_order_frees_table(self):
        """После оплаты стол освобождается: повторный open создаёт новый заказ."""
        oid = self._order_with_plov()
        self.client.post(self._u("api/pay/"), {
            "order_id": oid, "method_id": self.payment.id, "amount": "42000",
        })
        oid2 = self._json(self.client.post(self._u("api/open/"), {"table_id": self.table.id}))["order_id"]
        self.assertNotEqual(oid, oid2)


# =========================================================================
# B3.1: деление счёта (по позициям и поровну)
# =========================================================================

class SplitBillTests(PosFoundationBase):
    def setUp(self):
        self.admin = User.objects.create_superuser("split", "s@x.co", "x")
        self.client.force_login(self.admin)
        self.zone = FloorZone.objects.create(tenant=self.tenant, name="Зал")
        self.table = FloorTable.objects.create(zone=self.zone, number="9", seats=4)
        self.dish2 = Dish.objects.create(
            country=self.tenant, name="Лагман",
            final_weight=Decimal("0.35"), selling_price=Decimal("38000"),
        )

    def _u(self, s):
        return f"/c/{self.tenant.slug}/pos/order/{s}"

    def _json(self, r):
        self.assertEqual(r.status_code, 200, r.content[:300])
        return _json.loads(r.content)

    def _order_two_items(self):
        """Заказ: Плов 42000 + Лагман 38000 = 80000. Возвращает (oid, item_ids)."""
        oid = self._json(self.client.post(self._u("api/open/"), {"table_id": self.table.id}))["order_id"]
        self.client.post(self._u("api/add/"), {"order_id": oid, "dish_id": self.dish.id})
        data = self._json(self.client.post(self._u("api/add/"), {"order_id": oid, "dish_id": self.dish2.id}))
        ids = [it["id"] for it in data["items"]]
        return oid, ids

    def test_even_split_two(self):
        oid, _ = self._order_two_items()
        st = self._json(self.client.post(self._u("api/split/set/"), {"order_id": oid, "mode": "even", "n": "2"}))
        self.assertEqual(st["mode"], "even")
        self.assertEqual(len(st["bills"]), 2)
        self.assertEqual([b["amount"] for b in st["bills"]], [40000, 40000])

    def test_even_split_remainder_sums_to_total(self):
        oid, _ = self._order_two_items()  # 80000
        st = self._json(self.client.post(self._u("api/split/set/"), {"order_id": oid, "mode": "even", "n": "3"}))
        amounts = [b["amount"] for b in st["bills"]]
        self.assertEqual(round(sum(amounts), 2), 80000)  # без потери копеек
        self.assertEqual(len(amounts), 3)

    def test_positions_split_amounts(self):
        oid, ids = self._order_two_items()
        import json as jj
        assignments = jj.dumps({str(ids[0]): 0, str(ids[1]): 1})
        st = self._json(self.client.post(self._u("api/split/set/"), {"order_id": oid, "mode": "positions", "assignments": assignments}))
        self.assertEqual(st["mode"], "positions")
        amts = sorted(b["amount"] for b in st["bills"])
        self.assertEqual(amts, [38000, 42000])
        self.assertEqual(st["unassigned"], 0)

    def test_pay_all_bills_closes_order(self):
        oid, _ = self._order_two_items()
        st = self._json(self.client.post(self._u("api/split/set/"), {"order_id": oid, "mode": "even", "n": "2"}))
        b0, b1 = st["bills"]
        r1 = self._json(self.client.post(self._u("api/split/pay/"), {
            "order_id": oid, "bill_id": b0["id"], "method_id": self.payment.id, "amount": "40000",
        }))
        self.assertFalse(r1["order_closed"])
        self.assertEqual(Order.objects.get(id=oid).status, Order.STATUS_NEW)
        r2 = self._json(self.client.post(self._u("api/split/pay/"), {
            "order_id": oid, "bill_id": b1["id"], "method_id": self.payment.id, "amount": "40000",
        }))
        self.assertTrue(r2["order_closed"])
        order = Order.objects.get(id=oid)
        self.assertEqual(order.status, Order.STATUS_DONE)
        self.assertEqual(order.total_amount, Decimal("80000"))
        self.assertEqual(PosPayment.objects.filter(order=order).count(), 2)

    def test_positions_unassigned_blocks_pay(self):
        oid, ids = self._order_two_items()
        import json as jj
        # распределили только одну позицию
        assignments = jj.dumps({str(ids[0]): 0})
        st = self._json(self.client.post(self._u("api/split/set/"), {"order_id": oid, "mode": "positions", "assignments": assignments}))
        bill = st["bills"][0]
        r = self.client.post(self._u("api/split/pay/"), {
            "order_id": oid, "bill_id": bill["id"], "method_id": self.payment.id, "amount": str(bill["amount"]),
        })
        self.assertEqual(r.status_code, 400)

    def test_pay_mismatch_rejected(self):
        oid, _ = self._order_two_items()
        st = self._json(self.client.post(self._u("api/split/set/"), {"order_id": oid, "mode": "even", "n": "2"}))
        r = self.client.post(self._u("api/split/pay/"), {
            "order_id": oid, "bill_id": st["bills"][0]["id"], "method_id": self.payment.id, "amount": "30000",
        })
        self.assertEqual(r.status_code, 400)

    def test_clear_split(self):
        oid, _ = self._order_two_items()
        self.client.post(self._u("api/split/set/"), {"order_id": oid, "mode": "even", "n": "2"})
        st = self._json(self.client.post(self._u("api/split/clear/"), {"order_id": oid}))
        self.assertEqual(st["mode"], "none")
        self.assertEqual(st["bills"], [])
        self.assertEqual(PosBill.objects.filter(pos_order__order_id=oid).count(), 0)

    def test_cannot_resplit_after_payment(self):
        oid, _ = self._order_two_items()
        st = self._json(self.client.post(self._u("api/split/set/"), {"order_id": oid, "mode": "even", "n": "2"}))
        self.client.post(self._u("api/split/pay/"), {
            "order_id": oid, "bill_id": st["bills"][0]["id"], "method_id": self.payment.id, "amount": "40000",
        })
        r = self.client.post(self._u("api/split/set/"), {"order_id": oid, "mode": "even", "n": "4"})
        self.assertEqual(r.status_code, 400)


# =========================================================================
# seed_pos_defaults (демо-данные)
# =========================================================================

class SeedCommandTests(PosFoundationBase):
    def test_seed_creates_and_is_idempotent(self):
        from django.core.management import call_command
        from io import StringIO
        out = StringIO()
        call_command("seed_pos_defaults", "--tenant", self.tenant.slug, stdout=out)

        # ключевые объекты созданы
        self.assertTrue(FloorZone.objects.filter(tenant=self.tenant, name="Основной зал").exists())
        self.assertEqual(FloorTable.objects.filter(zone__tenant=self.tenant).count(), 4)
        self.assertTrue(KitchenScreen.objects.filter(tenant=self.tenant, name="Горячий цех").exists())
        self.assertTrue(ServiceTipSettings.objects.filter(tenant=self.tenant).exists())
        self.assertTrue(LoyaltyProgram.objects.filter(tenant=self.tenant, enabled=True).exists())
        self.assertTrue(FiscalizationSettings.objects.filter(tenant=self.tenant).exists())
        self.assertTrue(PaymentMethod.objects.filter(country=self.tenant, name="Наличные").exists())
        # экран подхватил существующую категорию
        screen = KitchenScreen.objects.get(tenant=self.tenant, name="Горячий цех")
        self.assertIn(self.category, list(screen.categories.all()))

        # повторный запуск не плодит дубли
        call_command("seed_pos_defaults", "--tenant", self.tenant.slug, stdout=StringIO())
        self.assertEqual(FloorTable.objects.filter(zone__tenant=self.tenant).count(), 4)
        self.assertEqual(KitchenScreen.objects.filter(tenant=self.tenant, name="Горячий цех").count(), 1)
        self.assertEqual(PaymentMethod.objects.filter(country=self.tenant, name="Наличные").count(), 1)


# =========================================================================
# Роли официант/кухня (POS-доступы)
# =========================================================================

from pos.access import POS_CASHIER, POS_KDS, POS_SETTINGS, pos_can, explicit_caps


class PosRolesTests(PosFoundationBase):
    def _staff(self, username, role=None, sections=None):
        u = User.objects.create_user(username, f"{username}@x.co", "x")
        prof = UserProfile.objects.create(
            user=u, role=role or UserProfile.ROLE_VIEWER,
            allowed_sections=sections or [],
        )
        prof.countries.add(self.tenant)
        return u

    def _get(self, user, path):
        self.client.force_login(user)
        return self.client.get(f"/c/{self.tenant.slug}/pos/{path}")

    def test_waiter_sees_only_cashier(self):
        w = self._staff("waiter", sections=[POS_CASHIER])
        self.assertEqual(self._get(w, "order/").status_code, 200)
        self.assertEqual(self._get(w, "kds/").status_code, 403)
        self.assertEqual(self._get(w, "kitchen/").status_code, 403)

    def test_cook_sees_only_kds(self):
        c = self._staff("cook", sections=[POS_KDS])
        self.assertEqual(self._get(c, "kds/").status_code, 200)
        self.assertEqual(self._get(c, "order/").status_code, 403)
        self.assertEqual(self._get(c, "kitchen/").status_code, 403)

    def test_manager_sees_all(self):
        m = self._staff("manager", sections=[POS_CASHIER, POS_KDS, POS_SETTINGS])
        self.assertEqual(self._get(m, "order/").status_code, 200)
        self.assertEqual(self._get(m, "kds/").status_code, 200)
        self.assertEqual(self._get(m, "kitchen/").status_code, 200)
        self.assertEqual(self._get(m, "access/").status_code, 200)

    def test_fallback_orders_section_grants_cashier_and_kds(self):
        # POS-роли не назначены -> прежняя логика: секция «Заказы» даёт и кассу, и KDS
        u = self._staff("legacy", sections=[UserProfile.SECTION_ORDERS])
        self.assertEqual(self._get(u, "order/").status_code, 200)
        self.assertEqual(self._get(u, "kds/").status_code, 200)
        self.assertEqual(self._get(u, "kitchen/").status_code, 403)

    def test_fallback_kitchen_role_grants_only_kds(self):
        u = self._staff("cookrole", role=UserProfile.ROLE_KITCHEN_STAFF, sections=[])
        self.assertEqual(self._get(u, "kds/").status_code, 200)
        self.assertEqual(self._get(u, "order/").status_code, 403)

    def test_admin_role_sees_all(self):
        a = self._staff("boss", role=UserProfile.ROLE_ADMIN, sections=[])
        self.assertEqual(self._get(a, "order/").status_code, 200)
        self.assertEqual(self._get(a, "kds/").status_code, 200)
        self.assertEqual(self._get(a, "kitchen/").status_code, 200)

    def test_pay_api_blocked_for_cook(self):
        c = self._staff("cook2", sections=[POS_KDS])
        self.client.force_login(c)
        r = self.client.post(f"/c/{self.tenant.slug}/pos/order/api/open/", {"table_id": 1})
        self.assertEqual(r.status_code, 403)

    def test_access_page_assigns_roles(self):
        admin = User.objects.create_superuser("root", "r@x.co", "x")
        self.client.force_login(admin)
        target = self._staff("newwaiter", sections=[])
        # назначаем официанта (только касса)
        r = self.client.post(f"/c/{self.tenant.slug}/pos/access/", {
            "action": "save", "user_id": target.id, "caps": [POS_CASHIER],
        })
        self.assertEqual(r.status_code, 302)
        prof = UserProfile.objects.get(user=target)
        self.assertEqual(explicit_caps(prof), [POS_CASHIER])
        # перечитываем пользователя (кэш related-профиля)
        target = User.objects.get(id=target.id)
        self.assertTrue(pos_can(target, POS_CASHIER))
        self.assertFalse(pos_can(target, POS_KDS))


# =========================================================================
# Живой план зала (drag-drop)
# =========================================================================

class FloorPlanMoveTests(PosFoundationBase):
    def setUp(self):
        self.admin = User.objects.create_superuser("floor", "f@x.co", "x")
        self.client.force_login(self.admin)
        self.zone = FloorZone.objects.create(tenant=self.tenant, name="Зал")

    def _move(self, table_id, x, y):
        return self.client.post(f"/c/{self.tenant.slug}/pos/floor/api/move/", {
            "table_id": table_id, "x": x, "y": y,
        })

    def test_move_saves_position(self):
        t = FloorTable.objects.create(zone=self.zone, number="1")
        r = self._move(t.id, "25.5", "60")
        self.assertEqual(r.status_code, 200)
        t.refresh_from_db()
        self.assertEqual(float(t.pos_x), 25.5)
        self.assertEqual(float(t.pos_y), 60)

    def test_move_clamps_range(self):
        t = FloorTable.objects.create(zone=self.zone, number="1")
        self._move(t.id, "150", "-10")
        t.refresh_from_db()
        self.assertEqual(float(t.pos_x), 100)
        self.assertEqual(float(t.pos_y), 0)

    def test_add_table_scatters_position(self):
        # через форму floor_plan — первый стол не в центре (50,50)
        self.client.post(f"/c/{self.tenant.slug}/pos/floor/", {
            "action": "add_table", "zone_id": self.zone.id, "number": "7", "seats": "4",
            "shape": FloorTable.SHAPE_ROUND,
        })
        t = FloorTable.objects.get(zone=self.zone, number="7")
        self.assertNotEqual((float(t.pos_x), float(t.pos_y)), (50.0, 50.0))

    def test_move_requires_settings_access(self):
        t = FloorTable.objects.create(zone=self.zone, number="1")
        waiter = User.objects.create_user("w3", "w3@x.co", "x")
        prof = UserProfile.objects.create(user=waiter, role=UserProfile.ROLE_VIEWER, allowed_sections=[POS_CASHIER])
        prof.countries.add(self.tenant)
        self.client.force_login(waiter)
        r = self._move(t.id, "20", "20")
        self.assertEqual(r.status_code, 403)


# =========================================================================
# Шов фискализации (минимальный, под будущий e-pos)
# =========================================================================

class FiscalizationSeamTests(PosFoundationBase):
    def setUp(self):
        self.admin = User.objects.create_superuser("fisc", "fi@x.co", "x")
        self.client.force_login(self.admin)
        self.zone = FloorZone.objects.create(tenant=self.tenant, name="Зал")
        self.table = FloorTable.objects.create(zone=self.zone, number="1")

    def _paid_order(self):
        oid = _json.loads(self.client.post(f"/c/{self.tenant.slug}/pos/order/api/open/", {"table_id": self.table.id}).content)["order_id"]
        self.client.post(f"/c/{self.tenant.slug}/pos/order/api/add/", {"order_id": oid, "dish_id": self.dish.id})
        self.client.post(f"/c/{self.tenant.slug}/pos/order/api/pay/", {
            "order_id": oid, "method_id": self.payment.id, "amount": "42000",
        })
        return oid

    def test_stub_test_mode(self):
        FiscalizationSettings.objects.create(
            tenant=self.tenant, location=None, mode=FiscalizationSettings.MODE_TEST)
        oid = self._paid_order()
        p = PosPayment.objects.get(order_id=oid)
        self.assertEqual(p.fiscal_status, PosPayment.FISCAL_TEST)
        self.assertEqual(p.fiscal_receipt_id, "")

    def test_stub_live_mode(self):
        FiscalizationSettings.objects.create(
            tenant=self.tenant, location=None, mode=FiscalizationSettings.MODE_LIVE)
        oid = self._paid_order()
        self.assertEqual(PosPayment.objects.get(order_id=oid).fiscal_status, PosPayment.FISCAL_DONE)

    def test_no_settings_skipped(self):
        oid = self._paid_order()
        self.assertEqual(PosPayment.objects.get(order_id=oid).fiscal_status, PosPayment.FISCAL_SKIPPED)

    def test_provider_result_is_stored(self):
        """Шов работает: результат провайдера (номер чека) сохраняется в оплату.
        Это доказывает, что реальный e-pos-провайдер пропишется без правок вызова."""
        from unittest.mock import patch
        from pos.fiscalization import FiscalResult
        from pos import fiscalization as fmod

        class FakeProvider:
            def fiscalize(self, payment, settings):
                return FiscalResult(PosPayment.FISCAL_DONE, receipt_id="RCPT-42")

        with patch.object(fmod, "get_provider", lambda country, settings: FakeProvider()):
            oid = self._paid_order()
        p = PosPayment.objects.get(order_id=oid)
        self.assertEqual(p.fiscal_status, PosPayment.FISCAL_DONE)
        self.assertEqual(p.fiscal_receipt_id, "RCPT-42")


class SplitStateItemsTest(PosFoundationBase):
    """Регресс: split/state должен ВОЗВРАЩАТЬ позиции (ключ items) — иначе
    режим «по позициям» не видит, что распределять."""

    def setUp(self):
        self.admin = User.objects.create_superuser("ss", "ss@x.co", "x")
        self.client.force_login(self.admin)
        self.zone = FloorZone.objects.create(tenant=self.tenant, name="Зал")
        self.table = FloorTable.objects.create(zone=self.zone, number="1")
        self.dish2 = Dish.objects.create(
            country=self.tenant, name="Лагман",
            final_weight=Decimal("0.35"), selling_price=Decimal("38000"))

    def test_state_returns_items(self):
        u = f"/c/{self.tenant.slug}/pos/order/"
        oid = _json.loads(self.client.post(u + "api/open/", {"table_id": self.table.id}).content)["order_id"]
        self.client.post(u + "api/add/", {"order_id": oid, "dish_id": self.dish.id})
        self.client.post(u + "api/add/", {"order_id": oid, "dish_id": self.dish2.id})
        st = _json.loads(self.client.post(u + "api/split/state/", {"order_id": oid}).content)
        self.assertIn("items", st)
        self.assertEqual(len(st["items"]), 2)
        self.assertEqual({i["name"] for i in st["items"]}, {"Плов", "Лагман"})
        self.assertTrue(all(i["bill_id"] is None for i in st["items"]))
