from decimal import Decimal

from django.http import JsonResponse, HttpResponseForbidden, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
import json
from decimal import Decimal
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .forms import ProductWithPriceForm
from .models import (
    Country,
    Product,
    ProductPrice,
    DishCategory,
    Dish,
    DishTechStep,
    Preparation,
    PreparationItem,
    PreparationSubItem,
    DishProductItem,
    DishPreparationItem,
    Employee,
    Packaging,
    DishPackagingItem,
    DishLaborItem,
    DishAdditionalExpense,
    MonthlyUtilityExpense,
    UserProfile,
    Location,
    Customer,
    CustomerAddress,
    Order,
    OrderItem,
    Dish,
    OrderSource,
    PromoCode,
    Country,
    # 🌐 Public website models (Part 1)
    DishAvailability,
    AddonGroup,
    AddonItem,
    DishAddonGroup,
    CategoryAddonGroup,
    # 🌐 Website content models (Part 4)
    DishGalleryImage,
    DishAddon,
)


def is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"

def clean_decimal(value, default="0"):
    if value is None or value == "":
        return default
    return str(value).replace(",", ".")


def _parse_optional_decimal(value):
    """
    Parse a Decimal from a form value that may be empty or invalid.

    Returns Decimal or None. Empty / invalid input yields None so the model
    receives a SQL NULL (latitude / longitude fields are nullable). Comma is
    accepted as the decimal separator, since users often type "41,2995".
    """
    if value is None:
        return None
    raw = str(value).strip().replace(",", ".")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except Exception:
        return None

def get_country(country_slug, user=None):
    country = get_object_or_404(Country, slug=country_slug)

    if user is not None and not user_can_access_country(user, country):
        raise Http404("Страна не найдена")

    return country


def user_can_access_country(user, country):
    if user.is_superuser:
        return True

    if not hasattr(user, "profile"):
        return False

    return user.profile.can_access_country(country)


def user_can_edit(user):
    if user.is_superuser:
        return True

    if not hasattr(user, "profile"):
        return False

    return user.profile.can_edit()


def user_can_access_section(user, section):
    if user.is_superuser:
        return True

    if not hasattr(user, "profile"):
        return False

    return user.profile.can_access_section(section)


def require_section_access(user, section):
    if not user_can_access_section(user, section):
        return HttpResponseForbidden("У вас нет доступа к этому разделу")

    return None


@login_required(login_url="/login/")
def country_list(request):
    if request.user.is_superuser:
        countries = Country.objects.all()
    else:
        countries = Country.objects.filter(user_profiles__user=request.user)

    return render(request, "foodcost/country_list.html", {
        "countries": countries,
    })

@login_required(login_url="/login/")
def dish_create(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_DISHES
    )

    if access_error:
        return access_error

    if not user_can_edit(request.user):
        return HttpResponseForbidden("У вас нет прав на создание блюда")

    categories = DishCategory.objects.filter(country=country)

    if request.method == "POST":
        name = request.POST.get("name") or "Новое блюдо"
        final_weight = request.POST.get("final_weight") or 0
        selling_price = request.POST.get("selling_price") or 0
        cooking_minutes = request.POST.get("cooking_minutes") or 0

        category_id = request.POST.get("category_id")
        new_category_name = request.POST.get("new_category_name")

        category = None

        if new_category_name:
            category = DishCategory.objects.create(
                country=country,
                name=new_category_name,
            )

        elif category_id:
            category = get_object_or_404(
                DishCategory,
                id=category_id,
                country=country,
            )

        dish = Dish.objects.create(
            country=country,
            category=category,
            name=name,
            final_weight=final_weight,
            selling_price=selling_price,
            cooking_minutes=cooking_minutes,
        )

        return redirect(f"/c/{country.slug}/dish/{dish.id}/")

    return render(request, "foodcost/dish_create.html", {
        "country": country,
        "categories": categories,
    })

@login_required(login_url="/login/")
def dish_list(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_DISHES
    )

    if access_error:
        if hasattr(request.user, "profile"):
            profile = request.user.profile

            if profile.is_super_admin():
                return access_error

            if UserProfile.SECTION_WRITE_OFFS in profile.allowed_sections:
                return redirect(f"/c/{country.slug}/writeoffs/")

            if UserProfile.SECTION_SHIFT_HANDOVER in profile.allowed_sections:
                return redirect(f"/c/{country.slug}/shift-handover/")

            if UserProfile.SECTION_PRODUCTS in profile.allowed_sections:
                return redirect(f"/c/{country.slug}/products/")

            if UserProfile.SECTION_PREPARATIONS in profile.allowed_sections:
                return redirect(f"/c/{country.slug}/preparations/")

            if UserProfile.SECTION_EMPLOYEES in profile.allowed_sections:
                return redirect(f"/c/{country.slug}/employees/")

            if UserProfile.SECTION_PACKAGING in profile.allowed_sections:
                return redirect(f"/c/{country.slug}/packaging/")

            if UserProfile.SECTION_UTILITIES in profile.allowed_sections:
                return redirect(f"/c/{country.slug}/utilities/")

        return access_error

    if request.method == "POST":
        if not user_can_edit(request.user):
            return HttpResponseForbidden("У вас нет прав на редактирование")

        action = request.POST.get("action")

        if action == "create_category":
            category_name = request.POST.get("category_name")

            if category_name:
                DishCategory.objects.create(
                    country=country,
                    name=category_name,
                )

        return redirect(f"/c/{country.slug}/")

    dishes = list(Dish.objects.filter(country=country))
    categories = DishCategory.objects.filter(country=country)

    filter_type = request.GET.get("filter", "all")
    sort_type = request.GET.get("sort", "name")
    category_ids = request.GET.getlist("categories")

    if category_ids:
        dishes = [
            dish for dish in dishes
            if dish.category_id and str(dish.category_id) in category_ids
        ]

    if filter_type == "loss":
        dishes = [dish for dish in dishes if dish.cached_margin < 0]

    if filter_type == "high_foodcost":
        dishes = [dish for dish in dishes if dish.cached_foodcost > 40]

    if filter_type == "normal":
        dishes = [
            dish for dish in dishes
            if dish.cached_foodcost <= 40 and dish.cached_margin >= 0
        ]

    if sort_type == "margin":
        dishes.sort(key=lambda dish: dish.cached_margin, reverse=True)
    elif sort_type == "foodcost":
        dishes.sort(key=lambda dish: dish.cached_foodcost, reverse=True)
    elif sort_type == "cost":
        dishes.sort(key=lambda dish: dish.cached_total_cost, reverse=True)
    else:
        dishes.sort(key=lambda dish: dish.name.lower())

    return render(request, "foodcost/dish_list.html", {
        "country": country,
        "dishes": dishes,
        "categories": categories,
        "selected_category_ids": category_ids,
        "filter_type": filter_type,
        "sort_type": sort_type,
        "can_edit": user_can_edit(request.user),
    })

