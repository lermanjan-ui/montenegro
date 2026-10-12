# -*- coding: utf-8 -*-
"""
AUTH, шаг 2: пароль задаётся на РЕГИСТРАЦИИ.

Патчит foodcost/views_auth.py (версия после шага 1):
  • signup(): читает password/password2, валидирует (длина, совпадение),
    кладёт пароль в session['signup'];
  • signup_verify(): создаёт пользователя С ПАРОЛЕМ (set_password) вместо
    set_unusable_password. Старые сессии без пароля → как раньше.

Шаблон signup.html (с полями пароля) и тест кладутся из архива как готовые файлы.
Идемпотентно. Запуск из корня проекта: python apply_auth_signup_password.py
"""
import ast
import pathlib
import sys

V = "foodcost/views_auth.py"

EDITS = [
    # 1) читаем пароль и подтверждение
    ('        channel = request.POST.get("channel") or "sms"\n'
     '        form = {"company_name": name, "inn": inn, "phone": phone,\n'
     '                "email": email, "channel": channel}',
     '        channel = request.POST.get("channel") or "sms"\n'
     '        password = request.POST.get("password") or ""\n'
     '        password2 = request.POST.get("password2") or ""\n'
     '        form = {"company_name": name, "inn": inn, "phone": phone,\n'
     '                "email": email, "channel": channel}'),

    # 2) валидация пароля + сохранение в сессию
    ('        elif channel not in ("sms", "email"):\n'
     '            error = "Выбери способ получения кода"\n'
     '        else:\n'
     '            request.session["signup"] = {\n'
     '                "company_name": name, "inn": inn,\n'
     '                "phone": phone, "email": email}',
     '        elif channel not in ("sms", "email"):\n'
     '            error = "Выбери способ получения кода"\n'
     '        elif len(password) < MIN_PASSWORD_LEN:\n'
     '            error = f"Пароль должен быть не короче {MIN_PASSWORD_LEN} символов"\n'
     '        elif password != password2:\n'
     '            error = "Пароли не совпадают"\n'
     '        else:\n'
     '            request.session["signup"] = {\n'
     '                "company_name": name, "inn": inn,\n'
     '                "phone": phone, "email": email, "password": password}'),

    # 3) создаём пользователя с паролем
    ('            user = User.objects.create_user(\n'
     '                username=username, email=data["email"],\n'
     '                is_superuser=False, is_staff=False)\n'
     '            user.set_unusable_password()   # пароль задаётся через восстановление\n'
     '            user.save()',
     '            user = User.objects.create_user(\n'
     '                username=username, email=data["email"],\n'
     '                is_superuser=False, is_staff=False)\n'
     '            _pwd = data.get("password") or ""\n'
     '            if _pwd:\n'
     '                user.set_password(_pwd)        # пароль из формы регистрации\n'
     '            else:\n'
     '                user.set_unusable_password()   # старые сессии без пароля\n'
     '            user.save()'),
]


def main():
    p = pathlib.Path(V)
    if not p.exists():
        print(f"! не найден {V} — сначала примени шаг 1 (fc_auth_step1)"); sys.exit(1)
    s = p.read_text(encoding="utf-8")
    changed = 0
    for old, new in EDITS:
        if new in s:
            continue
        n = s.count(old)
        if n != 1:
            print(f"! якорь найден {n} раз (ожидалось 1):\n---\n{old[:90]}\n---"); sys.exit(1)
        s = s.replace(old, new, 1)
        changed += 1
    if changed:
        ast.parse(s)
        p.write_text(s, encoding="utf-8")
        print(f"✓ {V}: регистрация теперь задаёт пароль (правок: {changed})")
    else:
        print(f"~ {V}: уже применено")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e); sys.exit(1)
