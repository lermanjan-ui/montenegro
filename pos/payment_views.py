"""Оплата и закрытие счёта (B3-ядро).

Единый расчёт итога (_compute) используется и прекчеком, и оплатой — сумма
считается в одном месте, сервер источник истины. Пишем в аддитивные
PosOrder/PosPayment и в штатные Order (discount_amount/subtotal/total/
payment_status/status), foodcost не меняем. Деление счёта по позициям — B3.1.

1 балл лояльности = 1 денежная единица.

Эндпоинты:
  /pos/order/api/quote/   — пересчёт итога под выбранные скидку/бонус/чаевые/сервис
  /pos/order/api/loyalty/ — поиск гостя по телефону, баланс и лимит списания
  /pos/order/api/pay/     — закрыть счёт (создать PosPayment, провести оплату)
"""

from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from django.utils.translation import gettext as _
from foodcost.views import get_country
from foodcost.models import PaymentMethod, Customer

from .access import pos_require, POS_CASHIER
from .fiscalization import fiscalize_payment

from .cashier_views import _line_net, _get_pos_order, _dec, _int, ZERO
from .models import (
    ServiceTipSettings, LoyaltyProgram, CustomerPoints,
    FiscalizationSettings, PaymentMethodLocationConfig, PosPayment,
)

HUNDRED = Decimal("100")


def _require_orders(request):
    return pos_require(request.user, POS_CASHIER)


def _q2(x):
    return Decimal(x).quantize(Decimal("0.01"))


def _service_settings(country):
    return ServiceTipSettings.objects.filter(tenant=country, location__isnull=True).first()


def _loyalty_program(country):
    return LoyaltyProgram.objects.filter(tenant=country).first()


def _visible_methods(country):
    """Способы оплаты, видимые на кассе (конфиг по точке, location=null —
    общий; если конфига нет, способ видим по умолчанию)."""
    methods = list(PaymentMethod.objects.filter(country=country, is_active=True).order_by("name"))
    cfgs = {
        c.payment_method_id: c
        for c in PaymentMethodLocationConfig.objects.filter(
            location__isnull=True, payment_method__country=country,
        )
    }
    out = []
    for m in methods:
        cfg = cfgs.get(m.id)
        if cfg is not None and not cfg.is_visible:
            continue
        out.append(m)
    return out


def _compute(country, pos_order, p):
    """Расчёт итога по текущему заказу и выбранным параметрам p (dict-like).
    Возвращает словарь разбивки (все суммы — float для JSON)."""
    order = pos_order.order

    subtotal = ZERO
    for oi in order.items.select_related("dish").all():
        subtotal += _line_net(oi, getattr(oi, "pos", None))

    # --- ручная скидка на счёт ---
    disc_type = (p.get("discount_type") or "").strip()
    disc_val = _dec(p.get("discount_value"), "0")
    discount = ZERO
    if disc_type == "percent":
        pct = min(max(disc_val, ZERO), HUNDRED)
        discount = subtotal * pct / HUNDRED
    elif disc_type == "amount":
        discount = min(max(disc_val, ZERO), subtotal)
    discount = _q2(discount)

    base_after_discount = subtotal - discount

    # --- сервисный сбор (dine-in) ---
    sset = _service_settings(country)
    include_service = str(p.get("include_service", "1")).lower() not in ("0", "false", "")
    service = ZERO
    service_available = bool(sset and sset.service_enabled and sset.service_dine_in and pos_order.is_dine_in)
    service_removable = bool(sset and sset.service_allow_remove)
    if service_available and (include_service or not service_removable):
        service = _q2(base_after_discount * (sset.service_percent or ZERO) / HUNDRED)
        include_service = True
    else:
        include_service = False

    # --- бонусы лояльности ---
    prog = _loyalty_program(country)
    bonus_points = max(_int(p.get("bonus_points"), 0), 0)
    bonus = Decimal(bonus_points)  # 1 балл = 1 ед.
    bonus_capped_reason = ""
    loyalty_enabled = bool(prog and prog.enabled)
    if not loyalty_enabled:
        bonus = ZERO
        bonus_points = 0
    else:
        if discount > 0 and not prog.combine_with_discounts:
            bonus = ZERO
            bonus_points = 0
            bonus_capped_reason = _("Баллы нельзя совмещать со скидкой")
        max_bonus = _q2(base_after_discount * (prog.redeem_max_percent or ZERO) / HUNDRED)
        if bonus > max_bonus:
            bonus = max_bonus
            bonus_points = int(max_bonus)
            bonus_capped_reason = _("Списание ограничено {}% счёта").format(prog.redeem_max_percent)
    bonus = _q2(bonus)

    # --- чаевые ---
    tips = _q2(max(_dec(p.get("tips"), "0"), ZERO))

    total = subtotal - discount - bonus + service + tips
    if total < ZERO:
        total = ZERO
    total = _q2(total)

    return {
        "order_id": order.id,
        "subtotal": float(subtotal),
        "discount": float(discount),
        "discount_type": disc_type,
        "discount_value": float(disc_val),
        "service": float(service),
        "service_available": service_available,
        "service_removable": service_removable,
        "include_service": include_service,
        "service_percent": float(sset.service_percent) if sset else 0,
        "bonus": float(bonus),
        "bonus_points": bonus_points,
        "bonus_capped_reason": bonus_capped_reason,
        "loyalty_enabled": loyalty_enabled,
        "tips": float(tips),
        "total": float(total),
    }


