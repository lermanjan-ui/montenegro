# -*- coding: utf-8 -*-
"""
Хелперы учётного типа товара. Единая точка правды, как трактовать товар
в себестоимости/учёте. Пока используется страницами каталога; дальше —
в расчёте себестоимости, складе и активах.
"""


def product_accounting_type(product):
    """Тип учёта товара: из категории, иначе 'ingredient' (back-compat)."""
    from .models_catalog import ProductCategory
    cat = getattr(product, "category", None)
    if cat is None:
        return ProductCategory.TYPE_INGREDIENT
    return cat.accounting_type


def is_fixed_asset(product):
    from .models_catalog import ProductCategory
    return product_accounting_type(product) == ProductCategory.TYPE_ASSET


def goes_into_dish_cost(product):
    """Входит ли товар в себестоимость блюда (ингредиент)."""
    from .models_catalog import ProductCategory
    return product_accounting_type(product) == ProductCategory.TYPE_INGREDIENT


def ingredient_product_qs(country):
    """Товары, пригодные как ингредиент: тип «ингредиент» ИЛИ без категории
    (по умолчанию ингредиент). Прячем упаковку-товар, накладные (химию) и
    основные средства (мебель) из подбора ингредиентов в блюде/заготовке."""
    from .models import Product
    from .models_catalog import ProductCategory
    return Product.objects.filter(country=country).exclude(
        category__accounting_type__in=[
            ProductCategory.TYPE_PACKAGING,
            ProductCategory.TYPE_OVERHEAD,
            ProductCategory.TYPE_ASSET,
        ])
