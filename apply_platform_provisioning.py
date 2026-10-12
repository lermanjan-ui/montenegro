# -*- coding: utf-8 -*-
"""
3.6 (добор) — провижининг дефолтных справочников при создании клиента в консоли
платформы. Патчит foodcost/platform_console.py: после создания первого филиала в
platform_client_create вызывает provision_tenant_defaults(country).
Идемпотентно. Запуск из корня проекта: python apply_platform_provisioning.py
"""
import ast
import pathlib
import sys

V = pathlib.Path("foodcost/platform_console.py")
ANCHOR = "            Location.objects.create(country=country, name=location_name)"
INSERT = (
    "\n            from .provisioning import provision_tenant_defaults\n"
    "            provision_tenant_defaults(country)  # дефолтные справочники нового клиента"
)
MARKER = "provision_tenant_defaults(country)"


def main():
    if not V.exists():
        print(f"! нет {V} — запусти из корня проекта"); sys.exit(1)
    s = V.read_text(encoding="utf-8")
    if MARKER in s:
        print("~ вызов провижининга в консоли уже на месте"); return
    n = s.count(ANCHOR)
    if n != 1:
        print(f"! якорь создания филиала найден {n} раз (ожидалось 1).\n"
              f"  Пришли блок platform_client_create — вставлю точечно."); sys.exit(1)
    s = s.replace(ANCHOR, ANCHOR + INSERT, 1)
    ast.parse(s)
    V.write_text(s, encoding="utf-8")
    print("✓ platform_client_create теперь досевает дефолтные справочники клиента")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e); sys.exit(1)
