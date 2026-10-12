# -*- coding: utf-8 -*-
"""
Шаг 3.3 (добор): чистит устаревший докстринг get_public_country() в
foodcost/public_api.py — убирает обещание удалённого «uzbek»-фолбэка, чтобы
документация совпадала с кодом (неизвестный слаг → 404).

Идемпотентно. Запуск из корня проекта:  python apply_pr33_docstring.py
"""
import ast
import pathlib
import sys

F = pathlib.Path("foodcost/public_api.py")

OLD = (
    '      Order of resolution:\n'
    '      1. ?country_slug=... query param\n'
    '      2. Default "uzbekistan"\n'
    '      3. Fallback: any Country whose name contains "uzbek" (case-insensitive)\n'
)
NEW = (
    '      Order of resolution:\n'
    '      1. ?country_slug=... query param\n'
    '      2. Default "uzbekistan" (DEFAULT_COUNTRY_SLUG) when none given\n'
    '      Unknown slug -> 404 (no fuzzy fallback to another tenant).\n'
)


def main():
    if not F.exists():
        print(f"! нет {F} — запусти из корня проекта"); sys.exit(1)
    s = F.read_text(encoding="utf-8")
    if NEW in s:
        print("~ докстринг уже обновлён"); return
    n = s.count(OLD)
    if n != 1:
        print(f"! якорь докстринга найден {n} раз (ожидалось 1) — пропускаю без изменений.\n"
              f"  Вероятно, докстринг уже другой; это не критично (код уже корректен).")
        return
    s = s.replace(OLD, NEW, 1)
    ast.parse(s)
    F.write_text(s, encoding="utf-8")
    print("✓ докстринг get_public_country() приведён в соответствие с кодом")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e); sys.exit(1)
