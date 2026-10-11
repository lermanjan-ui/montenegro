# -*- coding: utf-8 -*-
"""
Фаза 2 (списания): изоляция по филиалам.
  • /writeoffs/           — список и выпадашка складов сужены;
  • /writeoffs/analytics/ — аналитика только по филиалам пользователя.
Супер-админ и без филиалов — всё.
"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from foodcost.models import Country, Location, UserProfile, WriteOff


class WriteoffBranchScope(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.l1 = Location.objects.create(country=self.country, name="Филиал 1")
        self.l2 = Location.objects.create(country=self.country, name="Филиал 2")
        self._wo(self.l1)
        self._wo(self.l2)

    def _wo(self, loc):
        return WriteOff.objects.create(
            country=self.country, location=loc,
            item_type=WriteOff.ITEM_TYPE_PRODUCT,
            quantity=Decimal("1"), reason=WriteOff.REASON_OTHER,
            writeoff_date=datetime.date(2026, 10, 10),
        )

    def _user(self, name, role, sections, locs=()):
        u = User.objects.create_user(name, password="x")
        p = UserProfile.objects.create(user=u, role=role, allowed_sections=sections)
        p.countries.set([self.country])
        if locs:
            p.locations.set(locs)
        return u

    def test_writeoff_list_scoped(self):
        mgr = self._user("mgr", UserProfile.ROLE_ADMIN, ["writeoffs"], locs=[self.l1])
        self.client.force_login(mgr)
        resp = self.client.get(f"/c/{self.country.slug}/writeoffs/")
        self.assertEqual(resp.status_code, 200)
        locs = [w.location_id for w in resp.context["writeoffs"]]
        self.assertIn(self.l1.id, locs)
        self.assertNotIn(self.l2.id, locs)
        drop = [l.id for l in resp.context["locations"]]
        self.assertEqual(drop, [self.l1.id])

    def test_writeoff_list_super_admin_all(self):
        boss = self._user("boss", UserProfile.ROLE_SUPER_ADMIN, ["writeoffs"], locs=[])
        self.client.force_login(boss)
        resp = self.client.get(f"/c/{self.country.slug}/writeoffs/")
        self.assertEqual(resp.status_code, 200)
        locs = {w.location_id for w in resp.context["writeoffs"]}
        self.assertEqual(locs, {self.l1.id, self.l2.id})

    def test_writeoff_analytics_scoped(self):
        mgr = self._user("mgr2", UserProfile.ROLE_ADMIN, ["writeoff_analytics"], locs=[self.l1])
        self.client.force_login(mgr)
        resp = self.client.get(f"/c/{self.country.slug}/writeoffs/analytics/")
        self.assertEqual(resp.status_code, 200)
        locs = {w.location_id for w in resp.context["writeoffs"]}
        self.assertEqual(locs, {self.l1.id})
