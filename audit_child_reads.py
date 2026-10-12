# -*- coding: utf-8 -*-
"""
Шаг 3.2 (уточнение B) — узкий аудит: дочерние модели (без своего FK на Country),
которые ЧИТАЮТ/МЕНЯЮТ по id/pk из запроса БЕЗ скоупа по арендатору/родителю.

Это сужает широкий список B до реально рискованных мест. Ничего не меняет.
Запуск из корня проекта:  python audit_child_reads.py

Логика (AST): берём вызовы <ChildModel>.objects.get(...) / .filter(...).
  RISKY  — в аргументах есть id/pk из запроса (request.* или *_id/pk) И НЕТ ни
           'country', ни второго «родительского» ключа (кроме id/pk). Такой
           объект можно вытащить по чужому id → проверить вручную.
  REVIEW — есть id/pk из запроса, но есть и второй ключ-фильтр (похоже на скоуп
           через родителя, напр. transfer=transfer) — быстрый взгляд и ок.
  Остальные (create/bulk, без request-id, со 'country') не выводим.
"""
import ast
import io
import os
import pathlib
import re
import sys

APP_DIR = pathlib.Path("foodcost")
SKIP_DIRS = {"migrations", "__pycache__", "tests"}
REQ_RE = re.compile(r"request\.(POST|GET|data|resolver_match)|\b\w*_id\b|\bpk\b")
REPORT = pathlib.Path("child_reads_report.txt")


def detect_settings():
    mp = pathlib.Path("manage.py")
    m = mp.exists() and re.search(
        r"DJANGO_SETTINGS_MODULE['\"]\s*,\s*['\"]([^'\"]+)['\"]",
        mp.read_text(encoding="utf-8"))
    return m.group(1) if m else None


def classify_child():
    from django.apps import apps
    from django.db.models import ForeignKey, OneToOneField
    Country = apps.get_model("foodcost", "Country")
    child = set()
    for model in apps.get_app_config("foodcost").get_models():
        ok = False
        for f in model._meta.get_fields():
            if getattr(f, "name", "") == "country":
                ok = True; break
            if isinstance(f, (ForeignKey, OneToOneField)) and f.related_model is Country:
                ok = True; break
        if not ok:
            child.add(model.__name__)
    return child


def iter_py():
    for root, dirs, files in os.walk(APP_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for f in files:
            if f.endswith(".py") and "(1)" not in f:
                yield pathlib.Path(root) / f


def analyse(path, child, out):
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except Exception:
        return
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute) or fn.attr not in ("get", "filter"):
            continue
        # <Model>.objects.get/filter : fn.value is <Model>.objects (Attribute .objects)
        base = fn.value
        if not (isinstance(base, ast.Attribute) and base.attr == "objects"
                and isinstance(base.value, ast.Name)):
            continue
        model = base.value.id
        if model not in child:
            continue
        seg = ast.get_source_segment(src, node) or ""
        kw = [k.arg for k in node.keywords if k.arg]
        has_req = bool(REQ_RE.search(seg))
        has_country = "country" in seg
        other_keys = [k for k in kw if k not in ("id", "pk")]
        if not has_req or has_country:
            continue
        cat = "REVIEW" if other_keys else "RISKY"
        out.append((cat, f"{path}:{node.lineno}: {model}.objects.{fn.attr}("
                         f"{', '.join(kw)}) — {seg.splitlines()[0][:90]}"))


def main():
    sm = detect_settings()
    if not sm:
        print("! нет DJANGO_SETTINGS_MODULE в manage.py — запусти из корня"); sys.exit(1)
    if not APP_DIR.exists():
        print("! нет foodcost/ — запусти из корня проекта"); sys.exit(1)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", sm)
    sys.path.insert(0, os.getcwd())
    import django; django.setup()
    child = classify_child()

    out = []
    for p in iter_py():
        analyse(p, child, out)
    risky = [l for c, l in out if c == "RISKY"]
    review = [l for c, l in out if c == "REVIEW"]

    buf = io.StringIO()
    def w(s=""): buf.write(s + "\n")
    w("=" * 70); w("УТОЧНЁННЫЙ АУДИТ B (чтение дочерних по request-id без скоупа)"); w("=" * 70)
    w(f"[RISKY] id/pk из запроса, без country и без второго ключа: {len(risky)}")
    for l in risky: w("    " + l)
    w()
    w(f"[REVIEW] есть второй ключ-фильтр (вероятно скоуп через родителя): {len(review)}")
    for l in review: w("    " + l)
    w()
    w("RISKY — проверить: объект берётся по id из запроса; если рядом нет проверки,")
    w("что его родитель принадлежит арендатору пользователя → потенциальная утечка.")
    w("REVIEW — убедиться, что второй ключ (parent=...) уже отрезолвлен по арендатору.")
    text = buf.getvalue()
    print(text)
    REPORT.write_text(text, encoding="utf-8")
    print(f"\n✓ Отчёт сохранён в {REPORT} — пришли его.")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        import traceback; print("ОШИБКА:", e); traceback.print_exc(); sys.exit(1)
