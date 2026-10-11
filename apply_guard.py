# -*- coding: utf-8 -*-
"""
Идемпотентно включает middleware применения тарифа в config/settings.py.
Запуск из корня проекта:  python apply_guard.py
"""
import io
import sys

SETTINGS = "config/settings.py"
MARKER = "foodcost.tenant_guard.TenantModuleMiddleware"
ANCHOR = "    'django.middleware.clickjacking.XFrameOptionsMiddleware',\n]"
REPLACEMENT = (
    "    'django.middleware.clickjacking.XFrameOptionsMiddleware',\n"
    "    'foodcost.tenant_guard.TenantModuleMiddleware',\n"
    "]"
)


def main():
    with io.open(SETTINGS, "r", encoding="utf-8") as f:
        src = f.read()
    if MARKER in src:
        print("~ middleware тарифа уже включён — пропускаю")
        return
    assert ANCHOR in src, "не найден конец списка MIDDLEWARE (XFrameOptions). Проверь config/settings.py"
    src = src.replace(ANCHOR, REPLACEMENT, 1)
    with io.open(SETTINGS, "w", encoding="utf-8") as f:
        f.write(src)
    print("✓ TenantModuleMiddleware добавлен в MIDDLEWARE")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print("ОШИБКА:", e)
        sys.exit(1)
