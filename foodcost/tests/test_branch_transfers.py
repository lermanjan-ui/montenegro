# -*- coding: utf-8 -*-
"""
Фаза 2 (склад, перемещения): хаб-правило по филиалам.

Перемещение видно, если мой филиал = отправитель (from) ИЛИ получатель (to).
Отправлять можно только со своего склада; собственный черновик виден автору.
Супер-админ / без филиалов — всё.
"""
import datetime

from django.contrib.auth.models import User
from django.test import TestCase

from foodcost.models import Country, Location, UserProfile, Transfer

D = datetime.date(2026, 10, 10)


class TransferBranchScope(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.l1 = Location.objects.create(country=self.country, name="Филиал 1")
        self.l2 = Location.objects.create(country=self.country, name="Филиал 2")
        self.l3 = Location.objects.create(country=self.country, name="Филиал 3")
        self.boss = self._user("boss", UserProfile.ROLE_SUPER_ADMIN, locs=[])

        self.t12 = self._tr(self.l1, self.l2, "TR-2026-000001")  # from=мой(l1)
        self.t23 = self._tr(self.l2, self.l3, "TR-2026-000002")  # чужой
        self.t31 = self._tr(self.l3, self.l1, "TR-2026-000003")  # to=мой(l1)

        self.mgr = self._user("mgr", UserProfile.ROLE_ADMIN,
                              sections=[UserProfile.SECTION_TRANSFERS], locs=[self.l1])

    def _user(self, name, role, sections=(), locs=()):
        u = User.objects.create_user(name, password="x")
        p = UserProfile.objects.create(user=u, role=role, allowed_sections=list(sections))
        p.countries.set([self.country])
        if locs:
            p.locations.set(locs)
        return u

    def _tr(self, frm, to, num):
        return Transfer.objects.create(
            country=self.country, from_warehouse=frm, to_warehouse=to,
            status=Transfer.STATUS_CONFIRMED, transfer_date=D,
            document_number=num, created_by=self.boss,
        )

    def _get(self, user, url):
        self.client.force_login(user)
        return self.client.get(url)

    def _url(self):
        return f"/c/{self.country.slug}/transfers/"

    def test_list_hub_scope(self):
        resp = self._get(self.mgr, self._url())
        self.assertEqual(resp.status_code, 200)
        ids = {t.id for t in resp.context["page_obj"]}
        self.assertEqual(ids, {self.t12.id, self.t31.id})  # from l1 и to l1; не t23

    def test_list_shows_own_null_draft(self):
        mine = Transfer.objects.create(
            country=self.country, from_warehouse=None, to_warehouse=None,
            status=Transfer.STATUS_DRAFT, transfer_date=D, created_by=self.mgr,
        )
        other = Transfer.objects.create(
            country=self.country, from_warehouse=self.l2, to_warehouse=self.l3,
            status=Transfer.STATUS_DRAFT, transfer_date=D, created_by=self.boss,
        )
        resp = self._get(self.mgr, self._url())
        ids = {t.id for t in resp.context["page_obj"]}
        self.assertIn(mine.id, ids)
        self.assertNotIn(other.id, ids)

    def test_foreign_detail_redirects(self):
        resp = self._get(self.mgr, f"{self._url()}{self.t23.id}/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/transfers/", resp["Location"])

    def test_sender_detail_ok(self):
        resp = self._get(self.mgr, f"{self._url()}{self.t12.id}/")
        self.assertEqual(resp.status_code, 200)

    def test_receiver_detail_ok(self):
        resp = self._get(self.mgr, f"{self._url()}{self.t31.id}/")
        self.assertEqual(resp.status_code, 200)

    def test_super_admin_sees_all(self):
        self.boss.profile.allowed_sections = [UserProfile.SECTION_TRANSFERS]
        self.boss.profile.save()
        resp = self._get(self.boss, self._url())
        ids = {t.id for t in resp.context["page_obj"]}
        self.assertEqual(ids, {self.t12.id, self.t23.id, self.t31.id})
