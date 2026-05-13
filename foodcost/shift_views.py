from datetime import timedelta
from decimal import Decimal

import json
import urllib.request
from html import escape

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.conf import settings


from .models import (
    UserProfile,
    Product,
    Preparation,
    Dish,
    Location,
    ShiftHandover,
    ShiftPurchaseNeed,
    ShiftPreparationNeed,
    ShiftStopItem,
)

from .views import (
    get_country,
    require_section_access,
)


def clean_decimal(value):
    if value is None or value == "":
        return Decimal("0")

    try:
        return Decimal(str(value).replace(",", "."))
    except Exception:
        return Decimal("0")

def send_shift_handover_to_telegram(handover, is_update=False):
    bot_token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(settings, "TELEGRAM_CHAT_ID", "")

    if not bot_token or not chat_id:
        return

    if not handover.location or not handover.location.telegram_thread_id:
        return

    title = "✏️ Обновлена передача смены" if is_update else "🔁 Передача смены"

    lines = [
        f"<b>{title}</b>",
        "",
        f"<b>Филиал:</b> {escape(handover.location.name)}",
        f"<b>Дата:</b> {handover.shift_date.strftime('%d.%m.%Y')}",
        f"<b>Ответственный:</b> {escape(handover.responsible.username if handover.responsible else '—')}",
    ]

    if handover.purchase_needs.exists():
        lines.append("")
        lines.append("<b>Что нужно докупить:</b>")

        for item in handover.purchase_needs.select_related("product").all():
            text = f"• {escape(item.product.name)}"

            if item.quantity:
                text += f" — {item.quantity}"

            if item.comment:
                text += f" — {escape(item.comment)}"

            lines.append(text)

    if handover.preparation_needs.exists():
        lines.append("")
        lines.append("<b>Какие заготовки приготовить:</b>")

        for item in handover.preparation_needs.select_related("preparation").all():
            text = f"• {escape(item.preparation.name)}"

            if item.quantity:
                text += f" — {item.quantity}"

            if item.comment:
                text += f" — {escape(item.comment)}"

            lines.append(text)

    if handover.stop_items.exists():
        lines.append("")
        lines.append("<b>Что на стопе:</b>")

        for item in handover.stop_items.select_related("dish").all():
            text = f"• {escape(item.dish.name)}"

            if item.comment:
                text += f" — {escape(item.comment)}"

            lines.append(text)

    if handover.comment:
        lines.append("")
        lines.append("<b>Комментарий:</b>")
        lines.append(escape(handover.comment))

    payload = {
        "chat_id": chat_id,
        "message_thread_id": handover.location.telegram_thread_id,
        "text": "\n".join(lines),
        "parse_mode": "HTML",
    }

    try:
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        urllib.request.urlopen(request, timeout=5)

    except Exception:
        pass
        
        
