# -*- coding: utf-8 -*-
"""
B4 (фильтр подбора ингредиентов). Надёжно и точечно:
  • foodcost/accounting.py — добавляет хелпер ingredient_product_qs(country);
  • foodcost/views.py       — в dish_detail и preparation_detail подбор
                              ингредиентов берёт только товары-ингредиенты
                              (и без категории = по умолчанию ингредиент),
                              пряча упаковку-товар, накладные (химию) и ОС (мебель).

Расчёт себестоимости НЕ трогается. Меняется только список в выпадайке —
добавление ингредиента идёт по product_id через get_object_or_404(Product,...),
поэтому фильтр не может ничего сломать при сохранении.

Идемпотентно: повторный запуск ничего не меняет.
Запуск из корня проекта (где manage.py):  python apply_b4_picker.py
"""
import ast
import pathlib
import sys

VIEWS = "foodcost/views.py"
ACC = "foodcost/accounting.py"

HELPER = '''

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
'''

ANCHOR_A = (
    "    products = Product.objects.filter(country=country)\n"
    "    preparations = Preparation.objects.filter(country=country)\n"
    "    employees = Employee.objects.filter(country=country)\n"
    "    packagings = Packaging.objects.filter(country=country)\n"
)
REPL_A = (
    "    from .accounting import ingredient_product_qs  # B4: фильтр подбора ингредиентов\n"
    "    products = ingredient_product_qs(country)\n"
    "    preparations = Preparation.objects.filter(country=country)\n"
    "    employees = Employee.objects.filter(country=country)\n"
    "    packagings = Packaging.objects.filter(country=country)\n"
)

ANCHOR_B = (
    "    products = Product.objects.filter(country=country)\n"
    "    preparations = Preparation.objects.filter(country=country).exclude(id=preparation.id)\n"
)
REPL_B = (
    "    from .accounting import ingredient_product_qs  # B4: фильтр подбора ингредиентов\n"
    "    products = ingredient_product_qs(country)\n"
    "    preparations = Preparation.objects.filter(country=country).exclude(id=preparation.id)\n"
)


def patch_accounting():
    p = pathlib.Path(ACC)
    if not p.exists():
        print("! не найден", ACC, "— запусти из корня проекта")
        sys.exit(1)
    s = p.read_text(encoding="utf-8")
    if "def ingredient_product_qs" in s:
        print("~ accounting.py: хелпер уже есть")
        return
    s2 = s.rstrip("\n") + "\n" + HELPER
    ast.parse(s2)
    p.write_text(s2, encoding="utf-8")
    print("✓ accounting.py: добавлен ingredient_product_qs()")


def patch_views():
    p = pathlib.Path(VIEWS)
    if not p.exists():
        print("! не найден", VIEWS, "— запусти из корня проекта")
        sys.exit(1)
    s = p.read_text(encoding="utf-8")
    if "ingredient_product_qs(country)" in s:
        print("~ views.py: подбор ингредиентов уже отфильтрован")
        return

    for label, anchor, repl in (("dish_detail", ANCHOR_A, REPL_A),
                                ("preparation_detail", ANCHOR_B, REPL_B)):
        n = s.count(anchor)
        if n != 1:
            print(f"! views.py: якорь для {label} найден {n} раз (ожидалось 1) — "
                  "ничего не менял, покажи этот вывод Клоду")
            sys.exit(1)
        s = s.replace(anchor, repl, 1)

    ast.parse(s)  # не пишем сломанный файл
    p.write_text(s, encoding="utf-8")
    print("✓ views.py: подбор ингредиентов в блюде и заготовке отфильтрован")


def main():
    patch_accounting()
    patch_views()
    print("готово — расчёт себестоимости не тронут")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e)
        sys.exit(1)
