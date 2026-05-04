from decimal import Decimal

from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404

from .forms import ProductWithPriceForm
from .models import (
    Country,
    Product,
    ProductPrice,
    Dish,
    DishTechStep,
    Preparation,
    PreparationItem,
    DishProductItem,
    DishPreparationItem,
    Employee,
    Packaging,
    DishPackagingItem,
    DishLaborItem,
    DishAdditionalExpense,
    MonthlyUtilityExpense,
)


def is_ajax(request):
    return request.headers.get("x-requested-with") == "XMLHttpRequest"

def clean_decimal(value, default="0"):
    if value is None or value == "":
        return default
    return str(value).replace(",", ".")

def get_country(country_slug):
    return get_object_or_404(Country, slug=country_slug)


def country_list(request):
    countries = Country.objects.all()
    return render(request, "foodcost/country_list.html", {
        "countries": countries,
    })


def dish_create(request, country_slug):
    country = get_country(country_slug)

    dish = Dish.objects.create(
        country=country,
        name="Новое блюдо",
        final_weight=0,
        selling_price=0,
        cooking_minutes=0,
    )

    return redirect(f"/c/{country.slug}/dish/{dish.id}/")


def dish_list(request, country_slug):
    country = get_country(country_slug)

    dishes = list(Dish.objects.filter(country=country))

    filter_type = request.GET.get("filter", "all")
    sort_type = request.GET.get("sort", "name")

    if filter_type == "loss":
        dishes = [dish for dish in dishes if dish.margin() < 0]

    if filter_type == "high_foodcost":
        dishes = [dish for dish in dishes if dish.foodcost() > 40]

    if filter_type == "normal":
        dishes = [dish for dish in dishes if dish.foodcost() <= 40 and dish.margin() >= 0]

    if sort_type == "margin":
        dishes.sort(key=lambda dish: dish.margin(), reverse=True)
    elif sort_type == "foodcost":
        dishes.sort(key=lambda dish: dish.foodcost(), reverse=True)
    elif sort_type == "cost":
        dishes.sort(key=lambda dish: dish.calculate_cost(), reverse=True)
    else:
        dishes.sort(key=lambda dish: dish.name.lower())

    return render(request, "foodcost/dish_list.html", {
        "country": country,
        "dishes": dishes,
        "filter_type": filter_type,
        "sort_type": sort_type,
    })


def live_calculate(request, country_slug):
    country = get_country(country_slug)

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


def dish_detail(request, country_slug, dish_id):
    country = get_country(country_slug)
    dish = get_object_or_404(Dish, id=dish_id, country=country)

    products = Product.objects.filter(country=country)
    preparations = Preparation.objects.filter(country=country)
    employees = Employee.objects.filter(country=country)
    packagings = Packaging.objects.filter(country=country)
    tech_steps = DishTechStep.objects.filter(dish=dish)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "save":
            dish.name = request.POST.get("name") or dish.name
            dish.final_weight = request.POST.get("final_weight") or 0
            dish.selling_price = request.POST.get("selling_price") or 0
            dish.cooking_minutes = request.POST.get("cooking_minutes") or 0
            dish.tech_card = request.POST.get("tech_card", "")
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
            preparation = get_object_or_404(Preparation, id=request.POST.get("preparation_id"), country=country)

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

        if is_ajax(request):
            return JsonResponse({
                "ok": True,
                "dish_cost": round(dish.calculate_cost(), 2),
                "foodcost": round(dish.foodcost(), 2),
                "margin": round(dish.margin(), 2),
            })

        return redirect(f"/c/{country.slug}/dish/{dish.id}/")

    return render(request, "foodcost/dish_detail.html", {
        "country": country,
        "dish": dish,
        "products": products,
        "preparations": preparations,
        "employees": employees,
        "packagings": packagings,
        "tech_steps": tech_steps,
    })


def product_list(request, country_slug):
    country = get_country(country_slug)
    create_form = ProductWithPriceForm()

    if request.method == "POST":
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
    })


def product_detail(request, country_slug, product_id):
    country = get_country(country_slug)
    product = get_object_or_404(Product, id=product_id, country=country)

    if request.method == "POST":
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
        affected_dishes.append({
            "type": "Блюдо напрямую",
            "name": item.dish.name,
            "url": f"/c/{country.slug}/dish/{item.dish.id}/",
            "quantity": item.gross,
            "cost": item.calculate_cost(),
            "dish_cost": item.dish.calculate_cost(),
            "foodcost": item.dish.foodcost(),
        })

    for prep_item in preparation_items:
        preparation = prep_item.preparation

        for dish_prep_item in DishPreparationItem.objects.filter(
            preparation=preparation,
            dish__country=country,
        ).select_related("dish"):
            affected_dishes.append({
                "type": f"Через заготовку: {preparation.name}",
                "name": dish_prep_item.dish.name,
                "url": f"/c/{country.slug}/dish/{dish_prep_item.dish.id}/",
                "quantity": dish_prep_item.gross,
                "cost": dish_prep_item.calculate_cost(),
                "dish_cost": dish_prep_item.dish.calculate_cost(),
                "foodcost": dish_prep_item.dish.foodcost(),
            })

    return render(request, "foodcost/product_detail.html", {
        "country": country,
        "product": product,
        "prices": prices,
        "affected_dishes": affected_dishes,
        "preparation_items": preparation_items,
        "dish_items": dish_items,
    })


def preparation_list(request, country_slug):
    country = get_country(country_slug)

    if request.method == "POST":
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
    })


def preparation_detail(request, country_slug, prep_id):
    country = get_country(country_slug)
    preparation = get_object_or_404(Preparation, id=prep_id, country=country)
    products = Product.objects.filter(country=country)

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "save":
            preparation.name = request.POST.get("name")
            preparation.final_weight = request.POST.get("final_weight")
            preparation.cooking_minutes = request.POST.get("cooking_minutes") or 0
            preparation.save()

        if action == "add_item":
            product = get_object_or_404(Product, id=request.POST.get("product_id"), country=country)
            PreparationItem.objects.create(
                preparation=preparation,
                product=product,
                gross=request.POST.get("gross") or 0,
                net=request.POST.get("net") or request.POST.get("gross") or 0,
            )

        if action == "update_item":
            item = get_object_or_404(PreparationItem, id=request.POST.get("item_id"), preparation=preparation)
            product = get_object_or_404(Product, id=request.POST.get("product_id"), country=country)
            item.product = product
            item.gross = request.POST.get("gross") or 0
            item.net = request.POST.get("net") or item.gross
            item.save()

        if action == "delete_item":
            item = get_object_or_404(PreparationItem, id=request.POST.get("item_id"), preparation=preparation)
            item.delete()

        return redirect(f"/c/{country.slug}/preparations/{preparation.id}/")

    total_gross = sum(item.gross for item in preparation.items.all())
    total_net = sum(item.net for item in preparation.items.all())

    return render(request, "foodcost/preparation_detail.html", {
        "country": country,
        "preparation": preparation,
        "products": products,
        "total_gross": total_gross,
        "total_net": total_net,
    })


def employee_list(request, country_slug):
    country = get_country(country_slug)

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


def packaging_list(request, country_slug):
    country = get_country(country_slug)

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


def utilities_list(request, country_slug):
    country = get_country(country_slug)

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