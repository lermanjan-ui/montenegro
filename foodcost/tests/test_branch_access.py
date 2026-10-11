# -*- coding: utf-8 -*-
"""
Фаза 1 «Пользователи + Доступы»: вторая ось доступа — филиалы.
Проверяем хелпер user_location_ids / scope_by_location и что форма
создания пользователя сохраняет набор филиалов.
"""
import datetime

from django.contrib.auth.models import User
from django.test import TestCase

from foodcost.models import Country, Location, UserProfile, WriteOff
from foodcost.branch_scope import user_location_ids, scope_by_location


class BranchAccessPhase1(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.l1 = Location.objects.create(country=self.country, name="Филиал 1")
        self.l2 = Location.objects.create(country=self.country, name="Филиал 2")

    def _user(self, name, role, locs=(), location=None, is_superuser=False):
        u = User.objects.create_user(name, password="x")
        if is_superuser:
            u.is_superuser = True
            u.save()
        p = UserProfile.objects.create(user=u, role=role, location=location)
        if locs:
            p.locations.set(locs)
        return u

    def test_super_admin_sees_all(self):
        u = self._user("boss", UserProfile.ROLE_SUPER_ADMIN, [self.l1])
        self.assertIsNone(user_location_ids(u))

    def test_staff_scoped_to_assigned(self):
        u = self._user("cook", UserProfile.ROLE_KITCHEN_STAFF, [self.l1])
        self.assertEqual(user_location_ids(u), {self.l1.id})

    def test_multiple_branches(self):
        u = self._user("mgr", UserProfile.ROLE_ADMIN, [self.l1, self.l2])
        self.assertEqual(user_location_ids(u), {self.l1.id, self.l2.id})

    def test_no_branches_means_all(self):
        u = self._user("viewer", UserProfile.ROLE_VIEWER, [])
        self.assertIsNone(user_location_ids(u))

    def test_backcompat_single_location(self):
        u = self._user("legacy", UserProfile.ROLE_VIEWER, [], location=self.l2)
        self.assertEqual(user_location_ids(u), {self.l2.id})

    def test_scope_queryset_writeoffs(self):
        d = datetime.date(2026, 10, 1)
        WriteOff.objects.create(country=self.country, location=self.l1,
                                writeoff_date=d, item_type="product",
                                quantity=1, reason="other")
        WriteOff.objects.create(country=self.country, location=self.l2,
                                writeoff_date=d, item_type="product",
                                quantity=1, reason="other")
        cook = self._user("cook2", UserProfile.ROLE_KITCHEN_STAFF, [self.l1])
        qs = scope_by_location(WriteOff.objects.all(), cook)
        self.assertEqual(qs.count(), 1)
        self.assertEqual(qs.first().location_id, self.l1.id)
        # супер-админ видит оба
        boss = self._user("boss2", UserProfile.ROLE_SUPER_ADMIN, [])
        self.assertEqual(scope_by_location(WriteOff.objects.all(), boss).count(), 2)

    def test_create_user_form_saves_locations(self):
        root = self._user("root", UserProfile.ROLE_SUPER_ADMIN, [], is_superuser=True)
        self.client.force_login(root)
        self.client.post(f"/c/{self.country.slug}/users/", {
            "action": "create_user",
            "username": "newcook",
            "password": "pass123456",
            "role": UserProfile.ROLE_KITCHEN_STAFF,
            "locations": [str(self.l1.id), str(self.l2.id)],
        })
        p = User.objects.get(username="newcook").profile
        self.assertEqual(set(p.locations.values_list("id", flat=True)),
                         {self.l1.id, self.l2.id})
        self.assertEqual(p.location_id, self.l1.id)  # основной = первый выбранный
