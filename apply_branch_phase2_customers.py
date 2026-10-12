# -*- coding: utf-8 -*-
"""
Фаза 2 (ЗАКАЗЫ/КАССА, клиенты): ограничение по филиалам — order_views.py.
«Филиал» = Order.location (клиент «принадлежит» филиалу, если у него есть
заказ в этом филиале). Правило = user_location_ids: кассир одного филиала
видит только своих клиентов, «на все»/супер-админ — всех.

Что меняется (сужаем ВИДИМОСТЬ, безопасно и версионно-устойчиво):
  • customer_list   — в списке показываются только клиенты с заказом в
                      филиалах пользователя; KPI (всего/новые/проблемные/
                      повторные/VIP) считаются по тем же клиентам;
  • customer_detail — карточка чужого клиента недоступна (redirect на список).

Намеренно НЕ трогаем (поясняю):
  • суммы по клиенту (total_spent / средний чек в списке) остаются по всем
    филиалам — это агрегаты-аннотации, их разнос по филиалам инвазивен и
    зависит от версии; если нужно «суммы только по моему филиалу» — отдельный
    заход;
  • customer_lookup (подстановка по телефону при создании заказа) — не сужаем:
    это помощник ввода, а дедуп всё равно идёт через get_or_create(country,
    phone); сужение ломало бы подстановку для клиентов без заказов.

Якоря выбраны общими для старой и новой версий customer_list/customer_detail,
поэтому патч работает и до, и после твоего рефактора клиентов. Если функция
определена в файле дважды (старая+новая рядом) — скрипт ничего не меняет и
сообщит: сначала убери дубль.

Требует Фазу 1 (branch_scope.py). Миграций нет. Идемпотентно.
Запуск из корня проекта: python apply_branch_phase2_customers.py
"""
import ast
import pathlib
import re
import sys

O = "foodcost/order_views.py"

IMPORT_ANCHOR = "logger = logging.getLogger(__name__)"
IMPORT_LINE = "from .branch_scope import user_location_ids"

EDITS = [
    # customer_list: показываем только клиентов с заказом в филиалах пользователя
    ('    if search:\n'
     '        customers = customers.filter(\n'
     '            Q(name__icontains=search) | Q(phone__icontains=search)\n'
     '        )',
     '    _ids = user_location_ids(request.user)  # None = все филиалы (супер-админ)\n'
     '    if _ids is not None:\n'
     '        _branch_customers = Order.objects.filter(location_id__in=_ids).values("customer_id")\n'
     '        customers = customers.filter(id__in=_branch_customers)\n'
     '\n'
     '    if search:\n'
     '        customers = customers.filter(\n'
     '            Q(name__icontains=search) | Q(phone__icontains=search)\n'
     '        )'),

    # customer_list: KPI по тем же (своим) клиентам
    ('    base = Customer.objects.filter(country=country)',
     '    base = Customer.objects.filter(country=country)\n'
     '    if _ids is not None:\n'
     '        base = base.filter(id__in=Order.objects.filter(location_id__in=_ids).values("customer_id"))'),

    # customer_detail: доступ к чужому клиенту закрыт
    ('    customer = get_object_or_404(\n'
     '        Customer,\n'
     '        id=customer_id,\n'
     '        country=country,\n'
     '    )',
     '    customer = get_object_or_404(\n'
     '        Customer,\n'
     '        id=customer_id,\n'
     '        country=country,\n'
     '    )\n'
     '    _ids = user_location_ids(request.user)  # None = супер-админ / все филиалы\n'
     '    if _ids is not None and not customer.orders.filter(location_id__in=_ids).exists():\n'
     '        return redirect(f"/c/{country.slug}/customers/")'),
]


def ensure_import(s):
    # уже импортирован из branch_scope (патчами склада/заказов) — ничего не добавляем
    if re.search(r"from \.branch_scope import[^\n]*user_location_ids", s):
        return s
    if IMPORT_ANCHOR not in s:
        print(f"! не найден якорь импорта ({IMPORT_ANCHOR!r})"); sys.exit(1)
    return s.replace(IMPORT_ANCHOR, IMPORT_LINE + "\n\n" + IMPORT_ANCHOR, 1)


def main():
    p = pathlib.Path(O)
    if not p.exists():
        print(f"! не найден {O} — запусти из корня проекта"); sys.exit(1)
    s = p.read_text(encoding="utf-8")
    s = ensure_import(s)
    changed = 0
    for anchor, repl in EDITS:
        if repl in s:
            continue
        n = s.count(anchor)
        if n != 1:
            print(f"! якорь найден {n} раз (ожидалось 1), не меняю:\n---\n{anchor[:110]}\n---")
            sys.exit(1)
        s = s.replace(anchor, repl, 1)
        changed += 1
    ast.parse(s)
    p.write_text(s, encoding="utf-8")
    print(f"✓ {O}: применено правок: {changed}" if changed else f"~ {O}: уже применено")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e); sys.exit(1)
