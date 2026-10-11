# -*- coding: utf-8 -*-
"""
Идемпотентно подключает кабинет «Сотрудники компании»:
  1) импорт views_team + роут в foodcost/urls.py;
  2) ссылку «Сотрудники» в сайдбар base.html.
Запуск из корня проекта:  python apply_team.py
"""
import io
import sys

URLS = "foodcost/urls.py"
BASE = "foodcost/templates/foodcost/base.html"


def patch_urls():
    with io.open(URLS, "r", encoding="utf-8") as f:
        src = f.read()
    changed = False

    imp_anchor = "from . import views_settings"
    imp_line = "from . import views_team"
    if imp_line not in src:
        assert imp_anchor in src, f"нет якоря импорта: {imp_anchor!r}"
        src = src.replace(imp_anchor, imp_anchor + "\n" + imp_line, 1)
        changed = True
        print("✓ urls.py: импорт views_team добавлен")
    else:
        print("~ urls.py: импорт уже есть")

    marker = 'name="company_team"'
    anchor = "urlpatterns = ["
    block = (
        anchor + "\n"
        '    path("c/<slug:country_slug>/team/", views_team.company_team, name="company_team"),'
    )
    if marker not in src:
        assert anchor in src, f"нет якоря роутов: {anchor!r}"
        src = src.replace(anchor, block, 1)
        changed = True
        print("✓ urls.py: роут /team/ добавлен")
    else:
        print("~ urls.py: роут уже есть")

    if changed:
        with io.open(URLS, "w", encoding="utf-8") as f:
            f.write(src)


def patch_base():
    with io.open(BASE, "r", encoding="utf-8") as f:
        src = f.read()
    if "/team/' in request.path" in src:
        print("~ base.html: ссылка «Сотрудники» уже есть")
        return
    anchor = ('<a class="side-link {% if \'/settings/\' in request.path and '
              '\'/settings/homepage/\' not in request.path and \'/promotions/\' '
              'not in request.path %}active{% endif %}" '
              'href="/c/{{ country.slug }}/settings/">Настройки</a>')
    assert anchor in src, "не найден якорь ссылки «Настройки» в base.html"
    link = ('<a class="side-link {% if \'/team/\' in request.path %}active'
            '{% endif %}" href="/c/{{ country.slug }}/team/">Сотрудники</a>')
    src = src.replace(anchor, anchor + "\n\t\t\t\t\t        " + link, 1)
    with io.open(BASE, "w", encoding="utf-8") as f:
        f.write(src)
    print("✓ base.html: ссылка «Сотрудники» добавлена в сайдбар")


def main():
    patch_urls()
    patch_base()
    print("готово")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("ОШИБКА:", e)
        sys.exit(1)
