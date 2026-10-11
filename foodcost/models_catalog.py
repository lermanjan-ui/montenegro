# -*- coding: utf-8 -*-
"""
Категории товаров с типом учёта.

Тип учёта категории определяет, КАК товар этой категории участвует в
себестоимости и в учёте:
  • ingredient  — ингредиент, идёт в себестоимость блюда (через техкарту);
  • packaging   — упаковка, идёт в себестоимость как упаковка;
  • overhead    — накладные/расходники (бытовая химия и т.п.): в блюдо не
                  входит; по желанию (allocate_evenly) равномерно
                  распределяется на весь выпуск;
  • fixed_asset — основное средство (столы, оборудование): не списывается
                  сразу, учитывается как актив на балансе.

Связь с товаром — через Product.category (nullable). Товар без категории
ведёт себя как раньше (считается ингредиентом) — обратная совместимость.
"""
from django.db import models

from .models import Country


class ProductCategory(models.Model):
    TYPE_INGREDIENT = "ingredient"
    TYPE_PACKAGING = "packaging"
    TYPE_OVERHEAD = "overhead"
    TYPE_ASSET = "fixed_asset"
    TYPE_CHOICES = [
        (TYPE_INGREDIENT, "Ингредиент (в себестоимость блюда)"),
        (TYPE_PACKAGING, "Упаковка (в себестоимость упаковки)"),
        (TYPE_OVERHEAD, "Накладные / расходники"),
        (TYPE_ASSET, "Основное средство (актив)"),
    ]

    country = models.ForeignKey(
        Country, on_delete=models.CASCADE, related_name="product_categories")
    name = models.CharField("Название", max_length=120)
    accounting_type = models.CharField(
        "Тип учёта", max_length=20, choices=TYPE_CHOICES, default=TYPE_INGREDIENT)
    allocate_evenly = models.BooleanField(
        "Распределять на себестоимость равномерно", default=False,
        help_text="Для накладных: раскидывать стоимость на весь выпуск")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return f"{self.name} ({self.get_accounting_type_display()})"
