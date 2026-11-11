"""Деление счёта (B3.1): по позициям и поровну.

Аддитивно: под-счета PosBill + привязка PosOrderItem.bill / PosPayment.bill.
В режиме деления скидки/бонусы/сервис не применяются (каждый под-счёт = свои
позиции по их ценам / равная доля итога) — проценты по под-счетам отдельно.

Заказ закрывается, когда оплачены ВСЕ под-счета.

Эндпоинты:
  /pos/order/api/split/set/    — задать деление (mode=even|positions)
  /pos/order/api/split/state/  — текущее состояние деления
  /pos/order/api/split/clear/  — убрать деление (вернуться к общей оплате)
  /pos/order/api/split/pay/    — оплатить один под-счёт
"""

import json
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from django.utils.translation import gettext as _
from foodcost.views import get_country
from foodcost.models import PaymentMethod

from .cashier_views import _line_net, _get_pos_order, _dec, _int, ZERO
from .payment_views import _require_orders, _q2, _visible_methods
from .fiscalization import fiscalize_payment
from .models import PosOrderItem, PosPayment, PosBill, FiscalizationSettings

ZERO2 = Decimal("0.00")


def _subtotal(order):
    total = ZERO
    for oi in order.items.select_related("dish").all():
        total += _line_net(oi, getattr(oi, "pos", None))
    return _q2(total)


def _bill_amount_positions(bill):
    # bill.items — это PosOrderItem (related_name у PosOrderItem.bill), берём их
    # штатные order_item для расчёта суммы строки.
    total = ZERO
    for pi in bill.items.select_related("order_item", "order_item__dish").all():
        total += _line_net(pi.order_item, pi)
    return _q2(total)


def _state(country, pos_order):
    order = pos_order.order
    subtotal = _subtotal(order)
    bills = list(pos_order.bills.all())
    items = []
    assigned = ZERO
    for oi in order.items.select_related("dish").all():
        pi = getattr(oi, "pos", None)
        net = _line_net(oi, pi)
        bill_id = pi.bill_id if pi else None
        if bill_id:
            assigned += net
        items.append({
            "id": oi.id,
            "name": oi.dish_name_snapshot or (oi.dish.name if oi.dish else "—"),
            "qty": float(oi.quantity or 0),
            "line_total": float(net),
            "bill_id": bill_id,
        })
    return {
        "order_id": order.id,
        "mode": pos_order.split_mode,
        "subtotal": float(subtotal),
        "assigned": float(_q2(assigned)),
        "unassigned": float(_q2(subtotal - assigned)),
        "items": items,
        "bills": [
            {"id": b.id, "label": b.label, "amount": float(b.amount), "is_paid": b.is_paid}
            for b in bills
        ],
        "all_paid": bool(bills) and all(b.is_paid for b in bills),
        "methods": [
            {"id": m.id, "name": m.name, "is_cash": bool(m.is_cash)}
            for m in _visible_methods(country)
        ],
    }


@login_required(login_url="/login/")
@require_POST
def api_split_state(request, country_slug):
    country = get_country(country_slug, request.user)
    err = _require_orders(request)
    if err:
        return err
    pos_order = _get_pos_order(country, request.POST.get("order_id"))
    return JsonResponse(_state(country, pos_order))


@login_required(login_url="/login/")
@require_POST
def api_split_set(request, country_slug):
    country = get_country(country_slug, request.user)
    err = _require_orders(request)
    if err:
        return err
    pos_order = _get_pos_order(country, request.POST.get("order_id"))
    order = pos_order.order
    mode = (request.POST.get("mode") or "").strip()

    if pos_order.bills.filter(is_paid=True).exists():
        return JsonResponse({"error": _("Часть под-счетов уже оплачена — делить заново нельзя.")}, status=400)

    subtotal = _subtotal(order)
    if subtotal <= ZERO:
        return JsonResponse({"error": _("Счёт пуст.")}, status=400)

    with transaction.atomic():
        # сброс прошлого деления
        PosOrderItem.objects.filter(order_item__order=order).update(bill=None)
        pos_order.bills.all().delete()

        if mode == "even":
            n = max(_int(request.POST.get("n"), pos_order.guests or 2), 1)
            share = _q2(subtotal / n)
            bills = []
            for i in range(n):
                amt = share if i < n - 1 else _q2(subtotal - share * (n - 1))
                bills.append(PosBill.objects.create(
                    pos_order=pos_order, label=_("Гость {}").format(i + 1), amount=amt, sort_order=i,
                ))
            pos_order.split_mode = pos_order.SPLIT_EVEN

        elif mode == "positions":
            # assignments: {"<item_id>": <group_index>}
            try:
                assignments = json.loads(request.POST.get("assignments") or "{}")
            except (ValueError, TypeError):
                assignments = {}
            groups = sorted({int(g) for g in assignments.values()})
            index_to_bill = {}
            for gi in groups:
                index_to_bill[gi] = PosBill.objects.create(
                    pos_order=pos_order, label=_("Счёт {}").format(gi + 1), amount=ZERO, sort_order=gi,
                )
            # привязка позиций
            for item_id, gi in assignments.items():
                pi = PosOrderItem.objects.filter(
                    order_item_id=_int(item_id), order_item__order=order,
                ).first()
                if pi:
                    pi.bill = index_to_bill.get(int(gi))
                    pi.save(update_fields=["bill"])
            # суммы под-счетов
            for b in index_to_bill.values():
                b.amount = _bill_amount_positions(b)
                b.save(update_fields=["amount"])
            pos_order.split_mode = pos_order.SPLIT_POSITIONS
        else:
            return JsonResponse({"error": _("Неизвестный режим деления.")}, status=400)

        pos_order.save(update_fields=["split_mode"])

    return JsonResponse(_state(country, pos_order))


