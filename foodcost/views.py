from decimal import Decimal
from django.http import JsonResponse
from django.shortcuts import render, redirect, get_object_or_404

from .forms import ProductWithPriceForm
from .models import (
    Product,
    ProductPrice,
    Dish,
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


def dish_list(request):
    dishes = Dish.objects.all()
    return render(request, "foodcost/dish_list.html", {"dishes": dishes})


def live_calculate(request):
    item_type = request.GET.get("type")
    item_id = request.GET.get("id")
    quantity = Decimal(request.GET.get("quantity") or "0")

    cost = Decimal("0")

    if item_type == "product":
        product = get_object_or_404(Product, id=item_id)
        price = product.get_price()
        if price:
            cost = quantity * price.price

    if item_type == "preparation":
        preparation = get_object_or_404(Preparation, id=item_id)
        cost = quantity * preparation.cost_per_kg()

    if item_type == "packaging":
        packaging = get_object_or_404(Packaging, id=item_id)
        cost = quantity * packaging.cost

    if item_type == "labor":
        employee = get_object_or_404(Employee, id=item_id)
        cost = quantity * employee.minute_rate()

    return JsonResponse({"cost": round(cost, 2)})


def dish_detail(request, pk):
    dish = get_object_or_404(Dish, pk=pk)
    products = Product.objects.all()
    preparations = Preparation.objects.all()
    employees = Employee.objects.all()
    packagings = Packaging.objects.all()

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "save":
            dish.name = request.POST.get("name")
            dish.final_weight = request.POST.get("final_weight")
            dish.selling_price = request.POST.get("selling_price")
            dish.cooking_minutes = request.POST.get("cooking_minutes") or 0
            dish.save()

        if action == "add_product":
            DishProductItem.objects.create(
                dish=dish,
                product_id=request.POST.get("product_id"),
                gross=request.POST.get("gross"),
                net=request.POST.get("net") or request.POST.get("gross"),
            )

        if action == "update_product":
            item = get_object_or_404(DishProductItem, id=request.POST.get("item_id"))
            item.product_id = request.POST.get("product_id")
            item.gross = request.POST.get("gross") or 0
            item.net = request.POST.get("net") or item.gross
            item.save()

        if action == "delete_product":
            item = get_object_or_404(DishProductItem, id=request.POST.get("item_id"))
            item.delete()

        if action == "add_preparation":
            DishPreparationItem.objects.create(
                dish=dish,
                preparation_id=request.POST.get("preparation_id"),
                gross=request.POST.get("gross"),
                net=request.POST.get("net") or request.POST.get("gross"),
            )

        if action == "update_preparation":
            item = get_object_or_404(DishPreparationItem, id=request.POST.get("item_id"))
            item.preparation_id = request.POST.get("preparation_id")
            item.gross = request.POST.get("gross") or 0
            item.net = request.POST.get("net") or item.gross
            item.save()

        if action == "delete_preparation":
            item = get_object_or_404(DishPreparationItem, id=request.POST.get("item_id"))
            item.delete()

        if action == "add_packaging":
            packaging_id = request.POST.get("packaging_id")
            quantity = request.POST.get("quantity") or 1

            if packaging_id:
                DishPackagingItem.objects.create(
                    dish=dish,
                    packaging_id=packaging_id,
                    quantity=quantity,
                )

        if action == "update_packaging":
            item = get_object_or_404(DishPackagingItem, id=request.POST.get("item_id"))
            item.packaging_id = request.POST.get("packaging_id")
            item.quantity = request.POST.get("quantity") or 1
            item.save()

        if action == "delete_packaging":
            item = get_object_or_404(DishPackagingItem, id=request.POST.get("item_id"))
            item.delete()

        if action == "add_labor":
            employee_id = request.POST.get("employee_id")
            minutes = request.POST.get("minutes")

            if employee_id and minutes:
                DishLaborItem.objects.create(
                    dish=dish,
                    employee_id=employee_id,
                    minutes=minutes,
                )

        if action == "update_labor":
            item = get_object_or_404(DishLaborItem, id=request.POST.get("item_id"))
            item.employee_id = request.POST.get("employee_id")
            item.minutes = request.POST.get("minutes") or 0
            item.save()

        if action == "delete_labor":
            item = get_object_or_404(DishLaborItem, id=request.POST.get("item_id"))
            item.delete()

        if action == "add_extra":
            comment = request.POST.get("comment")
            cost = request.POST.get("cost")

            if comment and cost:
                DishAdditionalExpense.objects.create(
                    dish=dish,
                    comment=comment,
                    cost=cost,
                )

        if action == "update_extra":
            item = get_object_or_404(DishAdditionalExpense, id=request.POST.get("item_id"))
            item.comment = request.POST.get("comment")
            item.cost = request.POST.get("cost") or 0
            item.save()

        if action == "delete_extra":
            item = get_object_or_404(DishAdditionalExpense, id=request.POST.get("item_id"))
            item.delete()

        return redirect(f"/dishes/{dish.id}/")

    return render(request, "foodcost/dish_detail.html", {
        "dish": dish,
        "products": products,
        "preparations": preparations,
        "employees": employees,
        "packagings": packagings,
    })


def product_list(request):
    create_form = ProductWithPriceForm()

    if request.method == "POST":
        form_type = request.POST.get("form_type")

        if form_type == "create":
            create_form = ProductWithPriceForm(request.POST)
            if create_form.is_valid():
                product = Product.objects.create(
                    name=create_form.cleaned_data["name"],
                    unit=create_form.cleaned_data["unit"],
                )

                ProductPrice.objects.create(
                    product=product,
                    price=create_form.cleaned_data["price"],
                    date_from=create_form.cleaned_data["date"],
                )

                return redirect("/products/")

    products = Product.objects.all()

    return render(request, "foodcost/product_list.html", {
        "products": products,
        "create_form": create_form,
    })


def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk)

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

            return redirect(f"/products/{product.id}/")

        if action == "delete":
            product.delete()
            return redirect("/products/")

    prices = product.prices.order_by("date_from")

    dish_items = DishProductItem.objects.filter(product=product).select_related("dish")
    preparation_items = PreparationItem.objects.filter(product=product).select_related("preparation")

    affected_dishes = []

    for item in dish_items:
        affected_dishes.append({
            "type": "Блюдо напрямую",
            "name": item.dish.name,
            "url": f"/dishes/{item.dish.id}/",
            "quantity": item.gross,
            "cost": item.calculate_cost(),
            "dish_cost": item.dish.calculate_cost(),
            "foodcost": item.dish.foodcost(),
        })

    for prep_item in preparation_items:
        preparation = prep_item.preparation

        for dish_prep_item in DishPreparationItem.objects.filter(preparation=preparation).select_related("dish"):
            affected_dishes.append({
                "type": f"Через заготовку: {preparation.name}",
                "name": dish_prep_item.dish.name,
                "url": f"/dishes/{dish_prep_item.dish.id}/",
                "quantity": dish_prep_item.gross,
                "cost": dish_prep_item.calculate_cost(),
                "dish_cost": dish_prep_item.dish.calculate_cost(),
                "foodcost": dish_prep_item.dish.foodcost(),
            })

    return render(request, "foodcost/product_detail.html", {
        "product": product,
        "prices": prices,
        "affected_dishes": affected_dishes,
        "preparation_items": preparation_items,
        "dish_items": dish_items,
    })


