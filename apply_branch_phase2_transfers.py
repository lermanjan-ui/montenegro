# -*- coding: utf-8 -*-
"""
Фаза 2 (СКЛАД, перемещения): ограничение по филиалам — ХАБ-правило.

Перемещение видно, если филиал пользователя — отправитель (from) ИЛИ
получатель (to). Отправлять можно только со СВОЕГО склада (from сужён и
на уровне сервера запрещён чужой), принимать — с любого (to открыт), что
нужно для доставок между филиалами и на центральную кухню.

Что меняется (views_transfers.py):
  • список (не-кухня) сужается: from∈мои ИЛИ to∈мои (+ собственные черновики);
  • KPI считаются по тому же хаб-правилу;
  • карточка чужого перемещения недоступна (redirect на список);
  • нельзя отправить со склада чужого филиала (защита POST update_header/add_item).

Выпадашка складов в фильтре списка и поле «куда» остаются полными —
это осознанно (нужны филиалы-контрагенты). Поле «откуда» ограничивается
на сервере; если нужно сузить и сам select в шаблоне — пришли
transfer_detail.html, добавлю.

Кухня: как и раньше — свои черновики, склад-источник = её точка.
Требует Фазу 1 (branch_scope.py); скрипт дописывает helper scope_any_location.
Супер-админ / без филиалов видят всё. Миграций нет. Идемпотентно.
Запуск из корня проекта: python apply_branch_phase2_transfers.py
"""
import ast
import pathlib
import sys

T = "foodcost/views_transfers.py"
SCOPE = "foodcost/branch_scope.py"

IMPORT_OLD = (
    "from .views import (\n"
    "    get_country,\n"
    "    require_section_access,\n"
    "    user_can_edit,\n"
    ")"
)
IMPORT_NEW = IMPORT_OLD + (
    "\nfrom .branch_scope import scope_any_location, user_location_ids"
)

SCOPE_ANY_FN = '''

def scope_any_location(qs, user, fields=("from_warehouse", "to_warehouse")):
    """Документ виден, если ЛЮБОЙ из складов-полей принадлежит филиалам
    пользователя (хаб-правило для перемещений). None = полный доступ."""
    ids = user_location_ids(user)
    if ids is None:
        return qs
    from django.db.models import Q
    cond = Q()
    for f in fields:
        cond |= Q(**{f"{f}__in": list(ids)})
    return qs.filter(cond)
'''

