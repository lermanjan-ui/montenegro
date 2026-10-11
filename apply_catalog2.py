# -*- coding: utf-8 -*-
"""
Идемпотентно добавляет назначение категории товару:
  1) foodcost/views.py — сохранение category на карточке + передача категорий
     в контекст (категорию трогаем ТОЛЬКО если поле пришло — чтобы форма
     добавления цены её не затирала);
  2) product_detail.html — выпадающий список категории;
  3) product_list.html   — подпись категории/типа под названием товара.
Запуск из корня проекта:  python apply_catalog2.py
"""
import io
import sys

VIEWS = "foodcost/views.py"
DETAIL = "foodcost/templates/foodcost/product_detail.html"
LIST = "foodcost/templates/foodcost/product_list.html"


def patch_views():
    with io.open(VIEWS, "r", encoding="utf-8") as f:
        src = f.read()
    changed = False

    save_old = (
        '        if action == "save":\n'
        '            product.name = request.POST.get("name")\n'
        '            product.unit = request.POST.get("unit")\n'
        '            product.save()'
    )
    save_new = (
        '        if action == "save":\n'
        '            product.name = request.POST.get("name")\n'
        '            product.unit = request.POST.get("unit")\n'
        '            # категорию меняем только если поле реально пришло\n'
        '            if "category_id" in request.POST:\n'
        '                from .models_catalog import ProductCategory\n'
        '                _cid = request.POST.get("category_id") or None\n'
        '                if _cid and ProductCategory.objects.filter(id=_cid, country=country).exists():\n'
        '                    product.category_id = _cid\n'
        '                else:\n'
        '                    product.category = None\n'
        '            product.save()'
    )
    if "if \"category_id\" in request.POST:" not in src:
        assert save_old in src, "не найден якорь сохранения товара (action save)"
        src = src.replace(save_old, save_new, 1)
        changed = True
        print("✓ views.py: сохранение категории товара")
    else:
        print("~ views.py: сохранение уже есть")

    ctx_old = (
        '    return render(request, "foodcost/product_detail.html", {\n'
        '        "country": country,\n'
        '        "product": product,\n'
    )
    ctx_new = (
        '    from .models_catalog import ProductCategory\n'
        '    return render(request, "foodcost/product_detail.html", {\n'
        '        "country": country,\n'
        '        "product": product,\n'
        '        "product_categories": ProductCategory.objects.filter(country=country),\n'
    )
    if '"product_categories": ProductCategory.objects.filter(country=country),' not in src:
        assert ctx_old in src, "не найден якорь контекста product_detail"
        src = src.replace(ctx_old, ctx_new, 1)
        changed = True
        print("✓ views.py: категории в контексте карточки")
    else:
        print("~ views.py: контекст уже есть")

    if changed:
        with io.open(VIEWS, "w", encoding="utf-8") as f:
            f.write(src)


def patch_detail():
    with io.open(DETAIL, "r", encoding="utf-8") as f:
        src = f.read()
    if 'name="category_id"' in src:
        print("~ product_detail.html: селект уже есть")
        return
    anchor = (
        '                {% else %}\n'
        '                    <input type="text" value="{{ product.unit_label }}" readonly>\n'
        '                {% endif %}\n'
        '            </div>\n'
        '\n'
        '            {% if can_edit %}\n'
        '                <button type="submit">Сохранить</button>'
    )
    assert anchor in src, "не найден якорь формы товара (unit → Сохранить)"
    field = (
        '                {% else %}\n'
        '                    <input type="text" value="{{ product.unit_label }}" readonly>\n'
        '                {% endif %}\n'
        '            </div>\n'
        '\n'
        '            <div>\n'
        '                <label>Категория товара</label>\n'
        '                {% if can_edit %}\n'
        '                <select name="category_id">\n'
        '                    <option value="">— без категории (ингредиент) —</option>\n'
        '                    {% for c in product_categories %}\n'
        '                    <option value="{{ c.id }}" {% if product.category_id == c.id %}selected{% endif %}>{{ c.name }} · {{ c.get_accounting_type_display }}</option>\n'
        '                    {% endfor %}\n'
        '                </select>\n'
        '                {% else %}\n'
        '                <input type="text" value="{% if product.category %}{{ product.category.name }}{% else %}—{% endif %}" readonly>\n'
        '                {% endif %}\n'
        '            </div>\n'
        '\n'
        '            {% if can_edit %}\n'
        '                <button type="submit">Сохранить</button>'
    )
    src = src.replace(anchor, field, 1)
    with io.open(DETAIL, "w", encoding="utf-8") as f:
        f.write(src)
    print("✓ product_detail.html: селект категории добавлен")


def patch_list():
    with io.open(LIST, "r", encoding="utf-8") as f:
        src = f.read()
    if "product.category.get_accounting_type_display" in src:
        print("~ product_list.html: подпись категории уже есть")
        return
    anchor = '                <div class="product-name">{{ product.name }}</div>'
    assert anchor in src, "не найден якорь названия товара в списке"
    new = (anchor + "\n"
           '                {% if product.category %}<div style="font-size:12px;color:var(--muted);margin-top:2px;">'
           '{{ product.category.name }} · {{ product.category.get_accounting_type_display }}</div>{% endif %}')
    src = src.replace(anchor, new, 1)
    with io.open(LIST, "w", encoding="utf-8") as f:
        f.write(src)
    print("✓ product_list.html: подпись категории добавлена")


def main():
    patch_views()
    patch_detail()
    patch_list()
    print("готово")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("ОШИБКА:", e)
        sys.exit(1)
