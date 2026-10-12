# -*- coding: utf-8 -*-
"""
Шаг 3.5 — аудит использований is_superuser (read-only, ничего не меняет).

Цель 3.5: «админ клиента» не должен быть Django-суперюзером, поэтому проверки
вида `is_superuser` там, где имелся в виду «админ арендатора», надо дополнить
проверкой РОЛИ (UserProfile.ROLE_ADMIN и т.п.).

Скрипт классифицирует каждое вхождение is_superuser:
  [PLATFORM] — в platform_console.py: доступ к консоли платформы, трогать НЕ надо.
  [COMBINED] — рядом (±3 строки) есть проверка роли (role ==, ROLE_, profile.role,
               user_can_*) → уже корректно, НЕ кандидат.
  [ALONE]    — проверки роли рядом НЕТ → КАНДИДАТ на 3.5 (разобрать вручную:
               это «платформенный» доступ (оставить) или «админ арендатора»
               (добавить роль)).

Запуск из корня проекта:  python audit_superuser.py
Пишет в консоль и в superuser_audit_report.txt.
"""
import io
import os
import pathlib
import re

APP_DIR = pathlib.Path("foodcost")
SKIP_DIRS = {"migrations", "__pycache__", "tests"}
ROLE_HINT = re.compile(r"role\s*==|role\s+in|ROLE_|profile\.role|user_can_|is_platform_|is_staff|is_super_admin|\.is_[a-z_]*admin|can_access_country|can_edit|can_access_section|user_profiles__user")
REPORT = pathlib.Path("superuser_audit_report.txt")


def iter_py():
    for root, dirs, files in os.walk(APP_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".py"):
                yield pathlib.Path(root) / f


def main():
    rows = {"PLATFORM": [], "COMBINED": [], "ALONE": []}
    for path in iter_py():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        is_platform = path.name == "platform_console.py"
        for i, line in enumerate(lines):
            if "is_superuser" not in line:
                continue
            if line.strip().startswith("#"):
                continue
            window = "\n".join(lines[max(0, i - 6):i + 7])
            if is_platform:
                cat = "PLATFORM"
            elif ROLE_HINT.search(window):
                cat = "COMBINED"
            else:
                cat = "ALONE"
            rows[cat].append(f"{path}:{i+1}: {line.strip()[:120]}")

    buf = io.StringIO()
    def w(s=""): buf.write(s + "\n")
    w("=" * 70)
    w("АУДИТ is_superuser (шаг 3.5) — read-only")
    w("=" * 70)
    w(f"[ALONE]  кандидаты (нет роли рядом): {len(rows['ALONE'])}")
    for l in rows["ALONE"]: w("    " + l)
    w()
    w(f"[COMBINED] уже со связкой роли (ок): {len(rows['COMBINED'])}")
    for l in rows["COMBINED"]: w("    " + l)
    w()
    w(f"[PLATFORM] доступ к консоли платформы (не трогать): {len(rows['PLATFORM'])}")
    for l in rows["PLATFORM"]: w("    " + l)
    w()
    w("Разбор ALONE: helper-ы доступа (user_can_access_country/edit/section) —")
    w("оставляем superuser-ветку, но проверяем, что НИЖЕ есть ветка роли для")
    w("админа арендатора. Остальные ALONE — решаем точечно: платформенное (оставить)")
    w("или админ арендатора (добавить ROLE_ADMIN).")

    text = buf.getvalue()
    print(text)
    REPORT.write_text(text, encoding="utf-8")
    print(f"\n✓ Отчёт сохранён в {REPORT} — пришли его.")


if __name__ == "__main__":
    import sys
    try:
        if not APP_DIR.exists():
            print("! нет foodcost/ — запусти из корня проекта"); sys.exit(1)
        main()
    except Exception as e:
        print("ОШИБКА:", e); sys.exit(1)
