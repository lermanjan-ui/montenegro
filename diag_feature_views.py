# -*- coding: utf-8 -*-
"""
Диагностика: проверяет, что все новые модули из urls.py содержат нужные функции.
Ловит разом все «has no attribute …», чтобы не чинить по одному при каждом старте.

Запуск из корня проекта (где manage.py):
    python diag_feature_views.py
Ничего не меняет — только печатает отчёт.
"""
import ast
import pathlib

NEED = {
    "views_landing":    ["landing"],
    "views_auth":       ["login_otp", "login_verify", "signup", "signup_verify"],
    "views_account":    ["my_account"],
    "views_company":    ["company_profile"],
    "views_team":       ["company_team"],
    "views_subscription": ["my_subscription"],
    "views_assets":     ["assets"],
    "views_catalog":    ["product_categories"],
    "platform_console": ["platform_dashboard", "platform_client_create",
                         "platform_client_detail", "platform_open_client",
                         "platform_team", "platform_team_remove"],
}


def funcs(path):
    t = path.read_text(encoding="utf-8")
    tree = ast.parse(t)
    return {n.name for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def main():
    base = pathlib.Path("foodcost")
    stale = []
    for mod, fns in NEED.items():
        p = base / f"{mod}.py"
        if not p.exists():
            print(f"XX  {mod:20s} — ФАЙЛ ОТСУТСТВУЕТ ({p})")
            stale.append(mod)
            continue
        try:
            have = funcs(p)
        except SyntaxError as e:
            print(f"XX  {mod:20s} — синтаксическая ошибка: {e}")
            stale.append(mod)
            continue
        miss = [f for f in fns if f not in have]
        if miss:
            print(f"XX  {mod:20s} — НЕ ХВАТАЕТ функций: {miss}")
            stale.append(mod)
        else:
            print(f"OK  {mod:20s}")
    print()
    if stale:
        print("НУЖНО ОБНОВИТЬ эти файлы (пришли список Клоду):", ", ".join(stale))
    else:
        print("Все модули на месте — urls.py поднимется.")


if __name__ == "__main__":
    main()