@login_required(login_url="/login/")
def live_calculate(request, country_slug):
    country = get_country(country_slug, request.user)

    item_type = request.GET.get("type")
    item_id = request.GET.get("id")
    quantity = Decimal(request.GET.get("quantity") or "0")

    cost = Decimal("0")

    if item_type == "product":
        product = get_object_or_404(Product, id=item_id, country=country)
        price = product.get_price()
        if price:
            cost = quantity * price.price

    if item_type == "preparation":
        preparation = get_object_or_404(Preparation, id=item_id, country=country)
        cost = quantity * preparation.cost_per_kg()

    if item_type == "packaging":
        packaging = get_object_or_404(Packaging, id=item_id, country=country)
        cost = quantity * packaging.cost

    if item_type == "labor":
        employee = get_object_or_404(Employee, id=item_id, country=country)
        cost = quantity * employee.minute_rate()

    return JsonResponse({"cost": round(cost, 2)})


def compute_dish_permissions(user):
    """
    Return a dict of role-based dish-detail permission flags.

    Used by dish_detail to decide which blocks to render and which POST
    actions are allowed for the current user.

    Flags:
        can_view_dish_finance       — see cost / margin / foodcost / breakdown
        can_edit_dish_finance       — edit products / preparations / packaging
                                      / labor / extras / recalculation
        can_edit_dish_base          — edit name / category / weight / price /
                                      cooking minutes (i.e. the 'save' action)
        can_edit_dish_site          — edit "Information for website" block
        can_edit_dish_gallery       — upload / sort / delete gallery images
                                      + change main photo
        can_edit_dish_addons        — attach / detach addon dishes
        can_edit_dish_availability  — toggle per-branch availability (cashier-friendly)
        can_edit_dish_stoplist      — alias of availability, kept for backward
                                      compatibility with templates / earlier code
        can_view_tech_card          — see / edit tech card and tech steps
    """
    flags = {
        "can_view_dish_finance": False,
        "can_edit_dish_finance": False,
        "can_edit_dish_base": False,
        "can_edit_dish_site": False,
        "can_edit_dish_gallery": False,
        "can_edit_dish_addons": False,
        "can_edit_dish_availability": False,
        "can_edit_dish_stoplist": False,
        "can_view_tech_card": False,
    }

    # Anonymous (login_required wraps the view but guard anyway)
    if not user.is_authenticated:
        return flags

    # Superuser: full access
    if user.is_superuser:
        return {k: True for k in flags}

    profile = getattr(user, "profile", None)
    if profile is None:
        return flags

    has_dishes = profile.can_access_section(UserProfile.SECTION_DISHES)
    has_orders = profile.can_access_section(UserProfile.SECTION_ORDERS)
    has_handover = profile.can_access_section(UserProfile.SECTION_SHIFT_HANDOVER)
    has_all_orders = profile.can_access_section(UserProfile.SECTION_ALL_ORDERS)

    can_edit_role = profile.can_edit()  # super_admin or admin role
    is_kitchen = profile.is_kitchen_staff()

    # --- Finance / cost ---
    if has_dishes:
        flags["can_view_dish_finance"] = True
        if can_edit_role:
            flags["can_edit_dish_finance"] = True

    # --- Base data (name/category/weight/price/cooking_minutes) ---
    if has_dishes and can_edit_role:
        flags["can_edit_dish_base"] = True

    # --- Website fields ---
    if has_dishes and can_edit_role:
        flags["can_edit_dish_site"] = True

    # --- Gallery & main photo ---
    if has_dishes and can_edit_role:
        flags["can_edit_dish_gallery"] = True

    # --- Availability / stop-list per branch ---
    # Cashiers (SECTION_ORDERS / ALL_ORDERS) and shift staff can toggle stops.
    # Admins with SECTION_DISHES can too. Kitchen staff also can.
    availability_allowed = (
        is_kitchen
        or has_orders
        or has_handover
        or has_all_orders
        or (has_dishes and can_edit_role)
    )
    flags["can_edit_dish_availability"] = availability_allowed
    flags["can_edit_dish_stoplist"] = availability_allowed  # back-compat alias

    # --- Addons (now: attach/detach Dish-as-addon) ---
    if has_dishes and can_edit_role:
        flags["can_edit_dish_addons"] = True

    # --- Tech card ---
    # Visible to anyone working with the dish (dishes section) and to kitchen.
    if has_dishes or is_kitchen:
        flags["can_view_tech_card"] = True

    return flags


def user_can_view_dish_page(user):
    """True iff the user can open the dish detail page at all."""
    if not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    profile = getattr(user, "profile", None)
    if profile is None:
        return False
    return (
        profile.can_access_section(UserProfile.SECTION_DISHES)
        or profile.can_access_section(UserProfile.SECTION_ORDERS)
        or profile.can_access_section(UserProfile.SECTION_ALL_ORDERS)
        or profile.can_access_section(UserProfile.SECTION_SHIFT_HANDOVER)
        or profile.is_kitchen_staff()
    )


