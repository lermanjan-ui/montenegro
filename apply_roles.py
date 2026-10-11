# -*- coding: utf-8 -*-
"""
Восстанавливает роли платформы в foodcost/models.py (надёжно, без привязки к
отступам). Нужно, потому что platform_console.py обращается к
UserProfile.ROLE_SUPER_ADMIN и UserProfile.ROLE_PLATFORM_MANAGER — без них
вкладка «Платформа» падает.

Делает ровно то, чего не хватает, и ничего больше:
  1) добавляет константы ROLE_SUPER_ADMIN / ROLE_PLATFORM_MANAGER в UserProfile;
  2) добавляет их в ROLE_CHOICES (чтобы были видны в выборе роли).

Идемпотентно: повторный запуск ничего не меняет.
Запуск из корня проекта:  python apply_roles.py
"""
import pathlib
import re
import sys

MODELS = "foodcost/models.py"

ADD_CONSTS = [
    ('ROLE_SUPER_ADMIN', '"super_admin"', 'Супер-админ платформы'),
    ('ROLE_PLATFORM_MANAGER', '"platform_manager"', 'Менеджер платформы'),
]


def main():
    p = pathlib.Path(MODELS)
    if not p.exists():
        print("! не найден", MODELS, "— запусти из корня проекта")
        sys.exit(1)
    s = p.read_text(encoding="utf-8")
    orig = s

    # --- найти класс UserProfile ---
    m = re.search(r"\nclass UserProfile\(models\.Model\):\n", s)
    if not m:
        print("! класс UserProfile не найден — проверь models.py вручную")
        sys.exit(1)
    class_start = m.end()

    # границы тела класса: до следующего top-level 'class ' / 'def ' в колонке 0
    rest = s[class_start:]
    nxt = re.search(r"\n(class |def )", rest)
    body_end = class_start + (nxt.start() if nxt else len(rest))
    body = s[class_start:body_end]

    # 1) константы
    new_consts = ""
    for const, val, _label in ADD_CONSTS:
        if not re.search(rf"\b{const}\s*=", body):
            new_consts += f"    {const} = {val}\n"
    if new_consts:
        s = s[:class_start] + new_consts + s[class_start:]
        # сдвинулись границы — пересчитаем тело
        body_end += len(new_consts)
        body = s[class_start:body_end]

    # 2) ROLE_CHOICES — вставить недостающие пары после открывающей скобки
    cm = re.search(r"ROLE_CHOICES\s*=\s*[\[(]", body)
    if cm:
        insert_at = class_start + cm.end()
        add_pairs = ""
        for const, _val, label in ADD_CONSTS:
            if f"({const}," not in body and f"({const} ," not in body:
                add_pairs += f'\n        ({const}, "{label}"),'
        if add_pairs:
            s = s[:insert_at] + add_pairs + s[insert_at:]
    else:
        print("~ ROLE_CHOICES не найден — константы добавлены, "
              "но в выпадающий список роль не попадёт (не критично).")

    if s != orig:
        p.write_text(s, encoding="utf-8")
        print("✓ models.py: роли платформы восстановлены "
              "(ROLE_SUPER_ADMIN / ROLE_PLATFORM_MANAGER)")
    else:
        print("~ models.py: роли уже на месте — ничего не менял")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ОШИБКА:", e)
        sys.exit(1)
