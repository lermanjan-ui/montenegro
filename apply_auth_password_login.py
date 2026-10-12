# -*- coding: utf-8 -*-
"""
Фаза auth (шаг 1): вход по паролю + восстановление по коду.

Переключает маршруты входа в foodcost/urls.py:
  /login/              → views_auth.login_password   (e-mail/телефон/логин + пароль)
  /login/forgot/       → views_auth.forgot_password  (запрос кода)
  /login/forgot/verify/→ views_auth.forgot_verify    (код + новый пароль)
Удаляет старый OTP-вход /login/verify/ (views_auth.login_verify).

Остальное (views_auth.py и шаблоны login_password/forgot_*) кладётся из архива
как готовые файлы. Регистрация с паролем — следующий шаг (нужен signup.html).

Идемпотентно. Запуск из корня проекта: python apply_auth_password_login.py
"""
import ast
import pathlib
import sys

U = "foodcost/urls.py"

OLD = '''    path("login/", views_auth.login_otp, name="login_page"),
    path("login/verify/", views_auth.login_verify, name="login_verify"),'''

NEW = '''    path("login/", views_auth.login_password, name="login_page"),
    path("login/forgot/", views_auth.forgot_password, name="forgot_password"),
    path("login/forgot/verify/", views_auth.forgot_verify, name="forgot_verify"),'''


def main():
    p = pathlib.Path(U)
    if not p.exists():
        print(f"! не найден {U} — запусти из корня проекта"); sys.exit(1)
    s = p.read_text(encoding="utf-8")
    if NEW in s:
        print(f"~ {U}: уже применено"); return
    n = s.count(OLD)
    if n != 1:
        print(f"! якорь маршрутов входа найден {n} раз (ожидалось 1) — не меняю.\n"
              f"  Проверь, что в urls.py есть строки login_otp / login_verify."); sys.exit(1)
    s = s.replace(OLD, NEW, 1)
    ast.parse(s)
    p.write_text(s, encoding="utf-8")
    print(f"✓ {U}: вход переключён на пароль + восстановление")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e); sys.exit(1)
