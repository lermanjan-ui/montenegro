# -*- coding: utf-8 -*-
"""
Тесты назначения категории товару на его карточке.
Главное — форма добавления цены (action=save без category_id) НЕ затирает категорию.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from foodcost.models import Country, Product, UserProfile
from foodcost.models_catalog import ProductCategory


class ProductCategoryAssignTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.admin = User.objects.create_user("cadmin", password="x")
        p = UserProfile.objects.create(
            user=self.admin, role=UserProfile.ROLE_ADMIN, allowed_sections=["products"])
        p.countries.set([self.country])
        self.cat = ProductCategory.objects.create(
            country=self.country, name="Упаковка", accounting_type="packaging")
        self.prod = Product.objects.create(country=self.country, name="Стакан", unit="pcs")
        self.url = f"/c/{self.country.slug}/products/{self.prod.id}/"
        self.client.force_login(self.admin)

    def test_assign_category(self):
        self.client.post(self.url, {
            "action": "save", "name": "Стакан", "unit": "pcs",
            "category_id": self.cat.id})
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.category_id, self.cat.id)

    def test_clear_category(self):
        self.prod.category = self.cat
        self.prod.save()
        self.client.post(self.url, {
            "action": "save", "name": "Стакан", "unit": "pcs", "category_id": ""})
        self.prod.refresh_from_db()
        self.assertIsNone(self.prod.category_id)

    def test_price_form_does_not_wipe_category(self):
        # форма добавления цены шлёт action=save без category_id
        self.prod.category = self.cat
        self.prod.save()
        self.client.post(self.url, {
            "action": "save", "name": "Стакан", "unit": "pcs",
            "price": "1000", "date": "2026-10-01"})
        self.prod.refresh_from_db()
        self.assertEqual(self.prod.category_id, self.cat.id)  # категория цела

    def test_foreign_category_ignored(self):
        other = Country.objects.create(name="Другой", slug="other")
        foreign = ProductCategory.objects.create(
            country=other, name="Чужая", accounting_type="ingredient")
        self.client.post(self.url, {
            "action": "save", "name": "Стакан", "unit": "pcs",
            "category_id": foreign.id})
        self.prod.refresh_from_db()
        self.assertIsNone(self.prod.category_id)  # чужую категорию не присвоить