def send_shift_handover_deleted_to_telegram(handover):
    bot_token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
    chat_id = getattr(settings, "TELEGRAM_CHAT_ID", "")

    if not bot_token or not chat_id:
        return

    if not handover.location or not handover.location.telegram_thread_id:
        return

    text = "\n".join([
        "❌ <b>Передача смены удалена</b>",
        "",
        f"<b>Филиал:</b> {escape(handover.location.name)}",
        f"<b>Дата:</b> {handover.shift_date.strftime('%d.%m.%Y')}",
        f"<b>Удалил:</b> {escape(handover.responsible.username if handover.responsible else '—')}",
    ])

    payload = {
        "chat_id": chat_id,
        "message_thread_id": handover.location.telegram_thread_id,
        "text": text,
        "parse_mode": "HTML",
    }

    try:
        request_obj = urllib.request.Request(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        urllib.request.urlopen(request_obj, timeout=5)

    except Exception:
        pass        

@login_required(login_url="/login/")
def shift_handover_list(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_SHIFT_HANDOVER
    )

    if access_error:
        return access_error

    if request.method == "POST":
        action = request.POST.get("action", "create")

        if action == "delete":
            handover = get_object_or_404(
                ShiftHandover,
                id=request.POST.get("handover_id"),
                country=country,
            )

            can_delete = (
                handover.responsible_id == request.user.id
                and timezone.now() <= handover.created_at + timedelta(hours=2)
            )

            if not can_delete:
                return HttpResponseForbidden("Время удаления истекло")

            send_shift_handover_deleted_to_telegram(handover)
            handover.delete()

            return redirect(f"/c/{country.slug}/shift-handover/")

        if action == "update":
            handover = get_object_or_404(
                ShiftHandover,
                id=request.POST.get("handover_id"),
                country=country,
            )

            can_edit = (
                handover.responsible_id == request.user.id
                and timezone.now() <= handover.created_at + timedelta(hours=2)
            )

            if not can_edit:
                return HttpResponseForbidden("Время редактирования истекло")

            handover.shift_date = request.POST.get("shift_date")
            handover.comment = request.POST.get("comment", "")
            handover.save()

            handover.purchase_needs.all().delete()
            handover.preparation_needs.all().delete()
            handover.stop_items.all().delete()

        else:
            handover = ShiftHandover.objects.create(
                country=country,
                location=request.user.profile.location,
                shift_date=request.POST.get("shift_date"),
                responsible=request.user,
                comment=request.POST.get("comment", ""),
            )

        product_ids = request.POST.getlist("product_ids")
        product_quantities = request.POST.getlist("product_quantities")
        product_comments = request.POST.getlist("product_comments")

        for index, product_id in enumerate(product_ids):
            product = get_object_or_404(
                Product,
                id=product_id,
                country=country,
            )

            quantity = product_quantities[index] if index < len(product_quantities) else 0
            comment = product_comments[index] if index < len(product_comments) else ""

            ShiftPurchaseNeed.objects.create(
                handover=handover,
                product=product,
                quantity=clean_decimal(quantity),
                comment=comment,
            )

        preparation_ids = request.POST.getlist("preparation_ids")
        preparation_quantities = request.POST.getlist("preparation_quantities")
        preparation_comments = request.POST.getlist("preparation_comments")

        for index, preparation_id in enumerate(preparation_ids):
            preparation = get_object_or_404(
                Preparation,
                id=preparation_id,
                country=country,
            )

            quantity = preparation_quantities[index] if index < len(preparation_quantities) else 0
            comment = preparation_comments[index] if index < len(preparation_comments) else ""

            ShiftPreparationNeed.objects.create(
                handover=handover,
                preparation=preparation,
                quantity=clean_decimal(quantity),
                comment=comment,
            )

        dish_ids = request.POST.getlist("dish_ids")
        dish_comments = request.POST.getlist("dish_comments")

        for index, dish_id in enumerate(dish_ids):
            dish = get_object_or_404(
                Dish,
                id=dish_id,
                country=country,
            )

            comment = dish_comments[index] if index < len(dish_comments) else ""

            ShiftStopItem.objects.create(
                handover=handover,
                dish=dish,
                comment=comment,
            )

        send_shift_handover_to_telegram(
            handover,
            is_update=(action == "update")
        )

        return redirect(f"/c/{country.slug}/shift-handover/")

    handovers = ShiftHandover.objects.filter(
        country=country
    )

    if (
        request.user.profile.is_kitchen_staff()
        and request.user.profile.location_id
    ):
        handovers = handovers.filter(
            location_id=request.user.profile.location_id
        )

    handovers = handovers.order_by(
        "-shift_date",
        "-created_at"
    )

    now = timezone.now()

    for handover in handovers:
        handover.can_edit = (
            handover.responsible_id == request.user.id
            and now <= handover.created_at + timedelta(hours=2)
        )

    products = Product.objects.filter(country=country).order_by("name")
    preparations = Preparation.objects.filter(country=country).order_by("name")
    dishes = Dish.objects.filter(country=country).order_by("name")

    return render(request, "foodcost/shift_handover_list.html", {
        "country": country,
        "handovers": handovers,
        "products": products,
        "preparations": preparations,
        "dishes": dishes,
        "today": timezone.now().date(),
    })
    
    
@login_required(login_url="/login/")
def shift_handover_admin(request, country_slug):

    country = get_country(country_slug, request.user)

    if not (

        request.user.profile.can_edit()

        or request.user.profile.is_super_admin()

    ):

        return HttpResponseForbidden("Нет доступа")

    location_id = request.GET.get("location")

    handovers = ShiftHandover.objects.filter(

        country=country

    )

    if location_id:

        handovers = handovers.filter(

            location_id=location_id

        )

    handovers = handovers.order_by(

        "-shift_date",

        "-created_at"

    )

    locations = Location.objects.filter(

        country=country

    ).order_by("name")

    return render(

        request,

        "foodcost/shift_handover_admin.html",

        {

            "country": country,

            "handovers": handovers,

            "locations": locations,

            "selected_location": location_id,

        }

    )