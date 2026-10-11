# -*- coding: utf-8 -*-
"""
Тесты категорий товаров и их типа учёта.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from foodcost.models import Country, Product, UserProfile
from foodcost.models_catalog import ProductCategory
from foodcost.accounting import product_accounting_type, is_fixed_asset


class ProductCategoryTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.url = f"/c/{self.country.slug}/product-categories/"
        self.admin = User.objects.create_user("cadmin", password="x")
        p = UserProfile.objects.create(
            user=self.admin, role=UserProfile.ROLE_ADMIN,
            allowed_sections=["products"])
        p.countries.set([self.country])

    def test_admin_opens_page(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_create_category(self):
        self.client.force_login(self.admin)
        self.client.post(self.url, {
            "action": "create", "name": "Бытовая химия",
            "accounting_type": "overhead", "allocate_evenly": "on"})
        cat = ProductCategory.objects.get(country=self.country, name="Бытовая химия")
        self.assertEqual(cat.accounting_type, "overhead")
        self.assertTrue(cat.allocate_evenly)

    def test_invalid_type_rejected(self):
        self.client.force_login(self.admin)
        resp = self.client.post(self.url, {
            "action": "create", "name": "X", "accounting_type": "bogus"})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(ProductCategory.objects.filter(name="X").exists())

    def test_accounting_helper_defaults_to_ingredient(self):
        prod = Product.objects.create(country=self.country, name="Мука", unit="kg")
        self.assertEqual(product_accounting_type(prod), "ingredient")
        self.assertFalse(is_fixed_asset(prod))

    def test_asset_category(self):
        cat = ProductCategory.objects.create(
            country=self.country, name="Оборудование", accounting_type="fixed_asset")
        prod = Product.objects.create(
            country=self.country, name="Печь", unit="pcs", category=cat)
        self.assertEqual(product_accounting_type(prod), "fixed_asset")
        self.assertTrue(is_fixed_asset(prod))

    def test_delete_keeps_products(self):
        cat = ProductCategory.objects.create(
            country=self.country, name="Расходники", accounting_type="overhead")
        prod = Product.objects.create(
            country=self.country, name="Салфетки", unit="pcs", category=cat)
        self.client.force_login(self.admin)
        self.client.post(self.url, {"action": "delete", "cat_id": cat.id})
        prod.refresh_from_db()
        self.assertIsNone(prod.category_id)  # товар остался, категория снята
        self.assertFalse(ProductCategory.objects.filter(id=cat.id).exists())

    def test_isolation(self):
        other = Country.objects.create(name="Другой", slug="other")
        u = User.objects.create_user("stranger", password="x")
        p = UserProfile.objects.create(
            user=u, role=UserProfile.ROLE_ADMIN, allowed_sections=["products"])
        p.countries.set([other])
        self.client.force_login(u)
        self.assertEqual(self.client.get(self.url).status_code, 404)