# --------------------------------------------------------------------------- #

@login_required(login_url="/login/")
@require_POST
def api_quote(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = _require_orders(request)
    if access_error:
        return access_error
    pos_order = _get_pos_order(country, request.POST.get("order_id"))
    data = _compute(country, pos_order, request.POST)
    # способы оплаты для попапа
    data["methods"] = [
        {"id": m.id, "name": m.name, "is_cash": bool(m.is_cash)}
        for m in _visible_methods(country)
    ]
    sset = _service_settings(country)
    data["tips_presets"] = (sset.tips_presets if (sset and sset.tips_enabled) else []) or []
    data["tips_allow_custom"] = bool(sset and sset.tips_enabled and sset.tips_allow_custom)
    data["tips_enabled"] = bool(sset and sset.tips_enabled)
    return JsonResponse(data)


@login_required(login_url="/login/")
@require_POST
def api_loyalty(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = _require_orders(request)
    if access_error:
        return access_error
    pos_order = _get_pos_order(country, request.POST.get("order_id"))
    phone = (request.POST.get("phone") or "").strip()
    if not phone:
        return JsonResponse({"error": _("Укажите телефон гостя.")}, status=400)

    prog = _loyalty_program(country)
    if not (prog and prog.enabled):
        return JsonResponse({"error": _("Программа лояльности выключена.")}, status=400)

    customer = Customer.objects.filter(country=country, phone=phone).first()
    if customer is None:
        return JsonResponse({"found": False, "phone": phone})

    cp, _created = CustomerPoints.objects.get_or_create(customer=customer)
    quote = _compute(country, pos_order, request.POST)
    base = Decimal(str(quote["subtotal"])) - Decimal(str(quote["discount"]))
    max_by_pct = _q2(base * (prog.redeem_max_percent or ZERO) / HUNDRED)
    max_redeem = int(min(cp.balance, max_by_pct))
    # привяжем гостя к заказу (для начисления/списания при оплате)
    if pos_order.loyalty_customer_id != customer.id:
        pos_order.loyalty_customer = customer
        pos_order.save(update_fields=["loyalty_customer"])
    return JsonResponse({
        "found": True,
        "name": customer.name,
        "phone": phone,
        "balance": float(cp.balance),
        "max_redeem": max_redeem,
        "require_sms": bool(prog.require_sms_on_redeem),  # UI-заглушка (no-op)
    })


@login_required(login_url="/login/")
@require_POST
def api_pay(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = _require_orders(request)
    if access_error:
        return access_error
    pos_order = _get_pos_order(country, request.POST.get("order_id"))
    order = pos_order.order

    if order.items.count() == 0:
        return JsonResponse({"error": _("Счёт пуст.")}, status=400)

    quote = _compute(country, pos_order, request.POST)
    total = Decimal(str(quote["total"]))

    # тендеры: method_id[] + amount[] (параллельные списки)
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
    paid_sum = _q2(paid_sum)
    if paid_sum != total:
        return JsonResponse(
            {"error": _("Сумма оплат (%(paid)s) ≠ итогу (%(total)s).") % {"paid": paid_sum, "total": total}}, status=400,
        )

    fset = FiscalizationSettings.objects.filter(tenant=country, location__isnull=True).first()
    fiscal_status = PosPayment.FISCAL_PENDING

    with transaction.atomic():
        # суммы на заказ
        order.subtotal_amount = Decimal(str(quote["subtotal"]))
        order.discount_amount = Decimal(str(quote["discount"]))
        order.total_amount = total
        all_cash = all(m is not None and m.is_cash for m, _ in tenders)
        order.payment_status = order.PAYMENT_STATUS_CASH if all_cash else order.PAYMENT_STATUS_PAID
        order.status = order.STATUS_DONE
        order.save(update_fields=[
            "subtotal_amount", "discount_amount", "total_amount",
            "payment_status", "status",
        ])

        pos_order.service_charge_amount = Decimal(str(quote["service"]))
        pos_order.tips_amount = Decimal(str(quote["tips"]))
        pos_order.bonus_amount = Decimal(str(quote["bonus"]))
        pos_order.bonus_points = quote["bonus_points"]
        pos_order.save(update_fields=[
            "service_charge_amount", "tips_amount", "bonus_amount", "bonus_points",
        ])

        for method, amt in tenders:
            payment = PosPayment.objects.create(order=order, method=method, amount=amt)
            fiscal_status = fiscalize_payment(country, payment, settings=fset).status

        # лояльность: списание и начисление
        prog = _loyalty_program(country)
        points_redeemed = quote["bonus_points"]
        points_earned = 0
        if prog and prog.enabled and pos_order.loyalty_customer_id:
            cp, _created = CustomerPoints.objects.get_or_create(customer=pos_order.loyalty_customer)
            if points_redeemed:
                cp.balance = max(cp.balance - Decimal(points_redeemed), ZERO)
            if prog.earn_percent:
                base = Decimal(str(quote["subtotal"]))
                if prog.earn_exclude_discounted:
                    base = base - Decimal(str(quote["discount"]))
                points_earned = int(_q2(base * prog.earn_percent / HUNDRED))
                cp.balance = cp.balance + Decimal(points_earned)
            cp.save(update_fields=["balance"])

    return JsonResponse({
        "ok": True,
        "order_id": order.id,
        "total": float(total),
        "fiscal_status": fiscal_status,
        "points_redeemed": points_redeemed,
        "points_earned": points_earned,
    })
