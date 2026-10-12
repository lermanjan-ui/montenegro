# -*- coding: utf-8 -*-
"""
Шаг 3.2 — аудит точек входа на утечку межарендной изоляции (read-only).

Ничего не меняет. Запускается из КОРНЯ проекта (где manage.py):
    python audit_tenant_entrypoints.py

Что проверяет (по РЕАЛЬНОМУ коду и схеме):
  A. Вьюхи с параметром `country_slug`, в теле которых НЕТ вызова get_country(...)
     → арендатор резолвится мимо проверки доступа пользователя.
  B. Прямые запросы к «дочерним» моделям (у которых НЕТ своего FK на Country)
     в файлах вьюх: `<Model>.objects...` → изоляция держится только через
     родителя, надо убедиться, что родитель отфильтрован по арендатору.
  C. `Country.objects` с резолвом по slug ВНЕ известных helper-резолверов
     (get_country / get_public_country / _get_country_from_payload).

Вывод — список файл:строка на ручную проверку. Пишется в консоль и в
tenant_audit_report.txt.
"""
import ast
import io
import os
import pathlib
import re
import sys

APP_DIR = pathlib.Path("foodcost")
SKIP_DIRS = {"migrations", "__pycache__", "tests"}
RESOLVER_FUNCS = {"get_country", "get_public_country", "_get_country_from_payload",
                  "user_can_access_country", "get_public_country_from_payload"}
REPORT = pathlib.Path("tenant_audit_report.txt")


def detect_settings():
    mp = pathlib.Path("manage.py")
    if not mp.exists():
        return None
    m = re.search(r"DJANGO_SETTINGS_MODULE['\"]\s*,\s*['\"]([^'\"]+)['\"]",
                  mp.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def classify_models():
    """Вернёт (scopeable, child): имена классов моделей foodcost."""
    from django.apps import apps
    from django.db.models import ForeignKey, OneToOneField
    Country = apps.get_model("foodcost", "Country")
    scopeable, child = set(), set()
    for model in apps.get_app_config("foodcost").get_models():
        has_country = False
        for f in model._meta.get_fields():
            if getattr(f, "name", "") == "country":
                has_country = True; break
            if isinstance(f, (ForeignKey, OneToOneField)) and f.related_model is Country:
                has_country = True; break
        (scopeable if has_country else child).add(model.__name__)
    scopeable.add("Country")  # сам Country резолвится helper-ами
    child.discard("Country")
    return scopeable, child


def iter_py_files():
    for root, dirs, files in os.walk(APP_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".py"):
                yield pathlib.Path(root) / f


_C_RE = re.compile(r"Country\.objects[^\n]*slug")


def check_ast(path, src, out):
    """A — country_slug-вьюхи без get_country(); C — Country.objects по slug
    вне helper-резолверов (учёт охватывающей функции через AST)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return
    # собрать диапазоны строк resolver-функций, чтобы исключить их из C
    resolver_spans = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name in RESOLVER_FUNCS:
                end = getattr(node, "end_lineno", node.lineno)
                resolver_spans.append((node.lineno, end))
            # A
            argnames = [a.arg for a in node.args.args]
            if "country_slug" in argnames and node.name not in RESOLVER_FUNCS:
                seg = ast.get_source_segment(src, node) or ""
                if "get_country(" not in seg:
                    out.append(("A", f"{path}:{node.lineno}: def {node.name}(...) "
                                     f"принимает country_slug, но не вызывает get_country()"))

    def in_resolver(lineno):
        return any(a <= lineno <= b for a, b in resolver_spans)

    for i, line in enumerate(src.splitlines(), 1):
        if line.strip().startswith("#"):
            continue
        if _C_RE.search(line) and not in_resolver(i):
            out.append(("C", f"{path}:{i}: Country.objects по slug вне helper: {line.strip()[:120]}"))


def check_child_models(path, src, child_models, out):
    """B — прямые запросы к дочерним моделям (без своего FK на Country)."""
    for i, line in enumerate(src.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        for m in re.finditer(r"\b([A-Z][A-Za-z0-9_]+)\.objects\b", line):
            name = m.group(1)
            if name in child_models:
                out.append(("B", f"{path}:{i}: {name}.objects — дочерняя модель "
                                 f"(нет своего FK на Country): {stripped[:120]}"))


def main():
    settings_mod = detect_settings()
    if not settings_mod:
        print("! не нашёл DJANGO_SETTINGS_MODULE в manage.py — запусти из корня проекта"); sys.exit(1)
    if not APP_DIR.exists():
        print("! нет каталога foodcost/ — запусти из корня проекта"); sys.exit(1)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_mod)
    sys.path.insert(0, os.getcwd())
    import django
    django.setup()

    scopeable, child = classify_models()

    out = []
    for path in iter_py_files():
        # пропускаем сам резолвер-модуль models.py от B/C-ложных срабатываний не нужно,
        # но проверяем всё — это аудит
        try:
            src = path.read_text(encoding="utf-8")
        except Exception:
            continue
        check_ast(path, src, out)
        check_child_models(path, src, child, out)

    groups = {"A": [], "B": [], "C": []}
    for g, line in out:
        groups[g].append(line)

    buf = io.StringIO()
    def w(s=""): buf.write(s + "\n")

    w("=" * 70)
    w("АУДИТ ТОЧЕК ВХОДА (шаг 3.2) — read-only, ничего не менялось")
    w("=" * 70)
    w(f"Моделей со своим FK на Country (scopeable): {len(scopeable)}")
    w(f"Дочерних моделей (без своего FK на Country): {len(child)}")
    w(f"  {', '.join(sorted(child))}")
    w()
    w(f"[A] Вьюхи с country_slug без get_country(): {len(groups['A'])}")
    for l in groups["A"]: w("    " + l)
    w()
    w(f"[B] Прямые запросы к дочерним моделям (проверить скоуп через родителя): {len(groups['B'])}")
    for l in groups["B"]: w("    " + l)
    w()
    w(f"[C] Country.objects по slug вне helper-резолверов: {len(groups['C'])}")
    for l in groups["C"]: w("    " + l)
    w()
    w("Как читать: A и C — приоритетные (прямой резолв арендатора мимо проверки).")
    w("B — кандидаты: каждая строка ОК, если родитель уже отфильтрован по арендатору;")
    w("    фиксим только там, где родитель берётся без .filter(country=...).")

    text = buf.getvalue()
    print(text)
    REPORT.write_text(text, encoding="utf-8")
    print(f"\n✓ Отчёт также сохранён в {REPORT} — пришли этот файл.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        import traceback
        print("ОШИБКА:", e); traceback.print_exc(); sys.exit(1)
