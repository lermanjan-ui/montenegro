# -*- coding: utf-8 -*-
"""
Фаза 2 (склад, остатки): изоляция по филиалам — views_stock.py.
«Филиал» = склад (Location), поле StockMovement.warehouse.

Все остатки считаются из StockMovement, поэтому проверяем сам срез данных
(журнал движений — без зависимости от справочника товаров) и сужение выпадашек.
Классы с товаром проверяют таблицу остатков и карточку товара.
Супер-админ / без филиалов — всё.
"""
import datetime
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from foodcost.models import Country, Location, UserProfile, StockMovement

DT = timezone.now()


class _Base(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.l1 = Location.objects.create(country=self.country, name="Филиал 1")
        self.l2 = Location.objects.create(country=self.country, name="Филиал 2")
        self.l3 = Location.objects.create(country=self.country, name="Филиал 3")
        self.boss = self._user("boss", UserProfile.ROLE_SUPER_ADMIN, locs=[])
        self.mgr = self._user("mgr", UserProfile.ROLE_ADMIN,
                              sections=[UserProfile.SECTION_STOCK], locs=[self.l1])

    def _user(self, name, role, sections=(), locs=()):
        u = User.objects.create_user(name, password="x")
        p = UserProfile.objects.create(user=u, role=role, allowed_sections=list(sections))
        p.countries.set([self.country])
        if locs:
            p.locations.set(locs)
        return u

    def _mv(self, warehouse, qty, product=None):
        return StockMovement.objects.create(
            country=self.country, warehouse=warehouse,
            item_type=StockMovement.ITEM_TYPE_PRODUCT, product=product,
            quantity_delta=Decimal(qty),
            movement_type=StockMovement.TYPE_RECEIPT,
            movement_datetime=DT,
        )

    def _get(self, user, url):
        self.client.force_login(user)
        return self.client.get(url)


class StockMovementsBranchScope(_Base):
    """Журнал движений — без товаров (product=None), чистая проверка среза."""
    def setUp(self):
        super().setUp()
        self._mv(self.l1, 10)
        self._mv(self.l2, 5)

    def _url(self):
        return f"/c/{self.country.slug}/stock/movements/"

    def test_scoped(self):
        resp = self._get(self.mgr, self._url())
        self.assertEqual(resp.status_code, 200)
        wh = {m.warehouse_id for m in resp.context["page_obj"]}
        self.assertEqual(wh, {self.l1.id})
        self.assertEqual(resp.context["kpi_total"], 1)
        self.assertEqual([w.id for w in resp.context["warehouses"]], [self.l1.id])

    def test_super_admin_all(self):
        self.boss.profile.allowed_sections = [UserProfile.SECTION_STOCK]
        self.boss.profile.save()
        resp = self._get(self.boss, self._url())
        wh = {m.warehouse_id for m in resp.context["page_obj"]}
        self.assertEqual(wh, {self.l1.id, self.l2.id})
        self.assertEqual(resp.context["kpi_total"], 2)


class StockListDropdownBranchScope(_Base):
    """Остатки: выпадашка складов сужена, страница открывается."""
    def test_locations_scoped(self):
        resp = self._get(self.mgr, f"/c/{self.country.slug}/stock/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([l.id for l in resp.context["locations"]], [self.l1.id])

    def test_super_admin_all_locations(self):
        self.boss.profile.allowed_sections = [UserProfile.SECTION_STOCK]
        self.boss.profile.save()
        resp = self._get(self.boss, f"/c/{self.country.slug}/stock/")
        self.assertEqual({l.id for l in resp.context["locations"]},
                         {self.l1.id, self.l2.id, self.l3.id})


class StockListBalancesBranchScope(_Base):
    """Таблица остатков и карточка товара — с реальным товаром.

    Если Product в проекте требует иных полей — поправь фабрику _product().
    """
    def setUp(self):
        super().setUp()
        from foodcost.models import Product
        self.product = Product.objects.create(
            country=self.country, name="Мука", sku="MUKA", unit="kg",
            minimum_stock=Decimal("0"),
        )
        self._mv(self.l1, 10, product=self.product)
        self._mv(self.l2, 5, product=self.product)

    def test_list_rows_scoped(self):
        resp = self._get(self.mgr, f"/c/{self.country.slug}/stock/")
        self.assertEqual(resp.status_code, 200)
        wh = {r["warehouse_id"] for r in resp.context["page_obj"]}
        self.assertEqual(wh, {self.l1.id})
        self.assertEqual(resp.context["kpi_positions"], 1)

    def test_list_rows_super_all(self):
        self.boss.profile.allowed_sections = [UserProfile.SECTION_STOCK]
        self.boss.profile.save()
        resp = self._get(self.boss, f"/c/{self.country.slug}/stock/")
        wh = {r["warehouse_id"] for r in resp.context["page_obj"]}
        self.assertEqual(wh, {self.l1.id, self.l2.id})

    def test_product_detail_scoped(self):
        resp = self._get(self.mgr, f"/c/{self.country.slug}/stock/product/{self.product.id}/")
        self.assertEqual(resp.status_code, 200)
        whs = {r["warehouse"] for r in resp.context["wh_rows"]}
        self.assertEqual(whs, {self.l1.name})          # только мой филиал
        self.assertEqual(resp.context["total_qty_display"], "10")  # не 15
