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
)


def is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"

def clean_decimal(value, default="0"):
    if value is None or value == "":
        return default
    return str(value).replace(",", ".")

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

@login_required(login_url="/login/")
def dish_detail(request, country_slug, dish_id):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(
        request.user,
        UserProfile.SECTION_DISHES
    )

    if access_error:
        return access_error

    dish = get_object_or_404(Dish, id=dish_id, country=country)

    products = Product.objects.filter(country=country)
    preparations = Preparation.objects.filter(country=country)
    employees = Employee.objects.filter(country=country)
    packagings = Packaging.objects.filter(country=country)
    tech_steps = DishTechStep.objects.filter(dish=dish)

    if request.method == "POST":
        if not user_can_edit(request.user):
            return HttpResponseForbidden("У вас нет прав на редактирование")

        action = request.POST.get("action")

        if action == "save":
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
            item = get_object_or_404(DishProductItem, id=request.POST.get("item_id"), dish=dish)
            product = get_object_or_404(Product, id=request.POST.get("product_id"), country=country)
            item.product = product
            item.gross = clean_decimal(request.POST.get("gross"))
            item.net = clean_decimal(request.POST.get("net") or request.POST.get("gross"))
            item.save()

        if action == "delete_product":
            item = get_object_or_404(DishProductItem, id=request.POST.get("item_id"), dish=dish)
            item.delete()

        if action == "add_preparation":
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
            item = get_object_or_404(DishPreparationItem, id=request.POST.get("item_id"), dish=dish)
            preparation = get_object_or_404(Preparation, id=request.POST.get("preparation_id"), country=country)
            item.preparation = preparation
            item.gross = clean_decimal(request.POST.get("gross"))
            item.net = clean_decimal(request.POST.get("net") or request.POST.get("gross"))
            item.save()

        if action == "delete_preparation":
            item = get_object_or_404(DishPreparationItem, id=request.POST.get("item_id"), dish=dish)
            item.delete()

        if action == "add_packaging":
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
            item = get_object_or_404(DishPackagingItem, id=request.POST.get("item_id"), dish=dish)
            packaging = get_object_or_404(Packaging, id=request.POST.get("packaging_id"), country=country)
            item.packaging = packaging
            item.quantity = clean_decimal(request.POST.get("quantity"), "1")
            item.save()

        if action == "delete_packaging":
            item = get_object_or_404(DishPackagingItem, id=request.POST.get("item_id"), dish=dish)
            item.delete()

        if action == "add_labor":
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
            item = get_object_or_404(DishLaborItem, id=request.POST.get("item_id"), dish=dish)
            employee = get_object_or_404(Employee, id=request.POST.get("employee_id"), country=country)
            item.employee = employee
            item.minutes = clean_decimal(request.POST.get("minutes"))
            item.save()

        if action == "delete_labor":
            item = get_object_or_404(DishLaborItem, id=request.POST.get("item_id"), dish=dish)
            item.delete()

        if action == "add_extra":
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
            item = get_object_or_404(DishAdditionalExpense, id=request.POST.get("item_id"), dish=dish)
            item.comment = request.POST.get("comment")
            item.cost = clean_decimal(request.POST.get("cost"))
            item.save()

        if action == "delete_extra":
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

    categories = DishCategory.objects.filter(country=country)

    return render(request, "foodcost/dish_detail.html", {
        "country": country,
        "dish": dish,
        "categories": categories,
        "products": products,
        "preparations": preparations,
        "employees": employees,
        "packagings": packagings,
        "tech_steps": tech_steps,
        "can_edit": user_can_edit(request.user),
    })
    
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
            location_name = request.POST.get("location_name")
            telegram_thread_id = request.POST.get("telegram_thread_id")

            if not location_name:
                error = "Укажи название филиала"
            else:
                Location.objects.create(
                    country=country,
                    name=location_name,
                    telegram_thread_id=telegram_thread_id or None,
                )

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

    locations = Location.objects.all().order_by("name")

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
    
    
    
    
from django.http import HttpResponse
from foodcost.models import Order
from decimal import Decimal

orders = Order.objects.select_related("source").all()

for order in orders:

    food_total = (
        order.subtotal_amount
        - order.discount_amount
    )

    customer_delivery_amount = Decimal("0")

    if order.source and order.source.name.lower() == "сайт":
        if food_total > 0 and food_total < Decimal("150000"):
            customer_delivery_amount = Decimal("15000")

    total_amount = food_total + customer_delivery_amount

    commission_amount = Decimal("0")

    if order.source:
        commission_amount = (
            total_amount
            * order.source.commission_percent
            / Decimal("100")
        )

    net_revenue = total_amount - commission_amount

    order.customer_delivery_amount = customer_delivery_amount
    order.total_amount = total_amount
    order.commission_amount = commission_amount
    order.net_revenue = net_revenue

    order.save(
        update_fields=[
            "customer_delivery_amount",
            "total_amount",
            "commission_amount",
            "net_revenue",
        ]
    )

return HttpResponse("DONE")