@login_required(login_url="/login/")
def dish_detail(request, country_slug, dish_id):
    """
    Unified dish detail page (ERP + website + gallery + addons).

    Access:
      - Section DISHES: full ERP access (subject to admin role for edits).
      - Section ORDERS / ALL_ORDERS / SHIFT_HANDOVER / kitchen role:
        page opens with limited blocks (availability only by default).

    Permissions are computed once by compute_dish_permissions() and passed to
    the template. Every POST action is gated by the matching flag.
    """
    country = get_country(country_slug, request.user)

    if not user_can_view_dish_page(request.user):
        return HttpResponseForbidden("У вас нет доступа к этому разделу")

    dish = get_object_or_404(Dish, id=dish_id, country=country)

    perms = compute_dish_permissions(request.user)

    products = Product.objects.filter(country=country)
    preparations = Preparation.objects.filter(country=country)
    employees = Employee.objects.filter(country=country)
    packagings = Packaging.objects.filter(country=country)
    tech_steps = DishTechStep.objects.filter(dish=dish)

    if request.method == "POST":
        action = request.POST.get("action")

        # =====================================================================
        # 🌐 Website fields update (does NOT include the photo — separate action)
        # =====================================================================
        if action == "update_site":
            if not perms["can_edit_dish_site"]:
                return HttpResponseForbidden("Нет прав на редактирование сайта")

            dish.is_visible_on_site = bool(request.POST.get("is_visible_on_site"))
            dish.is_stop_list = bool(request.POST.get("is_stop_list"))
            dish.is_featured = bool(request.POST.get("is_featured"))
            dish.is_new = bool(request.POST.get("is_new"))
            dish.is_spicy = bool(request.POST.get("is_spicy"))
            dish.is_vegetarian = bool(request.POST.get("is_vegetarian"))

            dish.public_name = (request.POST.get("public_name") or "").strip()
            dish.slug = (request.POST.get("slug") or "").strip()
            dish.short_description = (request.POST.get("short_description") or "").strip()
            dish.public_description = request.POST.get("public_description") or ""
            dish.composition = request.POST.get("composition") or ""
            dish.spice_level = (request.POST.get("spice_level") or "").strip()
            dish.badge = (request.POST.get("badge") or "").strip()

            # 🔗 External photo URL — has PRIORITY over uploaded photo in
            # the public API. Stored independently so the uploaded photo
            # remains as a fallback / backup.
            dish.photo_url = (request.POST.get("photo_url") or "").strip()

            try:
                dish.site_sort_order = int(request.POST.get("site_sort_order") or 0)
            except (TypeError, ValueError):
                dish.site_sort_order = 0

            dish.save()

            return redirect(f"/c/{country.slug}/dish/{dish.id}/#tab-website")

        # =====================================================================
        # 🏷 Public categories — add / remove (Part 5)
        # Multi-select was UX-rejected; users now manage relations one at a time.
        # =====================================================================
        if action == "add_public_category":
            if not perms["can_edit_dish_site"]:
                return HttpResponseForbidden("Нет прав на категории сайта")

            try:
                cat_id = int(request.POST.get("category_id") or 0)
            except (TypeError, ValueError):
                cat_id = 0
            if cat_id:
                cat = DishCategory.objects.filter(
                    id=cat_id, country=country
                ).first()
                if cat is not None:
                    dish.public_categories.add(cat)

            return redirect(f"/c/{country.slug}/dish/{dish.id}/#tab-website")

        if action == "remove_public_category":
            if not perms["can_edit_dish_site"]:
                return HttpResponseForbidden("Нет прав на категории сайта")

            try:
                cat_id = int(request.POST.get("category_id") or 0)
            except (TypeError, ValueError):
                cat_id = 0
            if cat_id:
                cat = DishCategory.objects.filter(
                    id=cat_id, country=country
                ).first()
                if cat is not None:
                    # Only remove the relation; never delete the DishCategory itself.
                    dish.public_categories.remove(cat)

            return redirect(f"/c/{country.slug}/dish/{dish.id}/#tab-website")

        # =====================================================================
        # 🖼 Main photo (Dish.photo) — separate small form
        # =====================================================================
        if action == "update_main_photo":
            if not perms["can_edit_dish_gallery"]:
                return HttpResponseForbidden("Нет прав на галерею")

            uploaded = request.FILES.get("photo")
            if uploaded:
                dish.photo = uploaded
                dish.save()
            elif request.POST.get("photo_clear"):
                if dish.photo:
                    dish.photo.delete(save=False)
                dish.photo = None
                dish.save()

            return redirect(f"/c/{country.slug}/dish/{dish.id}/#tab-gallery")

        # =====================================================================
        # 🖼 Gallery — add / update / delete
        # =====================================================================
        if action == "add_gallery_image":
            if not perms["can_edit_dish_gallery"]:
                return HttpResponseForbidden("Нет прав на галерею")

            uploaded = request.FILES.get("image")
            if uploaded:
                try:
                    sort_order = int(request.POST.get("sort_order") or 0)
                except (TypeError, ValueError):
                    sort_order = 0

                DishGalleryImage.objects.create(
                    dish=dish,
                    image=uploaded,
                    sort_order=sort_order,
                    alt_text=(request.POST.get("alt_text") or "").strip(),
                    is_active=True,
                )

            return redirect(f"/c/{country.slug}/dish/{dish.id}/#tab-gallery")

        if action == "update_gallery_image":
            if not perms["can_edit_dish_gallery"]:
                return HttpResponseForbidden("Нет прав на галерею")

            image_obj = get_object_or_404(
                DishGalleryImage,
                id=request.POST.get("image_id"),
                dish=dish,
            )

            try:
                image_obj.sort_order = int(request.POST.get("sort_order") or 0)
            except (TypeError, ValueError):
                image_obj.sort_order = 0

            image_obj.alt_text = (request.POST.get("alt_text") or "").strip()
            image_obj.is_active = bool(request.POST.get("is_active"))

            # crop_data — accept JSON text; ignore if invalid or empty so the
            # form still saves and existing crop_data is preserved when the
            # textarea is left blank.
            crop_raw = (request.POST.get("crop_data") or "").strip()
            if crop_raw:
                try:
                    image_obj.crop_data = json.loads(crop_raw)
                except (ValueError, TypeError):
                    pass

            image_obj.save()
            return redirect(f"/c/{country.slug}/dish/{dish.id}/#tab-gallery")

        if action == "delete_gallery_image":
            if not perms["can_edit_dish_gallery"]:
                return HttpResponseForbidden("Нет прав на галерею")

            image_obj = get_object_or_404(
                DishGalleryImage,
                id=request.POST.get("image_id"),
                dish=dish,
            )
            if image_obj.image:
                image_obj.image.delete(save=False)
            image_obj.delete()
            return redirect(f"/c/{country.slug}/dish/{dish.id}/#tab-gallery")

        # =====================================================================
        # ➕ Dish-as-addon — attach / detach
        # =====================================================================
        if action == "add_dish_addon":
            if not perms["can_edit_dish_addons"]:
                return HttpResponseForbidden("Нет прав на дополнения")

            addon_dish_id = request.POST.get("addon_dish_id")
            if addon_dish_id:
                addon_dish = Dish.objects.filter(
                    id=addon_dish_id, country=country
                ).first()
                if addon_dish and addon_dish.id != dish.id:
                    DishAddon.objects.get_or_create(
                        dish=dish,
                        addon_dish=addon_dish,
                        defaults={
                            "group_name": (request.POST.get("group_name") or "").strip(),
                            "sort_order": 0,
                            "is_active": True,
                        },
                    )

            return redirect(f"/c/{country.slug}/dish/{dish.id}/#tab-addons")

        if action == "delete_dish_addon":
            if not perms["can_edit_dish_addons"]:
                return HttpResponseForbidden("Нет прав на дополнения")

            DishAddon.objects.filter(
                id=request.POST.get("addon_id"),
                dish=dish,
            ).delete()
            return redirect(f"/c/{country.slug}/dish/{dish.id}/#tab-addons")

        # =====================================================================
        # 🚦 Per-branch availability — cashier-friendly, single toggle + comment
        #
        # Backward-compatible: also accepts the older "update_stoplist" action
        # name so any cached forms / external scripts keep working.
        # =====================================================================
        if action in ("update_availability", "update_stoplist"):
            if not perms["can_edit_dish_availability"]:
                return HttpResponseForbidden("Нет прав на доступность")

            location_id = request.POST.get("location_id")
            location = get_object_or_404(
                Location, id=location_id, country=country,
            )

            availability, _ = DishAvailability.objects.get_or_create(
                country=country,
                dish=dish,
                location=location,
            )

            is_available = bool(request.POST.get("is_available"))
            availability.is_available = is_available
            # Keep the legacy boolean in sync so existing code / migrations
            # that read `is_stop_list` remain consistent.
            availability.is_stop_list = not is_available
            availability.comment = (request.POST.get("comment") or "").strip()
            availability.save()

            return redirect(f"/c/{country.slug}/dish/{dish.id}/#tab-availability")

        # =====================================================================
        # ⚙ Legacy ERP actions — preserved exactly as before
        # =====================================================================
        if not user_can_edit(request.user):
            return HttpResponseForbidden("У вас нет прав на редактирование")

        if action == "save":
            if not perms["can_edit_dish_base"]:
                return HttpResponseForbidden("Нет прав на основные данные")

            dish.name = request.POST.get("name") or dish.name
            dish.final_weight = request.POST.get("final_weight") or 0
            dish.selling_price = request.POST.get("selling_price") or 0
            dish.cooking_minutes = request.POST.get("cooking_minutes") or 0
            dish.tech_card = request.POST.get("tech_card", "")

            category_id = request.POST.get("category_id")
            new_category_name = request.POST.get("new_category_name")

            if new_category_name:
                dish.category = DishCategory.objects.create(
                    country=country,
                    name=new_category_name,
                )
            elif category_id:
                dish.category = get_object_or_404(
                    DishCategory,
                    id=category_id,
                    country=country,
                )
            else:
                dish.category = None

            dish.save()

        if action == "add_step":
            description = request.POST.get("description")
            step_number = request.POST.get("step_number")

            if description:
                if not step_number:
                    last_step = DishTechStep.objects.filter(dish=dish).order_by("-step_number").first()
                    step_number = (last_step.step_number + 1) if last_step else 1

                step = DishTechStep.objects.create(
                    dish=dish,
                    step_number=step_number,
                    description=description,
                )

                if is_ajax(request):
                    return JsonResponse({
                        "ok": True,
                        "type": "step",
                        "id": step.id,
                        "step_number": step.step_number,
                        "description": step.description,
                    })

        if action == "update_step":
            step = get_object_or_404(DishTechStep, id=request.POST.get("step_id"), dish=dish)
            step.step_number = request.POST.get("step_number") or step.step_number
            step.description = request.POST.get("description") or ""
            step.save()

        if action == "delete_step":
            step = get_object_or_404(DishTechStep, id=request.POST.get("step_id"), dish=dish)
            step.delete()

        if action == "add_product":
            if not perms["can_edit_dish_finance"]:
                return HttpResponseForbidden("Нет прав на финансы блюда")
            product = get_object_or_404(Product, id=request.POST.get("product_id"), country=country)

            item = DishProductItem.objects.create(
                dish=dish,
                product=product,
                gross=clean_decimal(request.POST.get("gross")),
                net=clean_decimal(request.POST.get("net") or request.POST.get("gross")),
            )

            if is_ajax(request):
                return JsonResponse({
                    "ok": True,
                    "type": "product",
                    "id": item.id,
                    "name": item.product.name,
                    "gross": str(item.gross),
                    "net": str(item.net),
                    "cost": round(item.calculate_cost(), 2),
                    "dish_cost": round(dish.calculate_cost(), 2),
                    "foodcost": round(dish.foodcost(), 2),
                    "margin": round(dish.margin(), 2),
                })

        if action == "update_product":
            if not perms["can_edit_dish_finance"]:
                return HttpResponseForbidden("Нет прав на финансы блюда")
            item = get_object_or_404(DishProductItem, id=request.POST.get("item_id"), dish=dish)
            product = get_object_or_404(Product, id=request.POST.get("product_id"), country=country)
            item.product = product
            item.gross = clean_decimal(request.POST.get("gross"))
            item.net = clean_decimal(request.POST.get("net") or request.POST.get("gross"))
            item.save()

        if action == "delete_product":
            if not perms["can_edit_dish_finance"]:
                return HttpResponseForbidden("Нет прав на финансы блюда")
            item = get_object_or_404(DishProductItem, id=request.POST.get("item_id"), dish=dish)
            item.delete()

        if action == "add_preparation":
            if not perms["can_edit_dish_finance"]:
                return HttpResponseForbidden("Нет прав на финансы блюда")
            preparation_id = request.POST.get("preparation_id")

            if not preparation_id:
                return redirect(f"/c/{country.slug}/dish/{dish.id}/")

            preparation = get_object_or_404(
                Preparation,
                id=preparation_id,
                country=country,
            )

            item = DishPreparationItem.objects.create(
                dish=dish,
                preparation=preparation,
                gross=clean_decimal(request.POST.get("gross")),
                net=clean_decimal(request.POST.get("net") or request.POST.get("gross")),
            )

            if is_ajax(request):
                return JsonResponse({
                    "ok": True,
                    "type": "preparation",
                    "id": item.id,
                    "name": item.preparation.name,
                    "gross": str(item.gross),
                    "net": str(item.net),
                    "cost": round(item.calculate_cost(), 2),
                    "dish_cost": round(dish.calculate_cost(), 2),
                    "foodcost": round(dish.foodcost(), 2),
                    "margin": round(dish.margin(), 2),
                })

        if action == "update_preparation":
            if not perms["can_edit_dish_finance"]:
                return HttpResponseForbidden("Нет прав на финансы блюда")
            item = get_object_or_404(DishPreparationItem, id=request.POST.get("item_id"), dish=dish)
            preparation = get_object_or_404(Preparation, id=request.POST.get("preparation_id"), country=country)
            item.preparation = preparation
            item.gross = clean_decimal(request.POST.get("gross"))
            item.net = clean_decimal(request.POST.get("net") or request.POST.get("gross"))
            item.save()

        if action == "delete_preparation":
            if not perms["can_edit_dish_finance"]:
                return HttpResponseForbidden("Нет прав на финансы блюда")
            item = get_object_or_404(DishPreparationItem, id=request.POST.get("item_id"), dish=dish)
            item.delete()

        if action == "add_packaging":
            if not perms["can_edit_dish_finance"]:
                return HttpResponseForbidden("Нет прав на финансы блюда")
            packaging_id = request.POST.get("packaging_id")
            quantity = clean_decimal(request.POST.get("quantity"), "1")

            if packaging_id:
                packaging = get_object_or_404(Packaging, id=packaging_id, country=country)

                item = DishPackagingItem.objects.create(
                    dish=dish,
                    packaging=packaging,
                    quantity=quantity,
                )

                if is_ajax(request):
                    return JsonResponse({
                        "ok": True,
                        "type": "packaging",
                        "id": item.id,
                        "name": item.packaging.name,
                        "quantity": str(item.quantity),
                        "cost": round(item.calculate_cost(), 2),
                        "dish_cost": round(dish.calculate_cost(), 2),
                        "foodcost": round(dish.foodcost(), 2),
                        "margin": round(dish.margin(), 2),
                    })

        if action == "update_packaging":
            if not perms["can_edit_dish_finance"]:
                return HttpResponseForbidden("Нет прав на финансы блюда")
            item = get_object_or_404(DishPackagingItem, id=request.POST.get("item_id"), dish=dish)
            packaging = get_object_or_404(Packaging, id=request.POST.get("packaging_id"), country=country)
            item.packaging = packaging
            item.quantity = clean_decimal(request.POST.get("quantity"), "1")
            item.save()

        if action == "delete_packaging":
            if not perms["can_edit_dish_finance"]:
                return HttpResponseForbidden("Нет прав на финансы блюда")
            item = get_object_or_404(DishPackagingItem, id=request.POST.get("item_id"), dish=dish)
            item.delete()

        if action == "add_labor":
            if not perms["can_edit_dish_finance"]:
                return HttpResponseForbidden("Нет прав на финансы блюда")
            employee_id = request.POST.get("employee_id")
            minutes = clean_decimal(request.POST.get("minutes"))

            if employee_id and minutes:
                employee = get_object_or_404(Employee, id=employee_id, country=country)

                item = DishLaborItem.objects.create(
                    dish=dish,
                    employee=employee,
                    minutes=minutes,
                )

                if is_ajax(request):
                    return JsonResponse({
                        "ok": True,
                        "type": "labor",
                        "id": item.id,
                        "name": item.employee.name,
                        "minutes": str(item.minutes),
                        "cost": round(item.calculate_cost(), 2),
                        "dish_cost": round(dish.calculate_cost(), 2),
                        "foodcost": round(dish.foodcost(), 2),
                        "margin": round(dish.margin(), 2),
                    })

        if action == "update_labor":
            if not perms["can_edit_dish_finance"]:
                return HttpResponseForbidden("Нет прав на финансы блюда")
            item = get_object_or_404(DishLaborItem, id=request.POST.get("item_id"), dish=dish)
            employee = get_object_or_404(Employee, id=request.POST.get("employee_id"), country=country)
            item.employee = employee
            item.minutes = clean_decimal(request.POST.get("minutes"))
            item.save()

        if action == "delete_labor":
            if not perms["can_edit_dish_finance"]:
                return HttpResponseForbidden("Нет прав на финансы блюда")
            item = get_object_or_404(DishLaborItem, id=request.POST.get("item_id"), dish=dish)
            item.delete()

        if action == "add_extra":
            if not perms["can_edit_dish_finance"]:
                return HttpResponseForbidden("Нет прав на финансы блюда")
            comment = request.POST.get("comment")
            cost = clean_decimal(request.POST.get("cost"))

            if comment and cost:
                item = DishAdditionalExpense.objects.create(
                    dish=dish,
                    comment=comment,
                    cost=cost,
                )

                if is_ajax(request):
                    return JsonResponse({
                        "ok": True,
                        "type": "extra",
                        "id": item.id,
                        "name": item.comment,
                        "cost": round(item.cost, 2),
                        "dish_cost": round(dish.calculate_cost(), 2),
                        "foodcost": round(dish.foodcost(), 2),
                        "margin": round(dish.margin(), 2),
                    })

        if action == "update_extra":
            if not perms["can_edit_dish_finance"]:
                return HttpResponseForbidden("Нет прав на финансы блюда")
            item = get_object_or_404(DishAdditionalExpense, id=request.POST.get("item_id"), dish=dish)
            item.comment = request.POST.get("comment")
            item.cost = clean_decimal(request.POST.get("cost"))
            item.save()

        if action == "delete_extra":
            if not perms["can_edit_dish_finance"]:
                return HttpResponseForbidden("Нет прав на финансы блюда")
            item = get_object_or_404(DishAdditionalExpense, id=request.POST.get("item_id"), dish=dish)
            item.delete()

        dish.recalculate_cache()

        if is_ajax(request):
            return JsonResponse({
                "ok": True,
                "dish_cost": round(dish.cached_total_cost, 2),
                "foodcost": round(dish.cached_foodcost, 2),
                "margin": round(dish.cached_margin, 2),
            })

        return redirect(f"/c/{country.slug}/dish/{dish.id}/")

    # =========================================================================
    # GET — build display context
    # =========================================================================
    categories = DishCategory.objects.filter(country=country)
    all_categories = categories  # alias for the new website multi-select widget

    # 🚦 Per-branch availability rows
    locations_qs = Location.objects.filter(
        country=country,
        is_active=True,
    ).order_by("site_sort_order", "name")

    availability_map = {
        a.location_id: a
        for a in DishAvailability.objects.filter(country=country, dish=dish)
    }

    availability_rows = []
    for loc in locations_qs:
        rec = availability_map.get(loc.id)
        if rec is None:
            availability_rows.append({
                "location": loc,
                "availability": None,
                "is_available": True,
                "is_stop_list": False,
                "comment": "",
                "updated_at": None,
            })
        else:
            availability_rows.append({
                "location": loc,
                "availability": rec,
                "is_available": bool(rec.is_available),
                "is_stop_list": bool(rec.is_stop_list),
                "comment": rec.comment or "",
                "updated_at": rec.updated_at,
            })

    # 🖼 Gallery
    gallery_images = DishGalleryImage.objects.filter(
        dish=dish
    ).order_by("sort_order", "id")

    # ➕ Addons (Dish-as-addon)
    dish_addons = list(
        DishAddon.objects.filter(dish=dish)
        .select_related("addon_dish")
        .order_by("group_name", "sort_order", "id")
    )

    # Group addons by display group_name for the template.
    grouped = {}
    for link in dish_addons:
        key = (link.group_name or "Дополнительно").strip() or "Дополнительно"
        grouped.setdefault(key, []).append(link)
    dish_addons_by_group = sorted(grouped.items(), key=lambda kv: kv[0].lower())

    # Available addon dishes = all other dishes in the same country.
    available_addon_dishes = Dish.objects.filter(
        country=country
    ).exclude(id=dish.id).order_by("name")

    # 🏷 Public categories (Part 5 UX: attached list + remaining for dropdown).
    attached_public_categories = list(
        dish.public_categories.all().order_by("site_sort_order", "name")
    )
    attached_public_category_ids = {c.id for c in attached_public_categories}
    available_public_categories = [
        c for c in categories
        if c.id not in attached_public_category_ids
    ]

    # Gallery legacy text (back-compat: dish.gallery JSON of URLs).
    gallery_text = "\n".join(dish.gallery or []) if isinstance(dish.gallery, list) else ""

    context = {
        "country": country,
        "dish": dish,
        "categories": categories,
        "all_categories": all_categories,
        "products": products,
        "preparations": preparations,
        "employees": employees,
        "packagings": packagings,
        "tech_steps": tech_steps,
        "can_edit": user_can_edit(request.user),

        # 🌐 Part 3 context
        "locations": locations_qs,
        "availability_rows": availability_rows,
        "gallery_text": gallery_text,

        # 🌐 Part 4 context
        "gallery_images": gallery_images,
        "dish_addons": dish_addons,
        "dish_addons_by_group": dish_addons_by_group,
        "available_addon_dishes": available_addon_dishes,

        # 🏷 Part 5 context — category management
        "attached_public_categories": attached_public_categories,
        "available_public_categories": available_public_categories,

        # Permission flags
        "can_view_dish_finance": perms["can_view_dish_finance"],
        "can_edit_dish_finance": perms["can_edit_dish_finance"],
        "can_edit_dish_base": perms["can_edit_dish_base"],
        "can_edit_dish_site": perms["can_edit_dish_site"],
        "can_edit_dish_gallery": perms["can_edit_dish_gallery"],
        "can_edit_dish_addons": perms["can_edit_dish_addons"],
        "can_edit_dish_availability": perms["can_edit_dish_availability"],
        "can_edit_dish_stoplist": perms["can_edit_dish_stoplist"],  # back-compat
        "can_view_tech_card": perms["can_view_tech_card"],
    }

    return render(request, "foodcost/dish_detail.html", context)

