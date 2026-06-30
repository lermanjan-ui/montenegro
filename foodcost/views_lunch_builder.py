"""
🍱 Конструктор обедов (этап 2) — отдельный модуль, не трогает views.py.

Страницы (доступ — секция «Блюда»):
  GET/POST  /c/<slug>/lunches/                 — список обедов по датам + создание
  GET/POST  /c/<slug>/lunches/<lunch_id>/      — конструктор одного обеда:
            редактирование обеда, размеры (своя цена/граммовка), состав строками
            с привязкой к блюду/заготовке/продукту и живым расчётом
            себестоимости / маржи / фудкоста.

Себестоимость берётся из методов модели (LunchSizeItem.component_cost и т.д.),
которые повторяют расчёт блюд.
"""

from decimal import Decimal, InvalidOperation
import datetime

from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone

from .models import UserProfile, Dish, Preparation, Product
from .models_lunch import Lunch, LunchSize, LunchSizeItem
from .views import get_country, require_section_access


# --- секция доступа (меню/кухня). При необходимости поменять на свою. ---
LUNCH_SECTION = UserProfile.SECTION_DISHES


def _int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _dec(value, default="0"):
    raw = str(value if value is not None else default).strip()
    raw = raw.replace(" ", "").replace("\u00a0", "").replace(",", ".")
    if raw == "":
        raw = default
    try:
        return Decimal(raw)
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _date(value):
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.date.fromisoformat(raw)
    except ValueError:
        return None


def _money(value):
    try:
        n = int(round(float(value or 0)))
    except (TypeError, ValueError):
        return "0"
    s = f"{abs(n):,}".replace(",", " ")
    return f"-{s}" if n < 0 else s


def _parse_component(raw):
    """'dish:12' / 'prep:5' / 'product:9' / 'none' -> (kind, id)."""
    raw = (raw or "").strip()
    if not raw or raw == "none" or ":" not in raw:
        return None, None
    kind, _, sid = raw.partition(":")
    return kind, _int(sid)


def _apply_component(item, raw, country):
    """Проставить привязку строки состава по значению селекта."""
    kind, cid = _parse_component(raw)
    item.dish = None
    item.preparation = None
    item.product = None
    if kind == "dish" and cid:
        item.dish = Dish.objects.filter(id=cid, country=country).first()
    elif kind == "prep" and cid:
        item.preparation = Preparation.objects.filter(id=cid, country=country).first()
    elif kind == "product" and cid:
        item.product = Product.objects.filter(id=cid, country=country).first()


def _apply_extras(item, post):
    """Поля выгоды и доп. порций (аддендум)."""
    item.separate_price = _dec(post.get("separate_price"))
    ep = post.get("extra_price")
    item.extra_price = _dec(ep) if (ep or "").strip() != "" else None
    item.extra_weight = (post.get("extra_weight") or "").strip()
    em = post.get("extra_max")
    item.extra_max = _int(em) if (em or "").strip() != "" else None


# ===========================================================================
#  СПИСОК ОБЕДОВ
# ===========================================================================

