from django.contrib import admin
from .models import *


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "unit")


@admin.register(ProductPrice)
class ProductPriceAdmin(admin.ModelAdmin):
    list_display = ("product", "price", "date_from")


@admin.register(Preparation)
class PreparationAdmin(admin.ModelAdmin):
    list_display = ("name", "final_weight", "cost", "cost_per_kg")

    def cost(self, obj):
        return round(obj.calculate_cost(), 2)

def cost_per_kg(self, obj):
    if obj.final_weight == 0:
        return 0
    return round(obj.calculate_cost() / obj.final_weight, 2)


@admin.register(PreparationItem)
class PreparationItemAdmin(admin.ModelAdmin):
    list_display = ("preparation", "product", "gross", "net")


@admin.register(Dish)
class DishAdmin(admin.ModelAdmin):
    list_display = ("name", "final_weight", "selling_price", "cost", "cost_per_kg", "foodcost", "margin")

    def cost(self, obj):
        return round(obj.calculate_cost(), 2)

    def cost_per_kg(self, obj):
        return round(obj.cost_per_kg(), 2)

    def foodcost(self, obj):
        return round(obj.foodcost(), 2)

    def margin(self, obj):
        return round(obj.margin(), 2)


@admin.register(DishProductItem)
class DishProductItemAdmin(admin.ModelAdmin):
    list_display = ("dish", "product", "gross", "net")


@admin.register(DishPreparationItem)
class DishPreparationItemAdmin(admin.ModelAdmin):
    list_display = ("dish", "preparation", "gross", "net")