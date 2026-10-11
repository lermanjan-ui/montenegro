# -*- coding: utf-8 -*-
"""
Чинит поле UserProfile.phone: делает его nullable + дефолт "",
чтобы создание профиля без телефона не падало по NOT NULL.
Идемпотентно. Запуск из корня проекта:  python fix_phone.py
Потом:  python manage.py makemigrations foodcost && python manage.py migrate
"""
import io
import re
import sys

MODELS = "foodcost/models.py"
CANON = 'phone = models.CharField(max_length=32, blank=True, null=True, default="")'


def main():
    with io.open(MODELS, "r", encoding="utf-8") as f:
        src = f.read()

    # ищем именно наше поле (max_length=32) — у Customer.phone другая длина
    pat = re.compile(r'phone = models\.CharField\(max_length=32[^\n]*\)')
    m = pat.search(src)
    if not m:
        print("! не нашёл поле phone (max_length=32) в models.py.")
        print("  Покажи строку: grep -n 'phone = models.CharField' foodcost/models.py")
        return

    current = m.group(0)
    if current == CANON:
        print("~ поле phone уже корректно (nullable + default)")
        return

    print("было:  ", current)
    src = src[:m.start()] + CANON + src[m.end():]
    with io.open(MODELS, "w", encoding="utf-8") as f:
        f.write(src)
    print("стало: ", CANON)
    print("✓ поле phone поправлено. Теперь:")
    print("  python manage.py makemigrations foodcost && python manage.py migrate")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print("ОШИБКА:", e)
        sys.exit(1)