@login_required(login_url="/login/")
def product_list(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_PRODUCTS
    )

    if access_error:
        return access_error

    create_form = ProductWithPriceForm()

    if request.method == "POST":
        if not user_can_edit(request.user):
            return HttpResponseForbidden("У вас нет прав на редактирование")

        form_type = request.POST.get("form_type")

        if form_type == "create":
            create_form = ProductWithPriceForm(request.POST)

            if create_form.is_valid():
                product = Product.objects.create(
                    country=country,
                    name=create_form.cleaned_data["name"],
                    unit=create_form.cleaned_data["unit"],
                )

                ProductPrice.objects.create(
                    product=product,
                    price=create_form.cleaned_data["price"],
                    date_from=create_form.cleaned_data["date"],
                )

                return redirect(f"/c/{country.slug}/products/")

    products = Product.objects.filter(country=country)

    return render(request, "foodcost/product_list.html", {
        "country": country,
        "products": products,
        "create_form": create_form,
        "can_edit": user_can_edit(request.user),
    })

@login_required(login_url="/login/")
def product_detail(request, country_slug, product_id):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_PRODUCTS
    )

    if access_error:
        return access_error

    product = get_object_or_404(Product, id=product_id, country=country)

    if request.method == "POST":
        if not user_can_edit(request.user):
            return HttpResponseForbidden("У вас нет прав на редактирование")

        action = request.POST.get("action")

        if action == "save":
            product.name = request.POST.get("name")
            product.unit = request.POST.get("unit")
            product.save()

            price = request.POST.get("price")
            date = request.POST.get("date")

            if price and date:
                ProductPrice.objects.create(
                    product=product,
                    price=price,
                    date_from=date,
                )

            return redirect(f"/c/{country.slug}/products/{product.id}/")

        if action == "delete":
            product.delete()
            return redirect(f"/c/{country.slug}/products/")

    prices = product.prices.order_by("date_from")

    latest_prices = list(product.prices.order_by("-date_from")[:2])
    current_price = latest_prices[0].price if len(latest_prices) >= 1 else None
    previous_price = latest_prices[1].price if len(latest_prices) >= 2 else None

    dish_items = DishProductItem.objects.filter(
        product=product,
        dish__country=country,
    ).select_related("dish")

    preparation_items = PreparationItem.objects.filter(
        product=product,
        preparation__country=country,
    ).select_related("preparation")

    affected_dishes = []

    for item in dish_items:
        current_item_cost = item.calculate_cost()

        previous_item_cost = None
        difference = None
        previous_dish_cost = None
        previous_foodcost = None

        if current_price is not None and previous_price is not None:
            previous_item_cost = item.net * previous_price
            difference = current_item_cost - previous_item_cost
            previous_dish_cost = item.dish.calculate_cost() - difference

            if item.dish.selling_price:
                previous_foodcost = (item.dish.ingredient_cost() - difference) / item.dish.selling_price * 100

        affected_dishes.append({
            "type": "Блюдо напрямую",
            "name": item.dish.name,
            "url": f"/c/{country.slug}/dish/{item.dish.id}/",
            "quantity": item.net,
            "unit": item.unit_label(),
            "cost": current_item_cost,
            "previous_cost": previous_item_cost,
            "difference": difference,
            "dish_cost": item.dish.calculate_cost(),
            "previous_dish_cost": previous_dish_cost,
            "foodcost": item.dish.foodcost(),
            "previous_foodcost": previous_foodcost,
        })

    for prep_item in preparation_items:
        preparation = prep_item.preparation

        current_prep_item_cost = prep_item.calculate_cost()

        previous_prep_item_cost = None
        prep_difference = None

        if current_price is not None and previous_price is not None:
            previous_prep_item_cost = prep_item.net * previous_price
            prep_difference = current_prep_item_cost - previous_prep_item_cost

        for dish_prep_item in DishPreparationItem.objects.filter(
            preparation=preparation,
            dish__country=country,
        ).select_related("dish"):
            current_item_cost = dish_prep_item.calculate_cost()

            previous_item_cost = None
            difference = None
            previous_dish_cost = None
            previous_foodcost = None

            if (
                current_price is not None
                and previous_price is not None
                and prep_difference is not None
                and preparation.final_weight
            ):
                difference_per_kg = prep_difference / preparation.final_weight
                difference = dish_prep_item.net * difference_per_kg
                previous_item_cost = current_item_cost - difference
                previous_dish_cost = dish_prep_item.dish.calculate_cost() - difference

                if dish_prep_item.dish.selling_price:
                    previous_foodcost = (
                        dish_prep_item.dish.ingredient_cost() - difference
                    ) / dish_prep_item.dish.selling_price * 100

            affected_dishes.append({
                "type": f"Через заготовку: {preparation.name}",
                "name": dish_prep_item.dish.name,
                "url": f"/c/{country.slug}/dish/{dish_prep_item.dish.id}/",
                "quantity": dish_prep_item.net,
                "unit": "кг",
                "cost": current_item_cost,
                "previous_cost": previous_item_cost,
                "difference": difference,
                "dish_cost": dish_prep_item.dish.calculate_cost(),
                "previous_dish_cost": previous_dish_cost,
                "foodcost": dish_prep_item.dish.foodcost(),
                "previous_foodcost": previous_foodcost,
            })

    return render(request, "foodcost/product_detail.html", {
        "country": country,
        "product": product,
        "prices": prices,
        "current_price": current_price,
        "previous_price": previous_price,
        "affected_dishes": affected_dishes,
        "preparation_items": preparation_items,
        "dish_items": dish_items,
        "can_edit": user_can_edit(request.user),
    })
