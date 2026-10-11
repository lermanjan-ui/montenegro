# -*- coding: utf-8 -*-
"""
B4: подбор ингредиентов показывает только товары-ингредиенты (и без категории),
пряча упаковку-товар, накладные (химию) и основные средства (мебель).
Проверяем хелпер ingredient_product_qs — единую точку фильтра.
"""
from django.test import TestCase

from foodcost.models import Country, Product
from foodcost.models_catalog import ProductCategory
from foodcost.accounting import ingredient_product_qs


class IngredientPickerTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")

    def _cat(self, name, acc_type):
        return ProductCategory.objects.create(
            country=self.country, name=name, accounting_type=acc_type)

    def _product(self, name, cat=None):
        return Product.objects.create(
            country=self.country, name=name, unit="pcs", category=cat)

    def test_only_ingredients_and_uncategorized(self):
        self._product("Мука", self._cat("Продукты", "ingredient"))
        self._product("Соль")  # без категории = по умолчанию ингредиент
        self._product("Коробка", self._cat("Упаковка", "packaging"))
        self._product("Химия", self._cat("Бытовая химия", "overhead"))
        self._product("Стол", self._cat("Мебель", "fixed_asset"))

        names = set(ingredient_product_qs(self.country).values_list("name", flat=True))
        self.assertIn("Мука", names)
        self.assertIn("Соль", names)          # back-compat: без категории остаётся
        self.assertNotIn("Коробка", names)    # упаковка-товар скрыта
        self.assertNotIn("Химия", names)      # накладные скрыты
        self.assertNotIn("Стол", names)       # основное средство скрыто

    def test_isolation_by_country(self):
        other = Country.objects.create(name="Другой", slug="other")
        Product.objects.create(country=other, name="Чужая мука", unit="pcs")
        names = set(ingredient_product_qs(self.country).values_list("name", flat=True))
        self.assertNotIn("Чужая мука", names)
