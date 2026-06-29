"""
🍱📒 Журнал продаж обедов (LunchSale) — единая таблица: и ручные записи, и
автозаезд с сайта. Считает выручку / себестоимость / маржу / фудкост.

Сет собирается из блюд (LunchSaleItem → Dish) — себестоимость берётся из
dish.cached_total_cost; можно переопределить вручную (cost_override).
Цена продажи сета (sale_price) задаётся на КАЖДУЮ запись (один и тот же сет
может стоить по-разному у разных клиентов).

Клиент — свободный текст (имя + телефон), необязателен. Источник: вручную /
с сайта. Сайтовые записи привязаны к заказу (order) — для дедупликации.

Модели в отдельном модуле, подключаются строкой импорта в конце models.py.
"""

from decimal import Decimal

from django.db import models

from .models import Country, Location, Dish, Order


__all__ = ["LunchSale", "LunchSaleItem"]


def _D(value):
    try:
        return Decimal(str(value if value is not None else 0))
    except Exception:  # noqa: BLE001
        return Decimal(0)


class LunchSale(models.Model):
    """Одна продажа обеда (ручная или с сайта)."""

    SOURCE_MANUAL = "manual"
    SOURCE_SITE = "site"
    SOURCE_CHOICES = [
        (SOURCE_MANUAL, "Вручную"),
        (SOURCE_SITE, "С сайта"),
    ]

    country = models.ForeignKey(
        Country, on_delete=models.CASCADE, related_name="lunch_sales"
    )
    location = models.ForeignKey(
        Location, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="lunch_sales",
    )
    date = models.DateField()
    customer_name = models.CharField(max_length=255, blank=True, default="")
    customer_phone = models.CharField(max_length=40, blank=True, default="")
    title = models.CharField(max_length=255, blank=True, default="")
    quantity = models.PositiveIntegerField(default=1)
    # Цена продажи ОДНОГО сета (выручка = sale_price × quantity).
    sale_price = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    # Ручная себестоимость одного сета; если задана — игнорирует расчёт по блюдам.
    cost_override = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    source = models.CharField(
        max_length=10, choices=SOURCE_CHOICES, default=SOURCE_MANUAL
    )
    # Привязка к заказу для автозаписей с сайта (дедуп по order).
    order = models.ForeignKey(
        Order, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="lunch_sales",
    )
    comment = models.TextField(blank=True, default="")
    created_by = models.ForeignKey(
        "auth.User", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_lunch_sales",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        who = self.customer_name or self.customer_phone or "—"
        return f"{self.title or 'Обед'} · {who} · {self.date}"

    # --- расчёты ---
    def per_set_cost(self):
        """Себестоимость одного сета: ручная или сумма блюд."""
        if self.cost_override is not None:
            return _D(self.cost_override)
        total = Decimal(0)
        for it in self.items.all():
            total += it.cost()
        return total

    def revenue(self):
        return _D(self.sale_price) * _D(self.quantity or 1)

    def cost_total(self):
        return self.per_set_cost() * _D(self.quantity or 1)

    def margin(self):
        return self.revenue() - self.cost_total()

    def foodcost_percent(self):
        rev = self.revenue()
        if rev <= 0:
            return Decimal(0)
        return (self.cost_total() / rev * Decimal(100)).quantize(Decimal("0.1"))

    def margin_percent(self):
        rev = self.revenue()
        if rev <= 0:
            return Decimal(0)
        return (self.margin() / rev * Decimal(100)).quantize(Decimal("0.1"))


class LunchSaleItem(models.Model):
    """Позиция сета: блюдо из системы (для себестоимости) или просто текст."""

    sale = models.ForeignKey(
        LunchSale, on_delete=models.CASCADE, related_name="items"
    )
    dish = models.ForeignKey(
        Dish, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    name = models.CharField(max_length=255, blank=True, default="")
    quantity = models.DecimalField(max_digits=10, decimal_places=3, default=1)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.display_name()

    def display_name(self):
        if self.name:
            return self.name
        if self.dish_id and self.dish:
            return self.dish.name
        return "—"

    def cost(self):
        if self.dish_id and self.dish:
            return _D(self.dish.cached_total_cost) * _D(self.quantity or 1)
        return Decimal(0)