@login_required(login_url="/login/")
def preparation_list(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_PREPARATIONS
    )

    if access_error:
        return access_error

    if request.method == "POST":
        if not user_can_edit(request.user):
            return HttpResponseForbidden("У вас нет прав на редактирование")

        name = request.POST.get("name")
        final_weight = request.POST.get("final_weight") or 0
        cooking_minutes = request.POST.get("cooking_minutes") or 0

        if name:
            Preparation.objects.create(
                country=country,
                name=name,
                final_weight=final_weight,
                cooking_minutes=cooking_minutes,
            )

        return redirect(f"/c/{country.slug}/preparations/")

    preparations = Preparation.objects.filter(country=country)

    return render(request, "foodcost/preparation_list.html", {
        "country": country,
        "preparations": preparations,
        "can_edit": user_can_edit(request.user),
    })

@login_required(login_url="/login/")
def preparation_detail(request, country_slug, prep_id):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_PREPARATIONS
    )

    if access_error:
        return access_error

    preparation = get_object_or_404(Preparation, id=prep_id, country=country)

    products = Product.objects.filter(country=country)
    preparations = Preparation.objects.filter(country=country).exclude(id=preparation.id)

    if request.method == "POST":
        if not user_can_edit(request.user):
            return HttpResponseForbidden("У вас нет прав на редактирование")

        action = request.POST.get("action")

        if action == "save":
            preparation.name = request.POST.get("name")
            preparation.final_weight = request.POST.get("final_weight")
            preparation.cooking_minutes = request.POST.get("cooking_minutes") or 0
            preparation.save()

        if action == "add_item":
            product = get_object_or_404(
                Product,
                id=request.POST.get("product_id"),
                country=country,
            )

            PreparationItem.objects.create(
                preparation=preparation,
                product=product,
                gross=request.POST.get("gross") or 0,
                net=request.POST.get("net") or request.POST.get("gross") or 0,
            )

        if action == "add_subitem":
            sub_preparation = get_object_or_404(
                Preparation,
                id=request.POST.get("sub_preparation_id"),
                country=country,
            )

            PreparationSubItem.objects.create(
                preparation=preparation,
                sub_preparation=sub_preparation,
                gross=request.POST.get("gross") or 0,
                net=request.POST.get("net") or request.POST.get("gross") or 0,
            )

        if action == "update_item":
            item = get_object_or_404(
                PreparationItem,
                id=request.POST.get("item_id"),
                preparation=preparation,
            )

            product = get_object_or_404(
                Product,
                id=request.POST.get("product_id"),
                country=country,
            )

            item.product = product
            item.gross = request.POST.get("gross") or 0
            item.net = request.POST.get("net") or item.gross
            item.save()

        if action == "update_subitem":
            item = get_object_or_404(
                PreparationSubItem,
                id=request.POST.get("item_id"),
                preparation=preparation,
            )

            sub_preparation = get_object_or_404(
                Preparation,
                id=request.POST.get("sub_preparation_id"),
                country=country,
            )

            item.sub_preparation = sub_preparation
            item.gross = request.POST.get("gross") or 0
            item.net = request.POST.get("net") or item.gross
            item.save()

        if action == "delete_item":
            item = get_object_or_404(
                PreparationItem,
                id=request.POST.get("item_id"),
                preparation=preparation,
            )

            item.delete()

        if action == "delete_subitem":
            item = get_object_or_404(
                PreparationSubItem,
                id=request.POST.get("item_id"),
                preparation=preparation,
            )

            item.delete()

        return redirect(f"/c/{country.slug}/preparations/{preparation.id}/")

    total_gross = (
        sum(item.gross for item in preparation.items.all())
        + sum(item.gross for item in preparation.subitems.all())
    )

    total_net = (
        sum(item.net for item in preparation.items.all())
        + sum(item.net for item in preparation.subitems.all())
    )

    return render(request, "foodcost/preparation_detail.html", {
        "country": country,
        "preparation": preparation,
        "products": products,
        "preparations": preparations,
        "total_gross": total_gross,
        "total_net": total_net,
        "can_edit": user_can_edit(request.user),
    })

