# -*- coding: utf-8 -*-
"""
Шаг 3.4 — аудит management-команд на арендатор-контекст (read-only).

Фоновые/CLI-задачи, которые трогают данные арендатора, должны явно резолвить
арендатора (аргумент --country/slug или .filter(country=...)). Иначе: либо
работают «без арендатора» (пустые выборки), либо глобально по всем арендаторам.

Для каждой foodcost/management/commands/*.py печатает:
  SCOPED    — есть аргумент country/slug ИЛИ .filter(country=...)/Country.objects → ок.
  GLOBAL?   — есть записи в БД (create/update/delete/save), но НЕТ ни одного
              упоминания country/slug/tenant → проверить: это платформенное
              (Module/Plan/seed) или пропущен скоуп арендатора.
  PLATFORM  — пишет только глобальные модели (Module/Plan) — ок по замыслу.
  READONLY  — нет записей в БД.

Запуск из корня проекта:  python audit_commands.py
"""
import io
import pathlib
import re
import sys

CMD_DIR = pathlib.Path("foodcost/management/commands")
WRITE_RE = re.compile(r"\.objects\.(create|bulk_create|update_or_create|get_or_create|"
                      r"update|delete)\b|\.save\(|\.delete\(")
COUNTRY_RE = re.compile(r"country|slug|tenant", re.I)
PLATFORM_MODELS = re.compile(r"\b(Module|Plan)\.objects|seed_billing")
REPORT = pathlib.Path("commands_audit_report.txt")


def main():
    if not CMD_DIR.exists():
        print(f"! нет {CMD_DIR} — запусти из корня проекта"); sys.exit(1)
    rows = {"SCOPED": [], "GLOBAL?": [], "PLATFORM": [], "READONLY": []}
    for p in sorted(CMD_DIR.glob("*.py")):
        if p.name == "__init__.py":
            continue
        s = p.read_text(encoding="utf-8")
        has_write = bool(WRITE_RE.search(s))
        has_country = bool(COUNTRY_RE.search(s))
        is_platform = bool(PLATFORM_MODELS.search(s))
        if not has_write:
            cat = "READONLY"
        elif has_country:
            cat = "SCOPED"
        elif is_platform:
            cat = "PLATFORM"
        else:
            cat = "GLOBAL?"
        rows[cat].append(p.name)

    buf = io.StringIO()
    def w(s=""): buf.write(s + "\n")
    w("=" * 64); w("АУДИТ management-команд (шаг 3.4) — арендатор-контекст"); w("=" * 64)
    for cat in ("GLOBAL?", "SCOPED", "PLATFORM", "READONLY"):
        w(f"[{cat}] {len(rows[cat])}")
        for n in rows[cat]: w("    " + n)
        w()
    w("GLOBAL? — приоритет: команда ПИШЕТ в БД, но нигде не упоминает арендатора.")
    w("Проверить: если трогает данные арендатора без скоупа — добавить аргумент")
    w("--country и .filter(country=...). Если платформенное (общие справочники) — ок.")
    text = buf.getvalue()
    print(text)
    REPORT.write_text(text, encoding="utf-8")
    print(f"\n✓ Отчёт сохранён в {REPORT} — пришли его (особенно блок GLOBAL?).")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e); sys.exit(1)
