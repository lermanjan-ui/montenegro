# -*- coding: utf-8 -*-
"""
Фаза 2 (заказы/касса, клиенты): изоляция по филиалам — order_views.py.
Клиент «принадлежит» филиалу, если у него есть заказ в этом филиале.
Правило = user_location_ids. Супер-админ / без филиалов — все.

Проверяем видимость: список клиентов и доступ к карточке.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from foodcost.models import Country, Location, UserProfile, Customer, Order


class CustomerBranchScope(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.l1 = Location.objects.create(country=self.country, name="Филиал 1")
        self.l2 = Location.objects.create(country=self.country, name="Филиал 2")
        self.boss = self._user("boss", UserProfile.ROLE_SUPER_ADMIN, locs=[])

        self.a = self._cust("A", "001")          # заказ в l1
        self.b = self._cust("B", "002")          # заказ в l2
        self.c = self._cust("C", "003")          # заказы в l1 и l2
        self.d = self._cust("D", "004")          # без заказов
        self._order(self.l1, self.a)
        self._order(self.l2, self.b)
        self._order(self.l1, self.c)
        self._order(self.l2, self.c)

        self.mgr = self._user("mgr", UserProfile.ROLE_ADMIN,
                              sections=[UserProfile.SECTION_CUSTOMERS], locs=[self.l1])

    def _user(self, name, role, sections=(), locs=()):
        u = User.objects.create_user(name, password="x")
        p = UserProfile.objects.create(user=u, role=role, allowed_sections=list(sections))
        p.countries.set([self.country])
        if locs:
            p.locations.set(locs)
        return u

    def _cust(self, name, phone):
        return Customer.objects.create(country=self.country, name=name, phone=phone)

    def _order(self, loc, customer):
        return Order.objects.create(
            country=self.country, location=loc, customer=customer,
            order_date=timezone.now(), status=Order.STATUS_NEW,
            total_amount=Decimal("100"), is_cancelled=False, created_by=self.boss,
        )

    def _get(self, user, url):
        self.client.force_login(user)
        return self.client.get(url)

    def _list(self):
        return f"/c/{self.country.slug}/customers/"

    def test_list_scoped(self):
        resp = self._get(self.mgr, self._list())
        self.assertEqual(resp.status_code, 200)
        ids = {r["id"] for r in resp.context["rows"]}
        self.assertEqual(ids, {self.a.id, self.c.id})      # с заказом в l1
        self.assertNotIn(self.b.id, ids)                   # только l2
        self.assertNotIn(self.d.id, ids)                   # без заказов

    def test_list_super_all(self):
        self.boss.profile.allowed_sections = [UserProfile.SECTION_CUSTOMERS]
        self.boss.profile.save()
        resp = self._get(self.boss, self._list())
        ids = {r["id"] for r in resp.context["rows"]}
        self.assertEqual(ids, {self.a.id, self.b.id, self.c.id, self.d.id})

    def test_detail_foreign_redirects(self):
        resp = self._get(self.mgr, f"{self._list()}{self.b.id}/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/customers/", resp["Location"])

    def test_detail_no_orders_redirects(self):
        resp = self._get(self.mgr, f"{self._list()}{self.d.id}/")
        self.assertEqual(resp.status_code, 302)

    def test_detail_own_ok(self):
        resp = self._get(self.mgr, f"{self._list()}{self.a.id}/")
        self.assertEqual(resp.status_code, 200)

    def test_detail_super_ok(self):
        self.boss.profile.allowed_sections = [UserProfile.SECTION_CUSTOMERS]
        self.boss.profile.save()
        resp = self._get(self.boss, f"{self._list()}{self.b.id}/")
        self.assertEqual(resp.status_code, 200)
