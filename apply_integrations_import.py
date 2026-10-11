#!/usr/bin/env python3
"""Добавляет импорт модели интеграций в foodcost/models.py (чтобы Django её
зарегистрировал). Запуск из КОРНЯ проекта: python apply_integrations_import.py
Идемпотентно."""
import pathlib
p = pathlib.Path("foodcost/models.py")
src = p.read_text(encoding="utf-8")
line = "from .models_integrations import TenantIntegrationSettings  # noqa: E402,F401"
if line in src:
    print("~ импорт уже есть — пропускаю")
else:
    if not src.endswith("\n"):
        src += "\n"
    src += "\n" + line + "\n"
    p.write_text(src, encoding="utf-8")
    print("✓ импорт модели интеграций добавлен в foodcost/models.py")