EDITS = [
    (IMPORT_OLD, IMPORT_NEW),

    # список (не-кухня): хаб-scope + собственные черновики
    ('    qs = (\n'
     '        Transfer.objects\n'
     '        .filter(country=country)\n'
     '        .select_related("from_warehouse", "to_warehouse", "created_by")\n'
     '        .annotate(items_count=Count("items"))\n'
     '    )\n',
     '    qs = (\n'
     '        Transfer.objects\n'
     '        .filter(country=country)\n'
     '        .select_related("from_warehouse", "to_warehouse", "created_by")\n'
     '        .annotate(items_count=Count("items"))\n'
     '    )\n'
     '    _ids = user_location_ids(request.user)  # None = все филиалы (супер-админ)\n'
     '    if _ids is not None:\n'
     '        qs = qs.filter(\n'
     '            Q(from_warehouse_id__in=_ids) | Q(to_warehouse_id__in=_ids)\n'
     '            | Q(created_by=request.user, status=Transfer.STATUS_DRAFT)\n'
     '        )\n'),

    # KPI: хаб-scope
    ('    confirmed = Transfer.STATUS_CONFIRMED\n'
     '    kpi_total = Transfer.objects.filter(country=country).count()\n'
     '    kpi_confirmed = Transfer.objects.filter(country=country, status=confirmed).count()\n'
     '    kpi_drafts = Transfer.objects.filter(country=country, status=Transfer.STATUS_DRAFT).count()\n'
     '    kpi_positions = TransferItem.objects.filter(\n'
     '        transfer__country=country, transfer__status=confirmed\n'
     '    ).count()',
     '    confirmed = Transfer.STATUS_CONFIRMED\n'
     '    _kpi = scope_any_location(Transfer.objects.filter(country=country), request.user)\n'
     '    kpi_total = _kpi.count()\n'
     '    kpi_confirmed = _kpi.filter(status=confirmed).count()\n'
     '    kpi_drafts = _kpi.filter(status=Transfer.STATUS_DRAFT).count()\n'
     '    kpi_positions = scope_any_location(\n'
     '        TransferItem.objects.filter(transfer__country=country, transfer__status=confirmed),\n'
     '        request.user,\n'
     '        fields=("transfer__from_warehouse", "transfer__to_warehouse"),\n'
     '    ).count()'),

    # карточка: доступ только если мой филиал — from/to, либо я автор
    ('    transfer = get_object_or_404(Transfer, id=transfer_id, country=country)\n'
     '    is_draft = transfer.status == Transfer.STATUS_DRAFT',
     '    transfer = get_object_or_404(Transfer, id=transfer_id, country=country)\n'
     '    _ids = user_location_ids(request.user)  # None = супер-админ\n'
     '    if (_ids is not None and transfer.from_warehouse_id not in _ids\n'
     '            and transfer.to_warehouse_id not in _ids\n'
     '            and transfer.created_by_id != request.user.id):\n'
     '        return redirect(f"/c/{country.slug}/transfers/")\n'
     '    is_draft = transfer.status == Transfer.STATUS_DRAFT'),

    # update_header: нельзя отправлять с чужого склада
    ('            if is_kitchen:\n'
     '                transfer.from_warehouse = kitchen_location\n'
     '            else:\n'
     '                transfer.from_warehouse = Location.objects.filter(id=from_id, country=country).first() if from_id else None\n'
     '            transfer.to_warehouse = Location.objects.filter(id=to_id, country=country).first() if to_id else None',
     '            if is_kitchen:\n'
     '                transfer.from_warehouse = kitchen_location\n'
     '            else:\n'
     '                _f = Location.objects.filter(id=from_id, country=country).first() if from_id else None\n'
     '                if _f and _ids is not None and _f.id not in _ids:\n'
     '                    _f = None  # нельзя отправлять с чужого склада\n'
     '                transfer.from_warehouse = _f\n'
     '            transfer.to_warehouse = Location.objects.filter(id=to_id, country=country).first() if to_id else None'),

    # add_item (подхват шапки): тот же запрет чужого склада-источника
    ('                if is_kitchen:\n'
     '                    transfer.from_warehouse = kitchen_location\n'
     '                else:\n'
     '                    transfer.from_warehouse = Location.objects.filter(id=hf, country=country).first() if hf else transfer.from_warehouse\n'
     '                transfer.to_warehouse = Location.objects.filter(id=ht, country=country).first() if ht else transfer.to_warehouse',
     '                if is_kitchen:\n'
     '                    transfer.from_warehouse = kitchen_location\n'
     '                else:\n'
     '                    _f = Location.objects.filter(id=hf, country=country).first() if hf else transfer.from_warehouse\n'
     '                    if _f and _ids is not None and _f.id not in _ids:\n'
     '                        _f = transfer.from_warehouse  # не переключаем на чужой склад\n'
     '                    transfer.from_warehouse = _f\n'
     '                transfer.to_warehouse = Location.objects.filter(id=ht, country=country).first() if ht else transfer.to_warehouse'),
]


def ensure_scope_any():
    p = pathlib.Path(SCOPE)
    if not p.exists():
        print(f"! нет {SCOPE} — сначала примени Фазу 1"); sys.exit(1)
    s = p.read_text(encoding="utf-8")
    if "def scope_any_location(" in s:
        return
    s = s.rstrip() + "\n" + SCOPE_ANY_FN
    ast.parse(s)
    p.write_text(s, encoding="utf-8")
    print(f"✓ {SCOPE}: добавлен helper scope_any_location")


def main():
    ensure_scope_any()
    p = pathlib.Path(T)
    if not p.exists():
        print(f"! не найден {T} — запусти из корня проекта"); sys.exit(1)
    s = p.read_text(encoding="utf-8")
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
    if changed:
        ast.parse(s)
        p.write_text(s, encoding="utf-8")
        print(f"✓ {T}: применено изменений: {changed}")
    else:
        print(f"~ {T}: уже применено")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print("ОШИБКА:", e); sys.exit(1)