@login_required(login_url="/login/")
def employee_list(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_EMPLOYEES
    )

    if access_error:
        return access_error

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create":
            name = request.POST.get("name")
            monthly_salary = request.POST.get("monthly_salary")
            monthly_hours = request.POST.get("monthly_hours")

            if name and monthly_salary and monthly_hours:
                Employee.objects.create(
                    country=country,
                    name=name,
                    monthly_salary=monthly_salary,
                    monthly_hours=monthly_hours,
                )

        if action == "update":
            employee = get_object_or_404(Employee, id=request.POST.get("employee_id"), country=country)
            employee.name = request.POST.get("name")
            employee.monthly_salary = request.POST.get("monthly_salary") or 0
            employee.monthly_hours = request.POST.get("monthly_hours") or 0
            employee.save()

        if action == "delete":
            employee = get_object_or_404(Employee, id=request.POST.get("employee_id"), country=country)
            employee.delete()

        return redirect(f"/c/{country.slug}/employees/")

    employees = Employee.objects.filter(country=country)

    return render(request, "foodcost/employee_list.html", {
        "country": country,
        "employees": employees,
    })


@login_required(login_url="/login/")
def packaging_list(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_PACKAGING
    )

    if access_error:
        return access_error

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create":
            name = request.POST.get("name")
            cost = request.POST.get("cost")

            if name and cost:
                Packaging.objects.create(
                    country=country,
                    name=name,
                    cost=cost,
                )

        if action == "update":
            packaging = get_object_or_404(Packaging, id=request.POST.get("packaging_id"), country=country)
            packaging.name = request.POST.get("name")
            packaging.cost = request.POST.get("cost")
            packaging.save()

        return redirect(f"/c/{country.slug}/packaging/")

    packagings = Packaging.objects.filter(country=country)

    return render(request, "foodcost/packaging_list.html", {
        "country": country,
        "packagings": packagings,
    })


