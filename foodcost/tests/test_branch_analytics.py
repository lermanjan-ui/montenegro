# -*- coding: utf-8 -*-
"""
Фаза 2 (заказы/касса, аналитика): изоляция по филиалам — order_analytics.
«Филиал» = Order.location. /orders/analytics/, период по умолчанию = сегодня.

Проверяем, что снимок заказов и сводка по филиалам сужены до филиалов
пользователя (total_orders и locations_summary — без зависимости от значений
денежных свойств). Супер-админ — всё.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from foodcost.models import Country, Location, UserProfile, Order


class OrderAnalyticsBranchScope(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.l1 = Location.objects.create(country=self.country, name="Филиал 1")
        self.l2 = Location.objects.create(country=self.country, name="Филиал 2")
        self.boss = self._user("boss", UserProfile.ROLE_SUPER_ADMIN, locs=[])
        self._order(self.l1, "100")
        self._order(self.l2, "200")
        self.mgr = self._user("mgr", UserProfile.ROLE_ADMIN,
                              sections=[UserProfile.SECTION_ORDER_ANALYTICS], locs=[self.l1])

    def _user(self, name, role, sections=(), locs=()):
        u = User.objects.create_user(name, password="x")
        p = UserProfile.objects.create(user=u, role=role, allowed_sections=list(sections))
        p.countries.set([self.country])
        if locs:
            p.locations.set(locs)
        return u

    def _order(self, loc, amount):
        amt = Decimal(amount)
        return Order.objects.create(
            country=self.country, location=loc,
            order_date=timezone.now(), status=Order.STATUS_NEW,
            subtotal_amount=amt, discount_amount=Decimal("0"),
            delivery_amount=Decimal("0"), customer_delivery_amount=Decimal("0"),
            total_amount=amt, is_cancelled=False, created_by=self.boss,
        )

    def _get(self, user):
        self.client.force_login(user)
        return self.client.get(f"/c/{self.country.slug}/orders/analytics/")

    def test_scoped(self):
        resp = self._get(self.mgr)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["total_orders"], 1)
        self.assertEqual(resp.context["active_orders_count"], 1)
        names = {r["name"] for r in resp.context["locations_summary"]}
        self.assertEqual(names, {self.l1.name})

    def test_super_admin_all(self):
        self.boss.profile.allowed_sections = [UserProfile.SECTION_ORDER_ANALYTICS]
        self.boss.profile.save()
        resp = self._get(self.boss)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["total_orders"], 2)
        names = {r["name"] for r in resp.context["locations_summary"]}
        self.assertEqual(names, {self.l1.name, self.l2.name})
