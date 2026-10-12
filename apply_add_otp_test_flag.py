# -*- coding: utf-8 -*-
"""
Включает OTP_EXPOSE_CODE_FOR_TESTING в тестах: находит Django-settings
(settings.py с INSTALLED_APPS) в проекте и дописывает в конец безопасный блок:

    import sys as _sys
    if "test" in _sys.argv:
        OTP_EXPOSE_CODE_FOR_TESTING = True

Флаг активен ТОЛЬКО при `manage.py test` (прод не затрагивается). Идемпотентно.
Venv / site-packages / .git / node_modules / миграции пропускаются.

Запуск из корня проекта: python apply_add_otp_test_flag.py
"""
import os
import pathlib
import sys

SKIP_DIRS = {".git", "venv", ".venv", "env", "node_modules", "__pycache__",
             "site-packages", "migrations", "staticfiles", "static", "media"}

BLOCK = (
    "\n\n# OTP: в тестах показывать код подтверждения (каналы реально не шлют) —\n"
    "# нужно тестам авторизации (test_auth_web). Прод не затрагивается.\n"
    "import sys as _sys  # noqa: E402\n"
    'if "test" in _sys.argv:\n'
    "    OTP_EXPOSE_CODE_FOR_TESTING = True\n"
)


def find_settings():
    found = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f == "settings.py" or (f.startswith("settings") and f.endswith(".py")):
                p = pathlib.Path(root) / f
                try:
                    txt = p.read_text(encoding="utf-8")
                except Exception:
                    continue
                if "INSTALLED_APPS" in txt:
                    found.append((p, txt))
    return found


def main():
    targets = find_settings()
    if not targets:
        print("! не нашёл settings.py с INSTALLED_APPS — запусти из корня проекта,\n"
              "  либо пришли файл настроек, пропатчу точечно.")
        sys.exit(1)

    changed = 0
    for p, txt in targets:
        if "OTP_EXPOSE_CODE_FOR_TESTING" in txt:
            print(f"~ {p}: флаг уже есть")
            continue
        p.write_text(txt.rstrip() + "\n" + BLOCK, encoding="utf-8")
        print(f"✓ {p}: добавлен флаг OTP_EXPOSE_CODE_FOR_TESTING (активен только в тестах)")
        changed += 1

    if not changed:
        print("Готово: ничего менять не пришлось.")
    else:
        print(f"Готово. Изменено файлов: {changed}. Теперь: python manage.py test foodcost.tests.test_auth_web")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e); sys.exit(1)
