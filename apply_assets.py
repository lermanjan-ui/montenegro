# -*- coding: utf-8 -*-
"""
Подключает «Активы» (надёжно, без привязки к отступам):
  1) foodcost/urls.py — импорт views_assets + роут /assets/;
  2) base.html — ссылки «Категории товаров» (если ещё нет) и «Активы»
     в группе «Товары и склад» (мягко: если группы нет — подскажет).
Запуск из корня проекта:  python apply_assets.py
"""
import io
import sys
import pathlib

URLS = "foodcost/urls.py"
BASE = "foodcost/templates/foodcost/base.html"


def patch_urls():
    t = pathlib.Path(URLS).read_text(encoding="utf-8")
    before = t
    if "from . import views_assets" not in t:
        t = t.replace("urlpatterns = [", "from . import views_assets\n\nurlpatterns = [", 1)
    if 'name="assets"' not in t:
        t = t.replace(
            "urlpatterns = [",
            'urlpatterns = [\n    path("c/<slug:country_slug>/assets/", '
            'views_assets.assets, name="assets"),', 1)
    if t != before:
        pathlib.Path(URLS).write_text(t, encoding="utf-8")
        print("✓ urls.py: импорт и роут /assets/ добавлены")
    else:
        print("~ urls.py: уже на месте")


def patch_base():
    p = pathlib.Path(BASE)
    s = p.read_text(encoding="utf-8")
    anchor = 'href="/c/{{ country.slug }}/products/">Товары</a>'
    if anchor not in s:
        print("! base.html: группа «Товары» не найдена — примени новый base.html, "
              "затем добавь ссылки «Категории товаров» и «Активы» вручную.")
        return
    additions = ""
    if "/product-categories/" not in s:
        additions += (
            '\n\t\t\t            <a class="side-link {% if \'/product-categories/\' in request.path %}active{% endif %}" '
            'href="/c/{{ country.slug }}/product-categories/">Категории товаров</a>')
    if ">Активы</a>" not in s:
        additions += (
            '\n\t\t\t            <a class="side-link {% if \'/assets/\' in request.path %}active{% endif %}" '
            'href="/c/{{ country.slug }}/assets/">Активы</a>')
    if additions:
        s = s.replace(anchor, anchor + additions, 1)
        p.write_text(s, encoding="utf-8")
        print("✓ base.html: ссылки добавлены в группу «Товары и склад»")
    else:
        print("~ base.html: ссылки уже есть")


def main():
    patch_urls()
    patch_base()
    print("готово")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ОШИБКА:", e)
        sys.exit(1)
