# -*- coding: utf-8 -*-
"""
Идемпотентно подключает личный кабинет:
  1) импорт views_account + роут в foodcost/urls.py;
  2) ссылку «Мой профиль» в шапке base.html.
Запуск из корня проекта:  python apply_account.py
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
    imp_line = "from . import views_account"
    if imp_line not in src:
        assert imp_anchor in src, f"нет якоря импорта: {imp_anchor!r}"
        src = src.replace(imp_anchor, imp_anchor + "\n" + imp_line, 1)
        changed = True
        print("✓ urls.py: импорт views_account добавлен")
    else:
        print("~ urls.py: импорт уже есть")

    marker = 'name="my_account"'
    anchor = "urlpatterns = ["
    block = (
        anchor + "\n"
        '    path("c/<slug:country_slug>/account/", views_account.my_account, name="my_account"),'
    )
    if marker not in src:
        assert anchor in src, f"нет якоря роутов: {anchor!r}"
        src = src.replace(anchor, block, 1)
        changed = True
        print("✓ urls.py: роут /account/ добавлен")
    else:
        print("~ urls.py: роут уже есть")

    if changed:
        with io.open(URLS, "w", encoding="utf-8") as f:
            f.write(src)


def patch_base():
    with io.open(BASE, "r", encoding="utf-8") as f:
        src = f.read()
    if "/account/" in src and "Мой профиль" in src:
        print("~ base.html: ссылка «Мой профиль» уже есть")
        return
    anchor = (
        "                {% if request.user.is_authenticated %}\n"
        '                    <div class="user-box">'
    )
    assert anchor in src, "не найден якорь шапки (user-box) в base.html"
    link = (
        "                {% if request.user.is_authenticated %}\n"
        '                    {% if country %}<a href="/c/{{ country.slug }}/account/" '
        'class="side-link" style="padding:6px 10px;margin-right:6px;" '
        'title="Мой профиль">Мой профиль</a>{% endif %}\n'
        '                    <div class="user-box">'
    )
    src = src.replace(anchor, link, 1)
    with io.open(BASE, "w", encoding="utf-8") as f:
        f.write(src)
    print("✓ base.html: ссылка «Мой профиль» добавлена в шапку")


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