@login_required(login_url="/login/")
def utilities_list(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_UTILITIES
    )

    if access_error:
        return access_error

    if request.method == "POST":
        MonthlyUtilityExpense.objects.create(
            country=country,
            month=request.POST.get("month"),
            water=request.POST.get("water") or 0,
            electricity=request.POST.get("electricity") or 0,
            rent=request.POST.get("rent") or 0,
            working_hours=request.POST.get("working_hours") or 1,
        )

        return redirect(f"/c/{country.slug}/utilities/")

    utilities = MonthlyUtilityExpense.objects.filter(country=country).order_by("-month")

    return render(request, "foodcost/utilities_list.html", {
        "country": country,
        "utilities": utilities,
    })
    
  
    
@login_required(login_url="/login/")
def user_access_list(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_USERS
    )

    if access_error:
        return access_error

    if not request.user.is_authenticated:
        return HttpResponseForbidden("Нет доступа")

    if not request.user.is_superuser:
        return HttpResponseForbidden("Только главный админ может управлять пользователями")

    error = None

    for user in User.objects.all():
        UserProfile.objects.get_or_create(user=user)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create_location":
            location_name = (request.POST.get("location_name") or "").strip()

            if not location_name:
                error = "Укажи название филиала"
            else:
                # Telegram thread id — BigIntegerField, accepts empty.
                tg_raw = (request.POST.get("telegram_thread_id") or "").strip()
                try:
                    tg_thread_id = int(tg_raw) if tg_raw else None
                except (TypeError, ValueError):
                    tg_thread_id = None

                # site_sort_order — PositiveIntegerField, accepts blank.
                try:
                    sort_order = int(request.POST.get("site_sort_order") or 0)
                except (TypeError, ValueError):
                    sort_order = 0
                if sort_order < 0:
                    sort_order = 0

                Location.objects.create(
                    country=country,
                    name=location_name,
                    telegram_thread_id=tg_thread_id,
                    public_name=(request.POST.get("public_name") or "").strip(),
                    address=(request.POST.get("address") or "").strip(),
                    phone=(request.POST.get("phone") or "").strip(),
                    latitude=_parse_optional_decimal(request.POST.get("latitude")),
                    longitude=_parse_optional_decimal(request.POST.get("longitude")),
                    working_hours=(request.POST.get("working_hours") or "").strip(),
                    site_sort_order=sort_order,
                    # NB: HTML checkbox is present only when checked.
                    is_active=bool(request.POST.get("is_active")),
                    is_visible_on_site=bool(request.POST.get("is_visible_on_site")),
                    supports_pickup=bool(request.POST.get("supports_pickup")),
                    supports_delivery=bool(request.POST.get("supports_delivery")),
                )

                return redirect(f"/c/{country.slug}/users/")

        if action == "update_location":
            item = get_object_or_404(
                Location,
                id=request.POST.get("location_id"),
                country=country,
            )

            new_name = (request.POST.get("location_name") or "").strip()
            if not new_name:
                error = "Название филиала не может быть пустым"
            else:
                item.name = new_name

                tg_raw = (request.POST.get("telegram_thread_id") or "").strip()
                try:
                    item.telegram_thread_id = int(tg_raw) if tg_raw else None
                except (TypeError, ValueError):
                    item.telegram_thread_id = None

                item.public_name = (request.POST.get("public_name") or "").strip()
                item.address = (request.POST.get("address") or "").strip()
                item.phone = (request.POST.get("phone") or "").strip()
                item.latitude = _parse_optional_decimal(request.POST.get("latitude"))
                item.longitude = _parse_optional_decimal(request.POST.get("longitude"))
                item.working_hours = (request.POST.get("working_hours") or "").strip()

                try:
                    sort_order = int(request.POST.get("site_sort_order") or 0)
                except (TypeError, ValueError):
                    sort_order = 0
                if sort_order < 0:
                    sort_order = 0
                item.site_sort_order = sort_order

                item.is_active = bool(request.POST.get("is_active"))
                item.is_visible_on_site = bool(request.POST.get("is_visible_on_site"))
                item.supports_pickup = bool(request.POST.get("supports_pickup"))
                item.supports_delivery = bool(request.POST.get("supports_delivery"))
                item.save()

                return redirect(f"/c/{country.slug}/users/")

        if action == "delete_location":
            item = get_object_or_404(
                Location,
                id=request.POST.get("location_id"),
                country=country,
            )
            # Soft safety: if any UserProfile points at this location, refuse.
            # Order.location uses on_delete=SET_NULL so deletion is safe for
            # historical orders — they keep their data, just lose the FK.
            if UserProfile.objects.filter(location=item).exists():
                error = (
                    "Нельзя удалить филиал: к нему привязаны пользователи. "
                    "Сначала отвяжите их."
                )
            else:
                item.delete()
                return redirect(f"/c/{country.slug}/users/")

        if action == "create_user":
            username = request.POST.get("username")
            password = request.POST.get("password")
            role = request.POST.get("role")
            country_ids = request.POST.getlist("countries")

            allowed_sections = request.POST.getlist("allowed_sections")

            location_id = request.POST.get("location_id")
            

            if not username or not password or not role:
                error = "Заполни логин, пароль и роль"
            elif User.objects.filter(username=username).exists():
                error = "Пользователь с таким логином уже существует"
            else:
                user = User.objects.create_user(
                    username=username,
                    password=password,
                )

                profile, created = UserProfile.objects.get_or_create(user=user)
                profile.role = role
                profile.allowed_sections = allowed_sections
                profile.location_id = location_id or None
                profile.save()
                profile.countries.set(country_ids)

                return redirect(f"/c/{country.slug}/users/")

        if action == "update_user":
            user = get_object_or_404(User, id=request.POST.get("user_id"))
            profile, created = UserProfile.objects.get_or_create(user=user)

            username = request.POST.get("username")
            password = request.POST.get("password")
            role = request.POST.get("role")
            country_ids = request.POST.getlist("countries")
            allowed_sections = request.POST.getlist("allowed_sections")
            location_id = request.POST.get("location_id")

            if username:
                user.username = username

            if password:
                user.set_password(password)

            user.save()

            if role:

                profile.role = role

            profile.allowed_sections = allowed_sections

            profile.location_id = location_id or None

            profile.save()
            profile.countries.set(country_ids)
            

            return redirect(f"/c/{country.slug}/users/")

    users = User.objects.all().order_by("username")

    countries = Country.objects.all().order_by("name")

    locations = Location.objects.filter(country=country).order_by(
        "site_sort_order", "name"
    )

    return render(request, "foodcost/user_access_list.html", {
        "country": country,
        "users": users,
        "countries": countries,
        "locations": locations,
        "roles": UserProfile.ROLE_CHOICES,
        "sections": UserProfile.SECTION_CHOICES,
        "error": error,
    })
    
