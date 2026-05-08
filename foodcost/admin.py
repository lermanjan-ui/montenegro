from django.contrib import admin
from django.contrib.auth.models import User
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import (
    Country,
    Product,
    ProductPrice,
    Preparation,
    PreparationItem,
    DishCategory,
    Dish,
    DishProductItem,
    DishPreparationItem,
    Employee,
    Packaging,
    MonthlyUtilityExpense,
    DishPackagingItem,
    DishLaborItem,
    DishAdditionalExpense,
    UserProfile,
)


# =========================
# USER PROFILE INLINE
# =========================

class UserProfileInline(admin.StackedInline):
    model = UserProfile
    can_delete = False
    verbose_name_plural = "Права доступа"


class UserAdmin(BaseUserAdmin):
    inlines = (UserProfileInline,)


# Перерегистрируем User
admin.site.unregister(User)
admin.site.register(User, UserAdmin)


# =========================
# COUNTRY
# =========================

@admin.register(Country)
class CountryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


# =========================
# PRODUCT
# =========================

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "country", "unit")
    list_filter = ("country", "unit")
    search_fields = ("name",)


@admin.register(ProductPrice)
class ProductPriceAdmin(admin.ModelAdmin):
    list_display = ("product", "price", "date_from")
    list_filter = ("product__country", "date_from")
    search_fields = ("product__name",)


# =========================
# PREPARATION
# =========================

@admin.register(Preparation)
class PreparationAdmin(admin.ModelAdmin):
    list_display = ("name", "country", "final_weight", "cost", "cost_per_kg")
    list_filter = ("country",)
    search_fields = ("name",)

    def cost(self, obj):
        return round(obj.calculate_cost(), 2)

    def cost_per_kg(self, obj):
        if obj.final_weight == 0:
            return 0
        return round(obj.calculate_cost() / obj.final_weight, 2)


@admin.register(PreparationItem)
class PreparationItemAdmin(admin.ModelAdmin):
    list_display = ("preparation", "product", "gross", "net")
    list_filter = ("preparation__country",)


# =========================
# DISH
# =========================

@admin.register(DishCategory)
class DishCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "country")
    list_filter = ("country",)
    search_fields = ("name",)

@admin.register(Dish)
class DishAdmin(admin.ModelAdmin):
    list_display = ("name", "country", "final_weight", "selling_price", "cost", "foodcost", "margin")
    list_filter = ("country",)
    search_fields = ("name",)

    def cost(self, obj):
        return round(obj.calculate_cost(), 2)

    def foodcost(self, obj):
        return round(obj.foodcost(), 2)

    def margin(self, obj):
        return round(obj.margin(), 2)


@admin.register(DishProductItem)
class DishProductItemAdmin(admin.ModelAdmin):
    list_display = ("dish", "product", "gross", "net")
    list_filter = ("dish__country",)


@admin.register(DishPreparationItem)
class DishPreparationItemAdmin(admin.ModelAdmin):
    list_display = ("dish", "preparation", "gross", "net")
    list_filter = ("dish__country",)


# =========================
# EMPLOYEE
# =========================

@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("name", "country", "monthly_salary", "monthly_hours", "hourly_rate")
    list_filter = ("country",)
    search_fields = ("name",)


# =========================
# PACKAGING
# =========================

@admin.register(Packaging)
class PackagingAdmin(admin.ModelAdmin):
    list_display = ("name", "country", "cost")
    list_filter = ("country",)
    search_fields = ("name",)


# =========================
# UTILITIES
# =========================

@admin.register(MonthlyUtilityExpense)
class MonthlyUtilityExpenseAdmin(admin.ModelAdmin):
    list_display = ("country", "month", "water", "electricity", "rent", "total")
    list_filter = ("country", "month")


# =========================
# EXTRA
# =========================

@admin.register(DishPackagingItem)
class DishPackagingItemAdmin(admin.ModelAdmin):
    list_display = ("dish", "packaging", "quantity", "calculate_cost")
    list_filter = ("dish__country",)


@admin.register(DishLaborItem)
class DishLaborItemAdmin(admin.ModelAdmin):
    list_display = ("dish", "employee", "minutes", "calculate_cost")
    list_filter = ("dish__country",)


@admin.register(DishAdditionalExpense)
class DishAdditionalExpenseAdmin(admin.ModelAdmin):
    list_display = ("dish", "comment", "cost")
    list_filter = ("dish__country",)