@login_required(login_url="/login/")
@require_POST
def api_split_clear(request, country_slug):
    country = get_country(country_slug, request.user)
    err = _require_orders(request)
    if err:
        return err
    pos_order = _get_pos_order(country, request.POST.get("order_id"))
    if pos_order.bills.filter(is_paid=True).exists():
        return JsonResponse({"error": _("Есть оплаченные под-счета — отменить деление нельзя.")}, status=400)
    with transaction.atomic():
        PosOrderItem.objects.filter(order_item__order=pos_order.order).update(bill=None)
        pos_order.bills.all().delete()
        pos_order.split_mode = pos_order.SPLIT_NONE
        pos_order.save(update_fields=["split_mode"])
    return JsonResponse(_state(country, pos_order))


def _close_order_if_all_paid(country, pos_order):
    order = pos_order.order
    bills = list(pos_order.bills.all())
    if not bills or not all(b.is_paid for b in bills):
        return False
    subtotal = _subtotal(order)
    payments = list(PosPayment.objects.filter(order=order))
    all_cash = bool(payments) and all(p.method_id and p.method.is_cash for p in payments)
    order.subtotal_amount = subtotal
    order.discount_amount = ZERO2
    order.total_amount = subtotal
    order.payment_status = order.PAYMENT_STATUS_CASH if all_cash else order.PAYMENT_STATUS_PAID
    order.status = order.STATUS_DONE
    order.save(update_fields=[
        "subtotal_amount", "discount_amount", "total_amount",
        "payment_status", "status",
    ])
    return True


@login_required(login_url="/login/")
@require_POST
def api_split_pay(request, country_slug):
    country = get_country(country_slug, request.user)
    err = _require_orders(request)
    if err:
        return err
    pos_order = _get_pos_order(country, request.POST.get("order_id"))
    order = pos_order.order
    bill = get_object_or_404(PosBill, id=_int(request.POST.get("bill_id")), pos_order=pos_order)

    if bill.is_paid:
        return JsonResponse({"error": _("Под-счёт уже оплачен.")}, status=400)

    # в режиме «по позициям» нельзя закрывать, пока не распределены все позиции
    if pos_order.split_mode == pos_order.SPLIT_POSITIONS:
        unassigned = PosOrderItem.objects.filter(
            order_item__order=order, bill__isnull=True,
        ).exists()
        if unassigned:
            return JsonResponse({"error": _("Распределите все позиции по под-счетам.")}, status=400)

    method_ids = request.POST.getlist("method_id")
    amounts = request.POST.getlist("amount")
    tenders = []
    paid_sum = ZERO
    for i, mid in enumerate(method_ids):
        amt = _dec(amounts[i] if i < len(amounts) else "0", "0")
        if amt <= ZERO:
            continue
        method = PaymentMethod.objects.filter(id=_int(mid), country=country).first()
        tenders.append((method, _q2(amt)))
        paid_sum += amt
    if not tenders:
        return JsonResponse({"error": _("Не выбран способ оплаты.")}, status=400)
    if _q2(paid_sum) != _q2(bill.amount):
        return JsonResponse(
            {"error": _("Сумма оплат (%(paid)s) ≠ под-счёту (%(bill)s).") % {"paid": _q2(paid_sum), "bill": _q2(bill.amount)}},
            status=400,
        )

    fset = FiscalizationSettings.objects.filter(tenant=country, location__isnull=True).first()

    with transaction.atomic():
        for method, amt in tenders:
            payment = PosPayment.objects.create(order=order, bill=bill, method=method, amount=amt)
            fiscalize_payment(country, payment, settings=fset)
        bill.is_paid = True
        bill.paid_at = timezone.now()
        bill.save(update_fields=["is_paid", "paid_at"])
        order_closed = _close_order_if_all_paid(country, pos_order)

    state = _state(country, pos_order)
    state["order_closed"] = order_closed
    return JsonResponse(state)
