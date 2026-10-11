# -*- coding: utf-8 -*-
import io, sys
URLS = "foodcost/urls.py"
IMPORT_MARKER = "from . import platform_console"
IMPORT_ANCHOR = "from . import views_payme_merge"
ROUTES_MARKER = 'name="platform_dashboard"'
ROUTES_ANCHOR = "urlpatterns = ["
ROUTES_BLOCK = """
    # ===== Консоль платформенного супер-админа =====
    path("platform/", platform_console.platform_dashboard, name="platform_dashboard"),
    path("platform/clients/new/", platform_console.platform_client_create, name="platform_client_create"),
    path("platform/clients/<slug:country_slug>/", platform_console.platform_client_detail, name="platform_client_detail"),
    path("platform/clients/<slug:country_slug>/open/", platform_console.platform_open_client, name="platform_open_client"),
"""
def main():
    with io.open(URLS, "r", encoding="utf-8") as f:
        src = f.read()
    changed = False
    if IMPORT_MARKER not in src:
        assert IMPORT_ANCHOR in src, f"нет якоря импорта: {IMPORT_ANCHOR!r}"
        src = src.replace(IMPORT_ANCHOR, IMPORT_ANCHOR + "\n" + IMPORT_MARKER, 1)
        changed = True; print("импорт добавлен")
    else:
        print("импорт уже есть")
    if ROUTES_MARKER not in src:
        assert ROUTES_ANCHOR in src, f"нет якоря роутов: {ROUTES_ANCHOR!r}"
        src = src.replace(ROUTES_ANCHOR, ROUTES_ANCHOR + "\n" + ROUTES_BLOCK, 1)
        changed = True; print("роуты добавлены")
    else:
        print("роуты уже есть")
    if changed:
        with io.open(URLS, "w", encoding="utf-8") as f:
            f.write(src)
        print("foodcost/urls.py обновлён")
    else:
        print("изменений не требуется")
if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("ОШИБКА:", e); sys.exit(1)
