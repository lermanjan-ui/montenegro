# -*- coding: utf-8 -*-
"""
Фикс падающих тестов test_integration_settings_page.*
Причина: вьюха views_integrations.integration_settings_page есть, но в urls.py
нет маршрута на страницу настроек интеграций (и модуль не импортирован) → 404.

Добавляем:
  • import views_integrations;
  • path "c/<slug>/settings/integrations/" → integration_settings_page.

Больше ничего не трогаем. Идемпотентно.
Запуск из корня проекта: python apply_fix_integrations_route.py
"""
import ast
import pathlib
import sys

U = "foodcost/urls.py"

IMPORT_OLD = "from . import views_settings"
IMPORT_NEW = "from . import views_settings\nfrom . import views_integrations"

PATH_OLD = '''    path(
        "c/<slug:country_slug>/settings/",
        views_settings.settings_page,
        name="settings_page"
    ),'''
PATH_NEW = '''    path(
        "c/<slug:country_slug>/settings/",
        views_settings.settings_page,
        name="settings_page"
    ),
    path(
        "c/<slug:country_slug>/settings/integrations/",
        views_integrations.integration_settings_page,
        name="integration_settings",
    ),'''

EDITS = [(IMPORT_OLD, IMPORT_NEW), (PATH_OLD, PATH_NEW)]


def main():
    p = pathlib.Path(U)
    if not p.exists():
        print(f"! не найден {U} — запусти из корня проекта"); sys.exit(1)
    s = p.read_text(encoding="utf-8")
    changed = 0
    for old, new in EDITS:
        if new in s:
            continue
        n = s.count(old)
        if n != 1:
            print(f"! якорь найден {n} раз (ожидалось 1):\n---\n{old[:90]}\n---"); sys.exit(1)
        s = s.replace(old, new, 1)
        changed += 1
    if changed:
        ast.parse(s)
        p.write_text(s, encoding="utf-8")
        print(f"✓ {U}: добавлен маршрут интеграций (правок: {changed})")
    else:
        print(f"~ {U}: уже применено")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e); sys.exit(1)
