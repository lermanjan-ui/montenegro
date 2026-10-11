# -*- coding: utf-8 -*-
"""
Фаза 2 (смены): изоляция по филиалам.
  • передача смены (shift_handover_list) — список ограничен филиалами;
  • журнал смен (shifts_journal) — выпадашка филиалов сужена.
Супер-админ и пользователи без филиалов видят всё.
"""
import datetime

from django.contrib.auth.models import User
from django.test import TestCase

from foodcost.models import Country, Location, UserProfile, ShiftHandover


class ShiftBranchScope(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.l1 = Location.objects.create(country=self.country, name="Филиал 1")
        self.l2 = Location.objects.create(country=self.country, name="Филиал 2")

    def _user(self, name, role, sections, locs=(), countries=True):
        u = User.objects.create_user(name, password="x")
        p = UserProfile.objects.create(user=u, role=role, allowed_sections=sections)
        if countries:
            p.countries.set([self.country])
        if locs:
            p.locations.set(locs)
        return u

    def _handover(self, loc, user):
        return ShiftHandover.objects.create(
            country=self.country, location=loc,
            shift_date=datetime.date(2026, 10, 1),
            responsible=user, status=ShiftHandover.STATUS_CREATED,
        )

    def test_handover_list_scoped_to_branch(self):
        boss = self._user("boss", UserProfile.ROLE_ADMIN, ["shift_handover"], locs=[])
        self._handover(self.l1, boss)
        self._handover(self.l2, boss)
        mgr = self._user("mgr1", UserProfile.ROLE_ADMIN, ["shift_handover"], locs=[self.l1])
        self.client.force_login(mgr)
        resp = self.client.get(f"/c/{self.country.slug}/shift-handover/")
        self.assertEqual(resp.status_code, 200)
        locs = [h.location_id for h in resp.context["handovers"]]
        self.assertIn(self.l1.id, locs)
        self.assertNotIn(self.l2.id, locs)

    def test_handover_list_super_admin_sees_all(self):
        boss = self._user("boss2", UserProfile.ROLE_SUPER_ADMIN, ["shift_handover"], locs=[])
        self._handover(self.l1, boss)
        self._handover(self.l2, boss)
        self.client.force_login(boss)
        resp = self.client.get(f"/c/{self.country.slug}/shift-handover/")
        self.assertEqual(resp.status_code, 200)
        locs = [h.location_id for h in resp.context["handovers"]]
        self.assertIn(self.l1.id, locs)
        self.assertIn(self.l2.id, locs)

    def test_shifts_journal_location_dropdown_scoped(self):
        cook = self._user("cook", UserProfile.ROLE_KITCHEN_STAFF, ["shifts"], locs=[self.l1])
        self.client.force_login(cook)
        resp = self.client.get(f"/c/{self.country.slug}/shifts/")
        self.assertEqual(resp.status_code, 200)
        loc_ids = [l.id for l in resp.context["locations"]]
        self.assertEqual(loc_ids, [self.l1.id])

    def test_shifts_journal_super_admin_all_locations(self):
        boss = self._user("boss3", UserProfile.ROLE_SUPER_ADMIN, ["shifts"], locs=[])
        self.client.force_login(boss)
        resp = self.client.get(f"/c/{self.country.slug}/shifts/")
        self.assertEqual(resp.status_code, 200)
        loc_ids = sorted(l.id for l in resp.context["locations"])
        self.assertEqual(loc_ids, sorted([self.l1.id, self.l2.id]))
