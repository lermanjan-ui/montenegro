# -*- coding: utf-8 -*-
"""
Временный блок «Все страницы (debug)» в сайдбаре — ТОЛЬКО для суперадмина.
Ссылки на страницы, которых нет в обычном меню, чтобы по ним полазить.
Идемпотентно. Запуск из корня проекта:  python apply_devmenu.py
Удалить позже:  python apply_devmenu.py remove
"""
import io
import re
import sys

BASE = "foodcost/templates/foodcost/base.html"
START = "<!-- DEV-MENU-START"
END = "DEV-MENU-END -->"

BLOCK = """			{% if request.user.is_superuser %}
			<!-- DEV-MENU-START (временный блок «все страницы» — только суперадмин; удалить: python apply_devmenu.py remove) -->
			<details class="menu-section" open>
				<summary class="menu-title"><span class="menu-title-left">🔍 Все страницы (debug)</span><span class="menu-arrow">⌄</span></summary>
				<div class="menu-items">
					<a class="side-link" href="/c/{{ country.slug }}/orders/create/">Создание заказа</a>
					<a class="side-link" href="/c/{{ country.slug }}/customers/new/">Новый клиент (карточка)</a>
					<a class="side-link" href="/c/{{ country.slug }}/dish/create/">Создание блюда</a>
					<a class="side-link" href="/c/{{ country.slug }}/account/">Мой профиль</a>
					<a class="side-link" href="/c/{{ country.slug }}/company/">Реквизиты компании</a>
					<a class="side-link" href="/c/{{ country.slug }}/team/">Доступы (team)</a>
					<a class="side-link" href="/c/{{ country.slug }}/subscription/">Моя подписка</a>
					<a class="side-link" href="/platform/">▸ Платформа: клиенты</a>
					<a class="side-link" href="/platform/clients/new/">▸ Платформа: новый клиент</a>
					<a class="side-link" href="/platform/team/">▸ Платформа: команда</a>
					<a class="side-link" href="/welcome/" target="_blank">▸ Лендинг (/welcome/)</a>
					<a class="side-link" href="/signup/" target="_blank">▸ Регистрация</a>
					<a class="side-link" href="/login/" target="_blank">▸ Вход по коду</a>
					<a class="side-link" href="/login/password/" target="_blank">▸ Вход по паролю</a>
				</div>
			</details>
			<!-- DEV-MENU-END -->
			{% endif %}
"""


def main(remove=False):
    with io.open(BASE, "r", encoding="utf-8") as f:
        src = f.read()

    has = START in src
    if remove:
        if not has:
            print("~ блока нет — нечего удалять")
            return
        src = re.sub(
            r"\s*\{% if request\.user\.is_superuser %\}\s*<!-- DEV-MENU-START.*?DEV-MENU-END -->\s*\{% endif %\}",
            "", src, count=1, flags=re.S)
        with io.open(BASE, "w", encoding="utf-8") as f:
            f.write(src)
        print("✓ временный блок удалён")
        return

    if has:
        print("~ блок уже есть — пропускаю")
        return
    anchor = "\n\n\t\t\t</nav>"   # закрытие основного меню (не мобильного)
    assert src.count(anchor) == 1, "не найден уникальный конец сайдбара"
    src = src.replace(anchor, "\n\n" + BLOCK + "\t\t\t</nav>", 1)
    with io.open(BASE, "w", encoding="utf-8") as f:
        f.write(src)
    print("✓ временный блок «Все страницы (debug)» добавлен (виден только суперадмину)")


if __name__ == "__main__":
    try:
        main(remove=(len(sys.argv) > 1 and sys.argv[1] == "remove"))
    except AssertionError as e:
        print("ОШИБКА:", e)
        sys.exit(1)
