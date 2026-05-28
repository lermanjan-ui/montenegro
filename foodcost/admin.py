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
    Customer,
    CustomerAddress,
    OrderSource,
    DeliveryProvider,
    PromoCode,
    Order,
    OrderItem,
    PaymentMethod,
    FinancialExpense,
    EmployeeShift,
    EmployeePenaltyType,
    EmployeePenalty,
    EmployeePayment,
    # 🌐 Public website models (Part 1)
    DishAvailability,
    AddonGroup,
    AddonItem,
    DishAddonGroup,
    CategoryAddonGroup,
    DeliveryZone,
    CustomerFavorite,
    # 🌐 Website content models (Part 4)
    DishGalleryImage,
    DishAddon,
    # 🏠 Homepage CMS (Part 11)
    HomepageBanner,
    HomepageProductBlock,
    HomepageProductBlockItem,
    # 🏠 Homepage compact upsell (Part 1 — separate from frequently-bought)
    HomepageCompactUpsellBlock,
    HomepageCompactUpsellItem,
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

@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):

    list_display = (
        "name",
        "country",
        "is_cash",
        "is_active",
    )

    list_filter = (
        "country",
        "is_cash",
        "is_active",
    )

    search_fields = (
        "name",
    )    
    
admin.site.register(Customer)
admin.site.register(CustomerAddress)
admin.site.register(OrderSource)
admin.site.register(DeliveryProvider)
admin.site.register(PromoCode)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    """
    Custom admin so courier-facing fields (Part 9) are visible at a glance.
    Kept compact — full editing still happens in the dedicated ERP order
    detail page.
    """
    list_display = (
        "id",
        "public_order_number",
        "customer_name",
        "customer_phone",
        "fulfillment_method",
        "leave_at_door",
        "status",
        "total_amount",
        "created_at",
    )
    list_filter = (
        "country",
        "status",
        "fulfillment_method",
        "payment_status",
        "leave_at_door",
    )
    search_fields = (
        "id",
        "public_order_number",
        "customer_name",
        "customer_phone",
        "delivery_address",
        "delivery_landmark",
        "courier_comment",
    )
    readonly_fields = ("created_at", "updated_at")
    fieldsets = (
        ("Базовое", {
            "fields": (
                "country", "location", "customer", "source",
                "public_order_number", "status",
                "fulfillment_method", "payment_method", "payment_status",
                "promo_code",
            ),
        }),
        ("Клиент", {
            "fields": (
                "customer_name", "customer_phone",
                "customer_comment",
            ),
        }),
        ("Доставка / адрес", {
            "fields": (
                "delivery_address", "address_comment",
                "delivery_apartment", "delivery_entrance",
                "delivery_floor", "delivery_intercom",
                "delivery_latitude", "delivery_longitude",
            ),
        }),
        ("Курьер", {
            "fields": (
                "delivery_landmark",
                "courier_comment",
                "leave_at_door",
            ),
            "description": (
                "Ориентир и комментарий передаются курьеру. "
                "«Оставить у двери» — пометка для бесконтактной доставки."
            ),
        }),
        ("Суммы", {
            "fields": (
                "subtotal_amount", "discount_amount",
                "delivery_amount", "customer_delivery_amount",
                "free_customer_delivery",
                "commission_amount", "net_revenue",
                "total_amount",
            ),
        }),
        ("Кассир / служебное", {
            "fields": (
                "cashier_comment",
                "is_cancelled", "cancel_reason",
                "order_date", "created_at", "updated_at",
                "created_by",
            ),
            "classes": ("collapse",),
        }),
    )


admin.site.register(OrderItem)
admin.site.register(FinancialExpense)
admin.site.register(EmployeeShift)
admin.site.register(EmployeePenaltyType)
admin.site.register(EmployeePenalty)
admin.site.register(EmployeePayment)


# =========================
# 🌐 PUBLIC WEBSITE MODELS (Part 1)
# =========================

@admin.register(DishAvailability)
class DishAvailabilityAdmin(admin.ModelAdmin):
    list_display = (
        "dish",
        "location",
        "is_available",
        "is_stop_list",
        "updated_at",
    )
    list_filter = (
        "country",
        "location",
        "is_available",
        "is_stop_list",
    )
    search_fields = ("dish__name", "location__name")
    autocomplete_fields = ("dish",)


@admin.register(AddonGroup)
class AddonGroupAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "country",
        "is_active",
        "sort_order",
    )
    list_filter = ("country", "is_active")
    search_fields = ("name", "code")


@admin.register(AddonItem)
class AddonItemAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "group",
        "price",
        "is_available",
        "sort_order",
    )
    list_filter = ("country", "group", "is_available")
    search_fields = ("name",)


@admin.register(DishAddonGroup)
class DishAddonGroupAdmin(admin.ModelAdmin):
    list_display = ("dish", "group")
    list_filter = ("group",)
    search_fields = ("dish__name", "group__name")
    autocomplete_fields = ("dish",)


@admin.register(CategoryAddonGroup)
class CategoryAddonGroupAdmin(admin.ModelAdmin):
    list_display = ("category", "group")
    list_filter = ("group",)
    search_fields = ("category__name", "group__name")


