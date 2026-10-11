# -*- coding: utf-8 -*-
"""
Фаза 2 (склад, часть A): изоляция по филиалам — приходы и инвентаризация.
«Филиал» = склад (Location), поле warehouse.

Приходы:   список/KPI/выпадашка сужены; чужая карточка недоступна;
           собственный черновик без склада виден владельцу.
Инвентар.: журнал/KPI/выпадашка сужены; чужой документ недоступен.
Супер-админ / без филиалов — всё.
"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase

from foodcost.models import (
    Country, Location, UserProfile, PurchaseReceipt, Inventory,
)

D = datetime.date(2026, 10, 10)


class _Base(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.l1 = Location.objects.create(country=self.country, name="Филиал 1")
        self.l2 = Location.objects.create(country=self.country, name="Филиал 2")
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


class PurchaseBranchScope(_Base):
    def setUp(self):
        super().setUp()
        self.r1 = PurchaseReceipt.objects.create(
            country=self.country, warehouse=self.l1,
            status=PurchaseReceipt.STATUS_CONFIRMED, receipt_date=D,
            total_amount=Decimal("100"), created_by=self.boss,
        )
        self.r2 = PurchaseReceipt.objects.create(
            country=self.country, warehouse=self.l2,
            status=PurchaseReceipt.STATUS_CONFIRMED, receipt_date=D,
            total_amount=Decimal("200"), created_by=self.boss,
        )
        self.mgr = self._user("mgr", UserProfile.ROLE_ADMIN,
                              sections=[UserProfile.SECTION_PURCHASES], locs=[self.l1])

    def _url(self):
        return f"/c/{self.country.slug}/purchases/"

    def test_list_scoped(self):
        resp = self._get(self.mgr, self._url())
        self.assertEqual(resp.status_code, 200)
        wh = [row["obj"].warehouse_id for row in resp.context["rows"]]
        self.assertIn(self.l1.id, wh)
        self.assertNotIn(self.l2.id, wh)
        # KPI сумма — только свой филиал (100), не 300
        self.assertEqual(resp.context["kpi_total"], 1)
        # выпадашка складов сужена
        self.assertEqual([l.id for l in resp.context["warehouses"]], [self.l1.id])

    def test_list_shows_own_null_draft(self):
        mine = PurchaseReceipt.objects.create(
            country=self.country, warehouse=None,
            status=PurchaseReceipt.STATUS_DRAFT, receipt_date=D, created_by=self.mgr,
        )
        other = PurchaseReceipt.objects.create(
            country=self.country, warehouse=None,
            status=PurchaseReceipt.STATUS_DRAFT, receipt_date=D, created_by=self.boss,
        )
        resp = self._get(self.mgr, self._url())
        ids = [row["obj"].id for row in resp.context["rows"]]
        self.assertIn(mine.id, ids)          # свой черновик без склада — виден
        self.assertNotIn(other.id, ids)      # чужой черновик без склада — нет

    def test_foreign_detail_redirects(self):
        resp = self._get(self.mgr, f"{self._url()}{self.r2.id}/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/purchases/", resp["Location"])

    def test_own_detail_ok(self):
        resp = self._get(self.mgr, f"{self._url()}{self.r1.id}/")
        self.assertEqual(resp.status_code, 200)

    def test_super_admin_sees_all(self):
        self.boss.profile.allowed_sections = [UserProfile.SECTION_PURCHASES]
        self.boss.profile.save()
        resp = self._get(self.boss, self._url())
        wh = {row["obj"].warehouse_id for row in resp.context["rows"]}
        self.assertEqual(wh, {self.l1.id, self.l2.id})
        self.assertEqual(resp.context["kpi_total"], 2)


class InventoryBranchScope(_Base):
    def setUp(self):
        super().setUp()
        self.i1 = Inventory.objects.create(
            country=self.country, warehouse=self.l1, inventory_date=D,
            status=Inventory.STATUS_CONFIRMED, created_by=self.boss,
            document_number="INV-2026-000001",
        )
        self.i2 = Inventory.objects.create(
            country=self.country, warehouse=self.l2, inventory_date=D,
            status=Inventory.STATUS_CONFIRMED, created_by=self.boss,
            document_number="INV-2026-000002",
        )
        self.mgr = self._user("imgr", UserProfile.ROLE_ADMIN,
                              sections=[UserProfile.SECTION_INVENTORY], locs=[self.l1])

    def _url(self):
        return f"/c/{self.country.slug}/inventory/"

    def test_list_scoped(self):
        resp = self._get(self.mgr, self._url())
        self.assertEqual(resp.status_code, 200)
        wh = {inv.warehouse_id for inv in resp.context["page_obj"]}
        self.assertEqual(wh, {self.l1.id})
        self.assertEqual(resp.context["kpi_total"], 1)
        self.assertEqual([l.id for l in resp.context["warehouses"]], [self.l1.id])

    def test_foreign_detail_redirects(self):
        resp = self._get(self.mgr, f"{self._url()}{self.i2.id}/")
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/inventory/", resp["Location"])

    def test_foreign_count_redirects(self):
        resp = self._get(self.mgr, f"{self._url()}{self.i2.id}/count/")
        self.assertEqual(resp.status_code, 302)

    def test_super_admin_sees_all(self):
        self.boss.profile.allowed_sections = [UserProfile.SECTION_INVENTORY]
        self.boss.profile.save()
        resp = self._get(self.boss, self._url())
        wh = {inv.warehouse_id for inv in resp.context["page_obj"]}
        self.assertEqual(wh, {self.l1.id, self.l2.id})
