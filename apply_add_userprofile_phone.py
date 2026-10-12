# -*- coding: utf-8 -*-
"""
Добавляет поле phone в модель UserProfile (foodcost/models.py).

Зачем: код авторизации (views_auth) и тесты (test_auth_web) используют
UserProfile.phone (вход/восстановление по номеру), но поля в модели нет →
TypeError в LoginOtpTests.setUp и падение ассерта profile.phone в signup.

После применения ОБЯЗАТЕЛЬНО сделать миграцию:
    python manage.py makemigrations foodcost
    python manage.py migrate

Поле nullable-подобное (blank, default="") — миграция безопасная, данные не
теряются. Идемпотентно.
Запуск из корня проекта: python apply_add_userprofile_phone.py
"""
import ast
import pathlib
import sys

M = "foodcost/models.py"

OLD = '''    allowed_sections = models.JSONField(
        default=list,
        blank=True
    )'''

NEW = '''    allowed_sections = models.JSONField(
        default=list,
        blank=True
    )

    # Телефон пользователя кабинета (вход и восстановление по номеру).
    # Нормализованный вид "+998...". Не уникальный на уровне БД (у части
    # пользователей пусто); поиск при входе — по этому полю.
    phone = models.CharField(max_length=40, blank=True, default="", db_index=True)'''


def main():
    p = pathlib.Path(M)
    if not p.exists():
        print(f"! не найден {M} — запусти из корня проекта"); sys.exit(1)
    s = p.read_text(encoding="utf-8")
    if "phone = models.CharField(max_length=40, blank=True, default=\"\", db_index=True)" in s:
        print(f"~ {M}: поле UserProfile.phone уже есть"); return
    n = s.count(OLD)
    if n != 1:
        print(f"! якорь allowed_sections найден {n} раз (ожидалось 1) — не меняю"); sys.exit(1)
    s = s.replace(OLD, NEW, 1)
    ast.parse(s)
    p.write_text(s, encoding="utf-8")
    print(f"✓ {M}: в UserProfile добавлено поле phone. Теперь: makemigrations + migrate")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e); sys.exit(1)
