# -*- coding: utf-8 -*-
"""
Фаза 2 — хвосты:
  (а) customer_list: суммы в строке клиента считаются только по заказам филиала;
  (б) перемещения: в карточке передаётся суженный список from_warehouses
      («Откуда»), а warehouses («Куда») остаётся полным.
Супер-админ / без филиалов — всё.
"""
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from foodcost.models import Country, Location, UserProfile, Customer, Order, Transfer


class _Base(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.l1 = Location.objects.create(country=self.country, name="Филиал 1")
        self.l2 = Location.objects.create(country=self.country, name="Филиал 2")
        self.l3 = Location.objects.create(country=self.country, name="Филиал 3")
        self.boss = self._user("boss", UserProfile.ROLE_SUPER_ADMIN, locs=[])

    def _user(self, name, role, sections=(), locs=()):
        u = User.objects.create_user(name, password="x")
        p = UserProfile.objects.create(user=u, role=role, allowed_sections=list(sections))
        p.countries.set([self.country])
        if locs:
            p.locations.set(locs)
        return u

    def _get(self, user, url):
        self.client.force_login(user)
        return self.client.get(url)


class CustomerSumsBranchScope(_Base):
    def setUp(self):
        super().setUp()
        self.c = Customer.objects.create(country=self.country, name="Клиент", phone="001")
        self._order(self.l1, "100")   # заказ в моём филиале
        self._order(self.l2, "200")   # заказ в чужом
        self.mgr = self._user("mgr", UserProfile.ROLE_ADMIN,
                              sections=[UserProfile.SECTION_CUSTOMERS], locs=[self.l1])

    def _order(self, loc, amount):
        return Order.objects.create(
            country=self.country, location=loc, customer=self.c,
            order_date=timezone.now(), status=Order.STATUS_NEW,
            total_amount=Decimal(amount), is_cancelled=False, created_by=self.boss,
        )

    def _row(self, resp):
        return next(r for r in resp.context["rows"] if r["id"] == self.c.id)

    def test_sums_scoped(self):
        resp = self._get(self.mgr, f"/c/{self.country.slug}/customers/")
        self.assertEqual(resp.status_code, 200)
        row = self._row(resp)
        self.assertEqual(row["orders_count"], 1)        # только заказ в l1
        self.assertEqual(row["total_display"], "100")   # не 300

    def test_sums_super_all(self):
        self.boss.profile.allowed_sections = [UserProfile.SECTION_CUSTOMERS]
        self.boss.profile.save()
        resp = self._get(self.boss, f"/c/{self.country.slug}/customers/")
        row = self._row(resp)
        self.assertEqual(row["orders_count"], 2)
        self.assertEqual(row["total_display"], "300")


class TransferFromSelectBranchScope(_Base):
    def setUp(self):
        super().setUp()
        self.mgr = self._user("mgr", UserProfile.ROLE_ADMIN,
                              sections=[UserProfile.SECTION_TRANSFERS], locs=[self.l1])
        self.draft = Transfer.objects.create(
            country=self.country, status=Transfer.STATUS_DRAFT,
            from_warehouse=None, to_warehouse=None,
            transfer_date=timezone.now().date(), created_by=self.mgr,
        )

    def _url(self):
        return f"/c/{self.country.slug}/transfers/{self.draft.id}/"

    def test_from_select_scoped(self):
        resp = self._get(self.mgr, self._url())
        self.assertEqual(resp.status_code, 200)
        frm = [w.id for w in resp.context["from_warehouses"]]
        self.assertEqual(frm, [self.l1.id])                       # «Откуда» — только мой филиал
        to = {w.id for w in resp.context["warehouses"]}
        self.assertEqual(to, {self.l1.id, self.l2.id, self.l3.id})  # «Куда» — все

    def test_from_select_super_all(self):
        d = Transfer.objects.create(
            country=self.country, status=Transfer.STATUS_DRAFT,
            from_warehouse=None, to_warehouse=None,
            transfer_date=timezone.now().date(), created_by=self.boss,
        )
        self.boss.profile.allowed_sections = [UserProfile.SECTION_TRANSFERS]
        self.boss.profile.save()
        resp = self._get(self.boss, f"/c/{self.country.slug}/transfers/{d.id}/")
        frm = {w.id for w in resp.context["from_warehouses"]}
        self.assertEqual(frm, {self.l1.id, self.l2.id, self.l3.id})
