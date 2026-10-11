# -*- coding: utf-8 -*-
"""
Тесты реестра активов (/c/<slug>/assets/).
Актив = товар с категорией типа «основное средство».
"""
from django.contrib.auth.models import User
from django.test import TestCase

from foodcost.models import Country, Product, ProductPrice, UserProfile
from foodcost.models_catalog import ProductCategory


class AssetsTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.url = f"/c/{self.country.slug}/assets/"
        self.admin = User.objects.create_user("cadmin", password="x")
        p = UserProfile.objects.create(
            user=self.admin, role=UserProfile.ROLE_ADMIN, allowed_sections=["products"])
        p.countries.set([self.country])

        self.asset_cat = ProductCategory.objects.create(
            country=self.country, name="Оборудование", accounting_type="fixed_asset")
        self.ingr_cat = ProductCategory.objects.create(
            country=self.country, name="Продукты", accounting_type="ingredient")

    def _product(self, name, cat, price=None):
        pr = Product.objects.create(country=self.country, name=name, unit="pcs", category=cat)
        if price is not None:
            ProductPrice.objects.create(product=pr, price=price, date_from="2026-10-01")
        return pr

    def test_opens(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_only_assets_listed(self):
        self._product("Печь", self.asset_cat, price=5000000)
        self._product("Мука", self.ingr_cat, price=12000)
        self.client.force_login(self.admin)
        resp = self.client.get(self.url)
        self.assertContains(resp, "Печь")
        self.assertNotContains(resp, "Мука")

    def test_total_value(self):
        self._product("Печь", self.asset_cat, price=5000000)
        self._product("Стол", self.asset_cat, price=800000)
        self.client.force_login(self.admin)
        resp = self.client.get(self.url)
        self.assertEqual(resp.context["count"], 2)
        self.assertEqual(int(resp.context["total"]), 5800000)

    def test_isolation(self):
        other = Country.objects.create(name="Другой", slug="other")
        u = User.objects.create_user("stranger", password="x")
        pr = UserProfile.objects.create(
            user=u, role=UserProfile.ROLE_ADMIN, allowed_sections=["products"])
        pr.countries.set([other])
        self.client.force_login(u)
        self.assertEqual(self.client.get(self.url).status_code, 404)