@login_required
def lunches_list(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = require_section_access(request.user, LUNCH_SECTION)
    if access_error:
        return access_error

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "create_lunch":
            d = _date(request.POST.get("date")) or timezone.localdate()
            name = (request.POST.get("name") or "").strip() or "Обед"
            lunch = Lunch.objects.create(country=country, date=d, name=name)
            # сразу создаём дефолтный размер «Стандарт», чтобы было что наполнять
            LunchSize.objects.create(
                lunch=lunch, label="Стандарт", is_default=True, sort_order=0
            )
            return redirect(f"/c/{country.slug}/lunches/{lunch.id}/")
        if action == "delete_lunch":
            Lunch.objects.filter(
                id=_int(request.POST.get("lunch_id")), country=country
            ).delete()
            return redirect(f"/c/{country.slug}/lunches/")

    lunches = (
        Lunch.objects.filter(country=country)
        .prefetch_related("sizes")
        .order_by("-date", "sort_order", "id")
    )
    rows = []
    for lu in lunches:
        sizes = list(lu.sizes.all())
        default = None
        for s in sizes:
            if s.is_default:
                default = s
                break
        if default is None and sizes:
            default = sizes[0]
        rows.append({
            "lunch": lu,
            "sizes_count": len(sizes),
            "price_fmt": _money(default.price) if default else "0",
            "margin_fmt": _money(default.margin()) if default else "0",
            "foodcost": default.foodcost_percent() if default else 0,
        })

    context = {
        "country": country,
        "rows": rows,
        "today": timezone.localdate().strftime("%Y-%m-%d"),
    }
    return render(request, "foodcost/lunch_builder_list.html", context)


# ===========================================================================
#  КОНСТРУКТОР ОДНОГО ОБЕДА
# ===========================================================================

@login_required
def lunch_builder(request, country_slug, lunch_id):
    country = get_country(country_slug, request.user)
    access_error = require_section_access(request.user, LUNCH_SECTION)
    if access_error:
        return access_error

    lunch = get_object_or_404(Lunch, id=lunch_id, country=country)
    base_url = f"/c/{country.slug}/lunches/{lunch.id}/"

    if request.method == "POST":
        action = request.POST.get("action", "")

        # ----- обед -----
        if action == "update_lunch":
            lunch.name = (request.POST.get("name") or lunch.name).strip()
            lunch.description = (request.POST.get("description") or "").strip()
            lunch.photo_url = (request.POST.get("photo_url") or "").strip()
            badge = (request.POST.get("badge") or "").strip()
            lunch.badge = badge if badge in dict(Lunch.BADGE_CHOICES) else ""
            lunch.available = request.POST.get("available") == "on"
            lunch.is_active = request.POST.get("is_active") == "on"
            lunch.order_cutoff = (request.POST.get("order_cutoff") or "").strip()
            d = _date(request.POST.get("date"))
            if d:
                lunch.date = d
            lunch.sort_order = _int(request.POST.get("sort_order"), lunch.sort_order) or 0
            lunch.save()
            return redirect(base_url)

        if action == "delete_lunch":
            lunch.delete()
            return redirect(f"/c/{country.slug}/lunches/")

        # ----- размеры -----
        if action == "add_size":
            is_def = request.POST.get("is_default") == "on"
            if is_def:
                lunch.sizes.update(is_default=False)
            LunchSize.objects.create(
                lunch=lunch,
                label=(request.POST.get("label") or "Размер").strip(),
                price=_dec(request.POST.get("price")),
                weight_total=_int(request.POST.get("weight_total"), 0) or 0,
                is_default=is_def,
                sort_order=_int(request.POST.get("sort_order"), 0) or 0,
            )
            return redirect(base_url)

        if action == "update_size":
            size = get_object_or_404(
                LunchSize, id=_int(request.POST.get("size_id")), lunch=lunch
            )
            size.label = (request.POST.get("label") or size.label).strip()
            size.price = _dec(request.POST.get("price"))
            size.weight_total = _int(request.POST.get("weight_total"), 0) or 0
            size.sort_order = _int(request.POST.get("sort_order"), 0) or 0
            is_def = request.POST.get("is_default") == "on"
            if is_def:
                lunch.sizes.exclude(id=size.id).update(is_default=False)
            size.is_default = is_def
            size.save()
            return redirect(base_url)

        if action == "delete_size":
            LunchSize.objects.filter(
                id=_int(request.POST.get("size_id")), lunch=lunch
            ).delete()
            return redirect(base_url)

        # ----- строки состава -----
        if action == "add_item":
            size = get_object_or_404(
                LunchSize, id=_int(request.POST.get("size_id")), lunch=lunch
            )
            item = LunchSizeItem(
                size=size,
                role=(request.POST.get("role") or "other").strip(),
                name=(request.POST.get("name") or "").strip(),
                weight=(request.POST.get("weight") or "").strip(),
                net=_dec(request.POST.get("net")),
                quantity=_dec(request.POST.get("quantity"), "1"),
                sort_order=_int(request.POST.get("sort_order"), 0) or 0,
            )
            co = request.POST.get("cost_override")
            item.cost_override = _dec(co) if (co or "").strip() != "" else None
            _apply_component(item, request.POST.get("component"), country)
            _apply_extras(item, request.POST)
            item.save()
            return redirect(base_url)

        if action == "update_item":
            item = get_object_or_404(
                LunchSizeItem, id=_int(request.POST.get("item_id")), size__lunch=lunch
            )
            item.role = (request.POST.get("role") or item.role).strip()
            item.name = (request.POST.get("name") or "").strip()
            item.weight = (request.POST.get("weight") or "").strip()
            item.net = _dec(request.POST.get("net"))
            item.quantity = _dec(request.POST.get("quantity"), "1")
            item.sort_order = _int(request.POST.get("sort_order"), 0) or 0
            co = request.POST.get("cost_override")
            item.cost_override = _dec(co) if (co or "").strip() != "" else None
            _apply_component(item, request.POST.get("component"), country)
            _apply_extras(item, request.POST)
            item.save()
            return redirect(base_url)

        if action == "delete_item":
            LunchSizeItem.objects.filter(
                id=_int(request.POST.get("item_id")), size__lunch=lunch
            ).delete()
            return redirect(base_url)

        return redirect(base_url)

    # ----- GET: собрать данные для шаблона -----
    dish_options = [
        {"value": f"dish:{d['id']}", "name": d["name"]}
        for d in Dish.objects.filter(country=country).order_by("name").values("id", "name")
    ]
    prep_options = [
        {"value": f"prep:{p['id']}", "name": p["name"]}
        for p in Preparation.objects.filter(country=country).order_by("name").values("id", "name")
    ]
    product_options = [
        {"value": f"product:{p['id']}", "name": p["name"]}
        for p in Product.objects.filter(country=country).order_by("name").values("id", "name")
    ]

    sizes_data = []
    for size in lunch.sizes.all().prefetch_related(
        "items__dish", "items__preparation", "items__product"
    ):
        items = []
        for it in size.items.all():
            cost = it.component_cost()
            if it.dish_id:
                comp_value = f"dish:{it.dish_id}"
            elif it.preparation_id:
                comp_value = f"prep:{it.preparation_id}"
            elif it.product_id:
                comp_value = f"product:{it.product_id}"
            else:
                comp_value = "none"
            items.append({
                "item": it,
                "display_name": it.display_name(),
                "comp_value": comp_value,
                "cost": cost,
                "cost_fmt": _money(cost),
            })
        total_cost = size.total_cost()
        separate = size.separate_price()
        savings = size.savings()
        sizes_data.append({
            "size": size,
            "items": items,
            "total_cost": total_cost,
            "total_cost_fmt": _money(total_cost),
            "price_fmt": _money(size.price),
            "margin": size.margin(),
            "margin_fmt": _money(size.margin()),
            "foodcost": size.foodcost_percent(),
            "margin_pct": size.margin_percent(),
            "separate_fmt": _money(separate),
            "savings": savings,
            "savings_fmt": _money(savings),
        })

    context = {
        "country": country,
        "lunch": lunch,
        "sizes_data": sizes_data,
        "dish_options": dish_options,
        "prep_options": prep_options,
        "product_options": product_options,
        "badge_choices": Lunch.BADGE_CHOICES,
        "role_choices": LunchSizeItem.ROLE_CHOICES,
        "date_value": lunch.date.strftime("%Y-%m-%d") if lunch.date else "",
    }
    return render(request, "foodcost/lunch_builder.html", context)


# ===========================================================================
#  🛒 Управление апсейлом страницы обедов (один список на всю страницу).
#     /c/<slug>/lunches/upsell/  — добавить/убрать товары (флаг show_in_combo_block).
#  Добавление через поле с фильтрацией по названию.
# ===========================================================================

@login_required
def lunch_upsell_manage(request, country_slug):
    country = get_country(country_slug, request.user)
    access_error = require_section_access(request.user, LUNCH_SECTION)
    if access_error:
        return access_error

    back = f"/c/{country.slug}/lunches/upsell/"

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "add_upsell":
            d = Dish.objects.filter(
                id=_int(request.POST.get("dish_id")), country=country
            ).first()
            if d is not None:
                d.show_in_combo_block = True
                d.save(update_fields=["show_in_combo_block"])
            return redirect(back)
        if action == "remove_upsell":
            d = Dish.objects.filter(
                id=_int(request.POST.get("dish_id")), country=country
            ).first()
            if d is not None:
                d.show_in_combo_block = False
                d.save(update_fields=["show_in_combo_block"])
            return redirect(back)
        return redirect(back)

    current = list(
        Dish.objects
        .filter(country=country, show_in_combo_block=True)
        .order_by("name")
        .values("id", "name", "selling_price", "is_visible_on_site", "is_stop_list")
    )
    current_ids = {d["id"] for d in current}
    candidates = [
        d for d in Dish.objects
        .filter(country=country, is_visible_on_site=True)
        .order_by("name")
        .values("id", "name")
        if d["id"] not in current_ids
    ]

    context = {
        "country": country,
        "current": current,
        "candidates": candidates,
    }
    return render(request, "foodcost/lunch_upsell.html", context)