@admin.register(DeliveryZone)
class DeliveryZoneAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "location",
        "center_latitude",
        "center_longitude",
        "radius_km",
        "delivery_price",
        "free_delivery_threshold",
        "site_sort_order",
        "is_active",
    )
    list_filter = ("country", "location", "is_active")
    search_fields = ("name", "location__name")
    list_editable = ("is_active", "site_sort_order")


@admin.register(CustomerFavorite)
class CustomerFavoriteAdmin(admin.ModelAdmin):
    list_display = ("customer", "dish", "created_at")
    list_filter = ("created_at",)
    search_fields = ("customer__name", "customer__phone", "dish__name")
    autocomplete_fields = ("dish",)


# =========================
# 🌐 WEBSITE CONTENT MODELS (Part 4)
# =========================

@admin.register(DishGalleryImage)
class DishGalleryImageAdmin(admin.ModelAdmin):
    list_display = ("dish", "sort_order", "is_active", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("dish__name", "alt_text")
    autocomplete_fields = ("dish",)
    list_editable = ("sort_order", "is_active")


@admin.register(DishAddon)
class DishAddonAdmin(admin.ModelAdmin):
    list_display = (
        "dish",
        "addon_dish",
        "group_name",
        "is_active",
        "sort_order",
    )
    list_filter = ("is_active", "group_name")
    search_fields = ("dish__name", "addon_dish__name", "group_name")
    autocomplete_fields = ("dish", "addon_dish")
    list_editable = ("group_name", "is_active", "sort_order")


# =========================
# 🏠 HOMEPAGE CMS (Part 11)
# =========================

@admin.register(HomepageBanner)
class HomepageBannerAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "country",
        "action_type",
        "sort_order",
        "is_active",
        "start_at",
        "end_at",
    )
    list_filter = ("country", "is_active", "action_type")
    search_fields = ("title", "subtitle", "action_value")
    list_editable = ("sort_order", "is_active")
    fieldsets = (
        ("Содержимое", {
            "fields": ("country", "title", "subtitle"),
        }),
        ("Изображения", {
            "fields": ("desktop_image", "mobile_image"),
            "description": (
                "URL-адреса картинок. Загрузка файла на сервер не "
                "используется — сюда вставляется готовая ссылка."
            ),
        }),
        ("Действие при клике", {
            "fields": ("action_type", "action_value"),
            "description": (
                "Для «Категория» / «Товар» в action_value — slug. "
                "Для «Внешняя ссылка» — полный URL. "
                "Для «Без действия» — оставьте пустым."
            ),
        }),
        ("Показ и расписание", {
            "fields": (
                "sort_order", "is_active",
                "start_at", "end_at",
            ),
            "description": (
                "Если start_at / end_at не заполнены — баннер "
                "показывается всегда (пока is_active=True)."
            ),
        }),
    )


class HomepageProductBlockItemInline(admin.TabularInline):
    model = HomepageProductBlockItem
    extra = 1
    fields = ("dish", "sort_order", "is_active")
    autocomplete_fields = ("dish",)
    ordering = ("sort_order", "id")


@admin.register(HomepageProductBlock)
class HomepageProductBlockAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "country",
        "sort_order",
        "is_active",
    )
    list_filter = ("country", "is_active")
    search_fields = ("title",)
    list_editable = ("sort_order", "is_active")
    inlines = [HomepageProductBlockItemInline]


@admin.register(HomepageProductBlockItem)
class HomepageProductBlockItemAdmin(admin.ModelAdmin):
    """
    Standalone admin in addition to the inline — useful when you want to
    move an item between blocks or filter all dishes that appear in any
    homepage block.
    """
    list_display = ("block", "dish", "sort_order", "is_active")
    list_filter = ("block__country", "is_active", "block")
    search_fields = ("block__title", "dish__name")
    autocomplete_fields = ("dish",)
    list_editable = ("sort_order", "is_active")


# =========================
# 🏠 HOMEPAGE COMPACT UPSELL (Part 1)
# =========================
# Same admin pattern as HomepageProductBlock above: an inline for items plus
# a standalone item admin for cross-block moves and filtering.

class HomepageCompactUpsellItemInline(admin.TabularInline):
    model = HomepageCompactUpsellItem
    extra = 1
    fields = ("dish", "sort_order", "is_active")
    autocomplete_fields = ("dish",)
    ordering = ("sort_order", "id")


@admin.register(HomepageCompactUpsellBlock)
class HomepageCompactUpsellBlockAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "country",
        "sort_order",
        "is_active",
    )
    list_filter = ("country", "is_active")
    search_fields = ("title",)
    list_editable = ("sort_order", "is_active")
    inlines = [HomepageCompactUpsellItemInline]


@admin.register(HomepageCompactUpsellItem)
class HomepageCompactUpsellItemAdmin(admin.ModelAdmin):
    """
    Standalone admin in addition to the inline — useful to move an item
    between compact blocks or to filter all dishes used in any compact block.
    """
    list_display = ("block", "dish", "sort_order", "is_active")
    list_filter = ("block__country", "is_active", "block")
    search_fields = ("block__title", "dish__name")
    autocomplete_fields = ("dish",)
    list_editable = ("sort_order", "is_active")