def login_page(request):
    error = None

    if request.method == "POST":
        username = request.POST.get("username")
        password = request.POST.get("password")

        user = authenticate(
            request,
            username=username,
            password=password
        )

        if user is not None:
            login(request, user)
            return redirect("/")
        else:
            error = "Неверный логин или пароль"

    return render(request, "foodcost/login.html", {
        "error": error,
    })


def logout_page(request):
    logout(request)
    return redirect("/login/")
    
    
    
@csrf_exempt
def tilda_webhook(request):

    if request.method != "POST":
        return JsonResponse({
            "success": False,
            "error": "POST only"
        })

    country = Country.objects.get(slug="uzbekistan")

    name = request.POST.get("Name", "").strip()
    phone = request.POST.get("Phone", "").strip()
    address = request.POST.get("Адрес_доставки", "").strip()

    payment_raw = request.POST.get("payment", "{}")

    try:
        payment_data = json.loads(payment_raw)
    except Exception:
        payment_data = {}

    subtotal_amount = Decimal(payment_data.get("subtotal") or "0")
    total_amount = Decimal(payment_data.get("amount") or "0")
    discount_amount = Decimal(payment_data.get("discount") or "0")
    promocode_value = payment_data.get("promocode") or ""

    customer, created = Customer.objects.get_or_create(
        country=country,
        phone=phone,
        defaults={
            "name": name or phone,
        }
    )

    customer.name = name or customer.name
    customer.save()

    if address:
        CustomerAddress.objects.get_or_create(
            customer=customer,
            address=address,
            defaults={
                "is_default": not customer.addresses.exists(),
            }
        )

    source, created = OrderSource.objects.get_or_create(
        country=country,
        name="Сайт",
        defaults={
            "is_active": True,
        }
    )

    promo_code = None

    if promocode_value:
        promo_code = PromoCode.objects.filter(
            country=country,
            code=promocode_value
        ).first()

    order = Order.objects.create(
        country=country,
        customer=customer,
        source=source,
        promo_code=promo_code,
        order_date=timezone.now(),

        customer_name=name,
        customer_phone=phone,
        customer_telegram="",

        delivery_address=address,
        cashier_comment="Заказ с сайта Tilda",

        subtotal_amount=subtotal_amount,
        discount_amount=discount_amount,
        delivery_amount=0,
        total_amount=total_amount,
    )

    products = payment_data.get("products") or []

    for product_line in products:

        if "=" not in product_line:
            continue

        dish_name, price_raw = product_line.rsplit("=", 1)

        dish_name = dish_name.strip()
        price = Decimal(price_raw or "0")

        dish = Dish.objects.filter(
            country=country,
            name__iexact=dish_name
        ).first()

        if not dish:
            order.cashier_comment += f"\nНе найдено блюдо: {dish_name}"
            order.save()
            continue

        OrderItem.objects.create(
            order=order,
            dish=dish,
            quantity=1,
            price_snapshot=price,
            cost_snapshot=dish.calculate_cost(),
            total_price=price,
        )

    return JsonResponse({
        "success": True,
        "order_id": order.id
    })