from django.contrib import admin

from .models import (
    KitchenScreen,
    KitchenTimeThreshold,
    ServiceTipSettings,
    LoyaltyProgram,
    LoyaltyTier,
    CustomerPoints,
    FloorZone,
    FloorTable,
    TableBooking,
    FiscalizationSettings,
    PaymentMethodLocationConfig,
    PosOrder,
    PosOrderItem,
    PosPayment,
    PosBill,
)


@admin.register(KitchenScreen)
class KitchenScreenAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "location", "is_connected", "is_active", "sort_order")
    list_filter = ("tenant", "location", "is_connected", "is_active")
    search_fields = ("name", "device_label")
    filter_horizontal = ("categories", "dishes")


@admin.register(KitchenTimeThreshold)
class KitchenTimeThresholdAdmin(admin.ModelAdmin):
    list_display = ("tenant", "screen", "category", "dish", "warn_minutes", "late_minutes")
    list_filter = ("tenant", "screen")


@admin.register(ServiceTipSettings)
class ServiceTipSettingsAdmin(admin.ModelAdmin):
    list_display = ("tenant", "location", "service_enabled", "service_percent", "tips_enabled")
    list_filter = ("tenant", "location", "service_enabled", "tips_enabled")


class LoyaltyTierInline(admin.TabularInline):
    model = LoyaltyTier
    extra = 0


@admin.register(LoyaltyProgram)
class LoyaltyProgramAdmin(admin.ModelAdmin):
    list_display = ("tenant", "enabled", "earn_percent", "redeem_max_percent", "tiers_enabled")
    list_filter = ("enabled", "tiers_enabled")
    inlines = [LoyaltyTierInline]


@admin.register(LoyaltyTier)
class LoyaltyTierAdmin(admin.ModelAdmin):
    list_display = ("name", "program", "min_turnover", "earn_percent", "sort_order")
    list_filter = ("program",)


@admin.register(CustomerPoints)
class CustomerPointsAdmin(admin.ModelAdmin):
    list_display = ("customer", "balance", "updated_at")
    search_fields = ("customer__name", "customer__phone")


class FloorTableInline(admin.TabularInline):
    model = FloorTable
    extra = 0


@admin.register(FloorZone)
class FloorZoneAdmin(admin.ModelAdmin):
    list_display = ("name", "tenant", "location", "sort_order")
    list_filter = ("tenant", "location")
    inlines = [FloorTableInline]


@admin.register(FloorTable)
class FloorTableAdmin(admin.ModelAdmin):
    list_display = ("number", "zone", "seats", "shape", "is_bookable", "is_active")
    list_filter = ("zone", "shape", "is_active")


@admin.register(TableBooking)
class TableBookingAdmin(admin.ModelAdmin):
    list_display = ("table", "guest_name", "phone", "booking_date", "booking_time", "guests", "status")
    list_filter = ("status", "booking_date")
    search_fields = ("guest_name", "phone")


@admin.register(FiscalizationSettings)
class FiscalizationSettingsAdmin(admin.ModelAdmin):
    list_display = ("tenant", "location", "mode", "vat_mode", "is_connected")
    list_filter = ("tenant", "location", "mode", "is_connected")


@admin.register(PaymentMethodLocationConfig)
class PaymentMethodLocationConfigAdmin(admin.ModelAdmin):
    list_display = ("payment_method", "location", "is_visible", "fiscalization", "sort_order")
    list_filter = ("location", "is_visible", "fiscalization")


class PosOrderItemInline(admin.TabularInline):
    model = PosOrderItem
    extra = 0


@admin.register(PosOrder)
class PosOrderAdmin(admin.ModelAdmin):
    list_display = ("order", "is_dine_in", "table", "guests", "waiter", "service_charge_amount", "tips_amount")
    list_filter = ("is_dine_in",)
    raw_id_fields = ("order", "table", "waiter")


@admin.register(PosOrderItem)
class PosOrderItemAdmin(admin.ModelAdmin):
    list_display = ("order_item", "kitchen_status", "screen", "course", "discount_percent")
    list_filter = ("kitchen_status", "screen")
    raw_id_fields = ("order_item",)


@admin.register(PosPayment)
class PosPaymentAdmin(admin.ModelAdmin):
    list_display = ("order", "bill", "method", "amount", "fiscal_status", "fiscal_receipt_id", "created_at")
    list_filter = ("fiscal_status", "method")
    raw_id_fields = ("order", "bill")


@admin.register(PosBill)
class PosBillAdmin(admin.ModelAdmin):
    list_display = ("pos_order", "label", "amount", "is_paid", "sort_order")
    list_filter = ("is_paid",)
    raw_id_fields = ("pos_order",)
