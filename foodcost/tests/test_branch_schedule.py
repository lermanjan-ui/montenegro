# -*- coding: utf-8 -*-
"""
Фаза 2 (смены, хвост): график смен (schedule_page) — выпадашка филиалов
и список сотрудников сужены до филиалов пользователя.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from foodcost.models import Country, Location, Employee, UserProfile


class ScheduleBranchScope(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.l1 = Location.objects.create(country=self.country, name="Филиал 1")
        self.l2 = Location.objects.create(country=self.country, name="Филиал 2")
        Employee.objects.create(country=self.country, name="Повар 1", location=self.l1)
        Employee.objects.create(country=self.country, name="Повар 2", location=self.l2)

    def _user(self, name, role, locs=()):
        u = User.objects.create_user(name, password="x")
        p = UserProfile.objects.create(user=u, role=role, allowed_sections=["schedule"])
        p.countries.set([self.country])
        if locs:
            p.locations.set(locs)
        return u

    def test_schedule_locations_scoped(self):
        mgr = self._user("mgr", UserProfile.ROLE_ADMIN, locs=[self.l1])
        self.client.force_login(mgr)
        resp = self.client.get(f"/c/{self.country.slug}/schedule/")
        self.assertEqual(resp.status_code, 200)
        loc_ids = [l.id for l in resp.context["locations"]]
        self.assertEqual(loc_ids, [self.l1.id])
        # сотрудники в попапе — только своего филиала
        emp_names = [e.name for e in resp.context["popup_employees"]]
        self.assertIn("Повар 1", emp_names)
        self.assertNotIn("Повар 2", emp_names)

    def test_schedule_super_admin_all(self):
        boss = self._user("boss", UserProfile.ROLE_SUPER_ADMIN, locs=[])
        self.client.force_login(boss)
        resp = self.client.get(f"/c/{self.country.slug}/schedule/")
        self.assertEqual(resp.status_code, 200)
        loc_ids = sorted(l.id for l in resp.context["locations"])
        self.assertEqual(loc_ids, sorted([self.l1.id, self.l2.id]))
