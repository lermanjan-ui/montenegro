from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db.models import Sum
from django.http import HttpResponseForbidden
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from .models import (
    UserProfile,
    Product,
    Preparation,
    WriteOff,
    Location,
    StockMovement,
)

from .views import (
    get_country,
    require_section_access,
)


def _sync_writeoff_movement(writeoff, country, user):
    """Синхронизирует движение склада со списанием.
    Удаляет прежнее движение этого списания и, если задан склад и
    количество > 0, создаёт новое (расход, −количество)."""
    StockMovement.objects.filter(
        country=country,
        source_type=StockMovement.SOURCE_WRITEOFF,
        source_id=writeoff.id,
    ).delete()

    if not writeoff.location_id:
        return
    qty = writeoff.quantity or Decimal(0)
    if qty <= 0:
        return

    total = writeoff.cost or Decimal(0)
    unit_cost = (total / qty) if qty else Decimal(0)

    if writeoff.item_type == WriteOff.ITEM_TYPE_PRODUCT and writeoff.product_id:
        StockMovement.objects.create(
            country=country, warehouse_id=writeoff.location_id,
            item_type=StockMovement.ITEM_TYPE_PRODUCT, product_id=writeoff.product_id,
            quantity_delta=-qty,
            movement_type=StockMovement.TYPE_WRITEOFF,
            source_type=StockMovement.SOURCE_WRITEOFF, source_id=writeoff.id,
            unit_cost=unit_cost, total_cost=-total,
            comment=f"Списание: {writeoff.get_reason_display()}",
            created_by=user,
        )
    elif writeoff.item_type == WriteOff.ITEM_TYPE_PREPARATION and writeoff.preparation_id:
        StockMovement.objects.create(
            country=country, warehouse_id=writeoff.location_id,
            item_type=StockMovement.ITEM_TYPE_PREPARATION, preparation_id=writeoff.preparation_id,
            quantity_delta=-qty,
            movement_type=StockMovement.TYPE_WRITEOFF,
            source_type=StockMovement.SOURCE_WRITEOFF, source_id=writeoff.id,
            unit_cost=unit_cost, total_cost=-total,
            comment=f"Списание: {writeoff.get_reason_display()}",
            created_by=user,
        )


def _resolve_writeoff_location(request, country, profile):
    """Склад списания: повару — его склад из профиля; остальным — из формы."""
    if profile.is_kitchen_staff():
        if profile.location_id:
            return Location.objects.filter(id=profile.location_id, country=country).first()
        return None
    loc_id = request.POST.get("location_id")
    if loc_id:
        return Location.objects.filter(id=loc_id, country=country).first()
    return None


@login_required(login_url="/login/")
def writeoff_list(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_WRITE_OFFS
    )

    if access_error:
        return access_error

    if not hasattr(request.user, "profile"):
        return HttpResponseForbidden("Нет доступа")

    if not request.user.profile.is_kitchen_staff() and not request.user.profile.can_edit():
        return HttpResponseForbidden("Нет доступа")

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create":
            item_type = request.POST.get("item_type")
            quantity = request.POST.get("quantity") or 0
            reason = request.POST.get("reason")
            comment = request.POST.get("comment", "")
            writeoff_date = request.POST.get("writeoff_date")

            writeoff = WriteOff.objects.create(
                country=country,
                item_type=item_type,
                quantity=Decimal(str(quantity).replace(",", ".")),
                reason=reason,
                comment=comment,
                writeoff_date=writeoff_date,
                created_by=request.user,
            )

            if item_type == WriteOff.ITEM_TYPE_PRODUCT:
                product_id = request.POST.get("product_id")

                if product_id:
                    writeoff.product = get_object_or_404(
                        Product,
                        id=product_id,
                        country=country,
                    )

            if item_type == WriteOff.ITEM_TYPE_PREPARATION:
                preparation_id = request.POST.get("preparation_id")

                if preparation_id:
                    writeoff.preparation = get_object_or_404(
                        Preparation,
                        id=preparation_id,
                        country=country,
                    )

            writeoff.location = _resolve_writeoff_location(request, country, request.user.profile)
            writeoff.save()
            _sync_writeoff_movement(writeoff, country, request.user)

            return redirect(f"/c/{country.slug}/writeoffs/")
            
        if action == "delete":
            writeoff = get_object_or_404(
                WriteOff,
                id=request.POST.get("writeoff_id"),
                country=country,
            )

            StockMovement.objects.filter(
                country=country,
                source_type=StockMovement.SOURCE_WRITEOFF,
                source_id=writeoff.id,
            ).delete()
            writeoff.delete()

            return redirect(f"/c/{country.slug}/writeoffs/")
            
        if action == "update":
            writeoff = get_object_or_404(
                WriteOff,
                id=request.POST.get("writeoff_id"),
                country=country,
            )

            writeoff.quantity = Decimal(
                str(request.POST.get("quantity") or 0).replace(",", ".")
            )

            writeoff.reason = request.POST.get("reason")
            writeoff.comment = request.POST.get("comment", "")
            writeoff.writeoff_date = request.POST.get("writeoff_date")

            # склад может поменять только не-кухня (у повара он из профиля)
            if not request.user.profile.is_kitchen_staff():
                if request.POST.get("location_id"):
                    writeoff.location = _resolve_writeoff_location(
                        request, country, request.user.profile
                    )

            writeoff.save()
            _sync_writeoff_movement(writeoff, country, request.user)

            return redirect(f"/c/{country.slug}/writeoffs/")

    if request.user.profile.is_kitchen_staff():
        days = 3
    else:
        days = 30

    date_from = timezone.now().date() - timedelta(days=days)

    writeoffs = WriteOff.objects.filter(
        country=country,
        writeoff_date__gte=date_from,
    ).order_by("-writeoff_date", "-created_at")

    products = Product.objects.filter(country=country).order_by("name")
    preparations = Preparation.objects.filter(country=country).order_by("name")

    locations = Location.objects.filter(country=country).order_by("name")
    is_kitchen = request.user.profile.is_kitchen_staff()
    profile_location_id = request.user.profile.location_id

    return render(request, "foodcost/writeoff_list.html", {
        "country": country,
        "writeoffs": writeoffs,
        "products": products,
        "preparations": preparations,
        "reasons": WriteOff.REASON_CHOICES,
        "today": timezone.now().date(),
        "days": days,
        "locations": locations,
        "is_kitchen": is_kitchen,
        "profile_location_id": profile_location_id,
    })


