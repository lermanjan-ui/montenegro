# -*- coding: utf-8 -*-
"""
3.6 — вызвать провижининг дефолтных справочников при самрегистрации.

Патчит foodcost/views_auth.py: сразу после создания первого филиала в
signup_verify добавляет вызов provision_tenant_defaults(country).
Идемпотентно. Запуск из корня проекта: python apply_signup_provisioning.py

Файл foodcost/provisioning.py кладётся из архива как готовый модуль.
"""
import ast
import pathlib
import sys

V = pathlib.Path("foodcost/views_auth.py")
ANCHOR = '            Location.objects.create(country=country, name="Основной филиал")'
INSERT = (
    '\n            from .provisioning import provision_tenant_defaults\n'
    '            provision_tenant_defaults(country)  # дефолтные справочники нового арендатора'
)
MARKER = "provision_tenant_defaults(country)"


def main():
    if not V.exists():
        print(f"! нет {V} — запусти из корня проекта"); sys.exit(1)
    s = V.read_text(encoding="utf-8")
    if MARKER in s:
        print("~ вызов провижининга уже на месте"); return
    n = s.count(ANCHOR)
    if n != 1:
        print(f"! якорь создания филиала найден {n} раз (ожидалось 1).\n"
              f"  Пришли фрагмент signup_verify — вставлю точечно.")
        sys.exit(1)
    s = s.replace(ANCHOR, ANCHOR + INSERT, 1)
    ast.parse(s)
    V.write_text(s, encoding="utf-8")
    print("✓ signup_verify теперь досевает дефолтные справочники нового арендатора")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e); sys.exit(1)
