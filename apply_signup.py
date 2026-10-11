# -*- coding: utf-8 -*-
"""
Идемпотентно подключает публичную регистрацию:
  импорт views_signup + роут /signup/ в foodcost/urls.py.
Запуск из корня проекта:  python apply_signup.py
"""
import io
import sys

URLS = "foodcost/urls.py"


def main():
    with io.open(URLS, "r", encoding="utf-8") as f:
        src = f.read()
    changed = False

    imp_anchor = "from . import views_settings"
    imp_line = "from . import views_signup"
    if imp_line not in src:
        assert imp_anchor in src, f"нет якоря импорта: {imp_anchor!r}"
        src = src.replace(imp_anchor, imp_anchor + "\n" + imp_line, 1)
        changed = True
        print("✓ urls.py: импорт views_signup добавлен")
    else:
        print("~ urls.py: импорт уже есть")

    marker = 'name="signup"'
    anchor = "urlpatterns = ["
    block = (
        anchor + "\n"
        '    path("signup/", views_signup.signup, name="signup"),'
    )
    if marker not in src:
        assert anchor in src, f"нет якоря роутов: {anchor!r}"
        src = src.replace(anchor, block, 1)
        changed = True
        print("✓ urls.py: роут /signup/ добавлен")
    else:
        print("~ urls.py: роут уже есть")

    if changed:
        with io.open(URLS, "w", encoding="utf-8") as f:
            f.write(src)
    print("готово")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("ОШИБКА:", e)
        sys.exit(1)