@login_required(login_url="/login/")
def writeoff_analytics(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_WRITE_OFF_ANALYTICS
    )

    if access_error:
        return access_error

    date_to = request.GET.get("date_to") or timezone.now().date().isoformat()
    date_from = request.GET.get("date_from") or (
        timezone.now().date() - timedelta(days=30)
    ).isoformat()

    item_type = request.GET.get("item_type", "")
    reason = request.GET.get("reason", "")
    product_id = request.GET.get("product_id", "")
    preparation_id = request.GET.get("preparation_id", "")
    user_id = request.GET.get("user_id", "")

    writeoffs = WriteOff.objects.filter(
        country=country,
        writeoff_date__gte=date_from,
        writeoff_date__lte=date_to,
    )

    if item_type:
        writeoffs = writeoffs.filter(item_type=item_type)

    if reason:
        writeoffs = writeoffs.filter(reason=reason)

    if product_id:
        writeoffs = writeoffs.filter(product_id=product_id)

    if preparation_id:
        writeoffs = writeoffs.filter(preparation_id=preparation_id)

    if user_id:
        writeoffs = writeoffs.filter(created_by_id=user_id)

    total_cost = writeoffs.aggregate(total=Sum("cost"))["total"] or 0

    staff_meal_cost = writeoffs.filter(
        reason=WriteOff.REASON_STAFF_MEAL
    ).aggregate(total=Sum("cost"))["total"] or 0

    by_reason = (
        writeoffs
        .values("reason")
        .annotate(total=Sum("cost"))
        .order_by("-total")
    )

    by_product = (
        writeoffs
        .filter(item_type=WriteOff.ITEM_TYPE_PRODUCT, product__isnull=False)
        .values("product__name")
        .annotate(total=Sum("cost"))
        .order_by("-total")
    )

    by_preparation = (
        writeoffs
        .filter(item_type=WriteOff.ITEM_TYPE_PREPARATION, preparation__isnull=False)
        .values("preparation__name")
        .annotate(total=Sum("cost"))
        .order_by("-total")
    )

    by_user = (
        writeoffs
        .filter(created_by__isnull=False)
        .values("created_by__username")
        .annotate(total=Sum("cost"))
        .order_by("-total")
    )

    reason_labels = dict(WriteOff.REASON_CHOICES)

    products = Product.objects.filter(country=country).order_by("name")
    preparations = Preparation.objects.filter(country=country).order_by("name")
    users = User.objects.filter(writeoffs__country=country).distinct().order_by("username")

    return render(request, "foodcost/writeoff_analytics.html", {
        "country": country,
        "writeoffs": writeoffs.order_by("-writeoff_date", "-created_at"),
        "total_cost": total_cost,
        "staff_meal_cost": staff_meal_cost,
        "by_reason": by_reason,
        "by_product": by_product,
        "by_preparation": by_preparation,
        "by_user": by_user,
        "reason_labels": reason_labels,
        "reasons": WriteOff.REASON_CHOICES,
        "products": products,
        "preparations": preparations,
        "users": users,
        "date_from": date_from,
        "date_to": date_to,
        "item_type": item_type,
        "reason": reason,
        "product_id": product_id,
        "preparation_id": preparation_id,
        "user_id": user_id,
    })