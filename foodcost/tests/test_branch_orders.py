# -*- coding: utf-8 -*-
"""
Фаза 2 (заказы/касса, ядро): изоляция по филиалам — order_views.py.
«Филиал» = Order.location. Правило = user_location_ids (как в складе).

Проверяем на надёжных маршрутах:
  • /orders/        (order_list)   — список/сводка сужены до филиалов;
  • /orders/<id>/   (order_detail) — чужой заказ недоступен (redirect).
Остальные вьюхи модуля (orders_count / order_all_list / order_create /
lunch_orders_list) используют тот же helper scope_by_location по Order.location.
Супер-админ / без филиалов — всё.
"""
import datetime

from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from foodcost.models import Country, Location, UserProfile, Order


class OrdersBranchScope(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.l1 = Location.objects.create(country=self.country, name="Филиал 1")
        self.l2 = Location.objects.create(country=self.country, name="Филиал 2")
        self.boss = self._user("boss", UserProfile.ROLE_SUPER_ADMIN, locs=[])
        self.o1 = self._order(self.l1)
        self.o2 = self._order(self.l2)
        self.mgr = self._user("mgr", UserProfile.ROLE_ADMIN,
                              sections=[UserProfile.SECTION_ORDERS], locs=[self.l1])

    def _user(self, name, role, sections=(), locs=()):
        u = User.objects.create_user(name, password="x")
        p = UserProfile.objects.create(user=u, role=role, allowed_sections=list(sections))
        p.countries.set([self.country])
        if locs:
            p.locations.set(locs)
        return u

    def _order(self, loc):
        return Order.objects.create(
            country=self.country, location=loc,
            order_date=timezone.now(),
            status=Order.STATUS_NEW,
            total_amount=Decimal("0"), delivery_amount=Decimal("0"),
            is_cancelled=False, created_by=self.boss,
        )

    def _get(self, user, url):
        self.client.force_login(user)
        return self.client.get(url)

    def _url(self):
        return f"/c/{self.country.slug}/orders/"

    # --- order_list ---
    def test_order_list_scoped(self):
        resp = self._get(self.mgr, self._url())
        self.assertEqual(resp.status_code, 200)
        locs = {o.location_id for o in resp.context["orders"]}
        self.assertEqual(locs, {self.l1.id})
        names = {r["name"] for r in resp.context["locations_summary"]}
        self.assertEqual(names, {self.l1.name})

    def test_order_list_super_all(self):
        self.boss.profile.allowed_sections = [UserProfile.SECTION_ORDERS]
        self.boss.profile.save()
        resp = self._get(self.boss, self._url())
        locs = {o.location_id for o in resp.context["orders"]}
        self.assertEqual(locs, {self.l1.id, self.l2.id})

    # --- order_detail access ---
    def test_detail_foreign_redirects(self):
        resp = self._get(self.mgr, f"{self._url()}{self.o2.id}/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/orders/", resp["Location"])

    def test_detail_own_ok(self):
        resp = self._get(self.mgr, f"{self._url()}{self.o1.id}/")
        self.assertEqual(resp.status_code, 200)

    def test_detail_super_ok(self):
        self.boss.profile.allowed_sections = [UserProfile.SECTION_ORDERS]
        self.boss.profile.save()
        resp = self._get(self.boss, f"{self._url()}{self.o2.id}/")
        self.assertEqual(resp.status_code, 200)