def preparation_list(request):
    preparations = Preparation.objects.all()
    return render(request, "foodcost/preparation_list.html", {"preparations": preparations})


def preparation_detail(request, pk):
    preparation = get_object_or_404(Preparation, pk=pk)
    products = Product.objects.all()

    if request.method == "POST":
        action = request.POST.get("action")

        if action == "save":
            preparation.name = request.POST.get("name")
            preparation.final_weight = request.POST.get("final_weight")
            preparation.cooking_minutes = request.POST.get("cooking_minutes") or 0
            preparation.save()

        if action == "add_item":
            PreparationItem.objects.create(
                preparation=preparation,
                product_id=request.POST.get("product_id"),
                gross=request.POST.get("gross"),
                net=request.POST.get("net") or request.POST.get("gross"),
            )

        if action == "update_item":
            item = get_object_or_404(PreparationItem, id=request.POST.get("item_id"))
            item.product_id = request.POST.get("product_id")
            item.gross = request.POST.get("gross") or 0
            item.net = request.POST.get("net") or item.gross
            item.save()

        if action == "delete_item":
            item = get_object_or_404(PreparationItem, id=request.POST.get("item_id"))
            item.delete()

        return redirect(f"/preparations/{preparation.id}/")

    return render(request, "foodcost/preparation_detail.html", {
        "preparation": preparation,
        "products": products,
    })


def employee_list(request):
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create":
            name = request.POST.get("name")
            monthly_salary = request.POST.get("monthly_salary")
            monthly_hours = request.POST.get("monthly_hours")

            if name and monthly_salary and monthly_hours:
                Employee.objects.create(
                    name=name,
                    monthly_salary=monthly_salary,
                    monthly_hours=monthly_hours,
                )

        if action == "update":
            employee = get_object_or_404(Employee, id=request.POST.get("employee_id"))
            employee.name = request.POST.get("name")
            employee.monthly_salary = request.POST.get("monthly_salary") or 0
            employee.monthly_hours = request.POST.get("monthly_hours") or 0
            employee.save()

        if action == "delete":
            employee = get_object_or_404(Employee, id=request.POST.get("employee_id"))
            employee.delete()

        return redirect("/employees/")

    employees = Employee.objects.all()
    return render(request, "foodcost/employee_list.html", {"employees": employees})


def packaging_list(request):
    if request.method == "POST":
        action = request.POST.get("action")

        if action == "create":
            Packaging.objects.create(
                name=request.POST.get("name"),
                cost=request.POST.get("cost"),
            )

        if action == "update":
            packaging = get_object_or_404(Packaging, id=request.POST.get("packaging_id"))
            packaging.name = request.POST.get("name")
            packaging.cost = request.POST.get("cost")
            packaging.save()

        return redirect("/packaging/")

    packagings = Packaging.objects.all()
    return render(request, "foodcost/packaging_list.html", {"packagings": packagings})


def utilities_list(request):
    if request.method == "POST":
        MonthlyUtilityExpense.objects.create(
            month=request.POST.get("month"),
            water=request.POST.get("water") or 0,
            electricity=request.POST.get("electricity") or 0,
            rent=request.POST.get("rent") or 0,
            working_hours=request.POST.get("working_hours") or 1,
        )
        return redirect("/utilities/")

    utilities = MonthlyUtilityExpense.objects.all().order_by("-month")
    return render(request, "foodcost/utilities_list.html", {"utilities": utilities})