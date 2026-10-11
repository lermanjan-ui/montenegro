#!/usr/bin/env python3
"""Добавляет маршрут экрана интеграций в foodcost/urls.py. Идемпотентно.
Запуск из КОРНЯ проекта: python apply_integration_route.py"""
import pathlib
p = pathlib.Path("foodcost/urls.py")
src = p.read_text(encoding="utf-8")
changed = False
imp = "from . import views_integrations"
if imp not in src:
    anchor_imp = "from . import views_settings"
    assert anchor_imp in src, "не найден импорт views_settings"
    src = src.replace(anchor_imp, anchor_imp + "\n" + imp, 1)
    changed = True
route_name = "integration_settings"
if route_name not in src:
    anchor = ('        "c/<slug:country_slug>/settings/",\n'
              '        views_settings.settings_page,\n'
              '        name="settings_page"\n'
              '    ),')
    assert anchor in src, "не найден блок маршрута settings_page"
    new = anchor + ('\n    path(\n'
                    '        "c/<slug:country_slug>/settings/integrations/",\n'
                    '        views_integrations.integration_settings_page,\n'
                    '        name="integration_settings",\n'
                    '    ),')
    src = src.replace(anchor, new, 1)
    changed = True
if changed:
    p.write_text(src, encoding="utf-8")
    print("✓ маршрут интеграций добавлен в foodcost/urls.py")
else:
    print("~ уже применено")
