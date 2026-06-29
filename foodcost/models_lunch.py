"""
🍱 Обеды-комплексы (новый слой РЯДОМ с LunchMenu, старый не трогаем).

Несколько обедов на дату; у каждого обеда — размеры (Стандарт/Большой) со
СВОЕЙ ценой и граммовкой; у каждого размера — построчный состав. Строка
состава может быть:
  • привязана к блюду / заготовке / продукту  → себестоимость считается,
  • или просто текстом (название + граммовка)  → себестоимость 0 или вручную.

Себестоимость строки повторяет логику блюда (как в Dish*Item):
  блюдо      → dish.cached_total_cost × quantity
  заготовка  → net(кг) × preparation.cost_per_kg()
  продукт    → net(ед. продукта) × product.get_price().price
  иначе      → cost_override (или 0)

Маржа размера = price − Σ(себестоимость строк); фудкост% = cost/price.

Доп. поля (аддендум «выгода + доп. порции»):
  у строки  — separate_price (à la carte цена позиции в базовом наборе),
              extra_price (цена одной доп. порции; null = добавка запрещена),
              extra_weight (подпись), extra_max (лимит доп. порций);
  у размера — separate_price() = Σ separate_price строк, savings() = separate−price.
"""

from decimal import Decimal

from django.db import models

from .models import Country, Dish, Preparation, Product


__all__ = ["Lunch", "LunchSize", "LunchSizeItem"]


def _D(value):
    try:
        return Decimal(str(value if value is not None else 0))
    except Exception:  # noqa: BLE001
        return Decimal(0)


class Lunch(models.Model):
    """Обед-комплекс на конкретную дату (несколько обедов на день)."""

    BADGE_POPULAR = "popular"
    BADGE_BEST_VALUE = "best_value"
    BADGE_CHOICES = [
        (BADGE_POPULAR, "Популярный"),
        (BADGE_BEST_VALUE, "Выгодный"),
    ]

    country = models.ForeignKey(
        Country, on_delete=models.CASCADE, related_name="lunches"
    )
    date = models.DateField()
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True, default="")
    photo = models.ImageField(upload_to="lunches/", null=True, blank=True)
    photo_url = models.URLField(max_length=1000, blank=True, default="")
    badge = models.CharField(
        max_length=20, choices=BADGE_CHOICES, blank=True, default=""
    )
    available = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)
    # Дедлайн заказа на день строкой "11:00" (как delivery_from у LunchMenu).
    order_cutoff = models.CharField(max_length=20, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["date", "sort_order", "id"]

    def __str__(self):
        return f"{self.name} ({self.date})"

    def default_size(self):
        return (
            self.sizes.filter(is_default=True).order_by("sort_order", "id").first()
            or self.sizes.order_by("sort_order", "id").first()
        )


class LunchSize(models.Model):
    """Размер обеда: своя цена, граммовка и состав."""

    lunch = models.ForeignKey(
        Lunch, on_delete=models.CASCADE, related_name="sizes"
    )
    label = models.CharField(max_length=60, default="Стандарт")
    is_default = models.BooleanField(default=False)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    weight_total = models.PositiveIntegerField(default=0)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.lunch.name} — {self.label}"

    def total_cost(self):
        total = Decimal(0)
        for it in self.items.all():
            total += it.component_cost()
        return total

    def margin(self):
        return _D(self.price) - self.total_cost()

    def foodcost_percent(self):
        price = _D(self.price)
        if price <= 0:
            return Decimal(0)
        return (self.total_cost() / price * Decimal(100)).quantize(Decimal("0.1"))

    def margin_percent(self):
        price = _D(self.price)
        if price <= 0:
            return Decimal(0)
        return (self.margin() / price * Decimal(100)).quantize(Decimal("0.1"))

    # --- выгода комплекса (аддендум) ---
    def separate_price(self):
        """Сумма позиций базового состава по отдельной (à la carte) цене."""
        total = Decimal(0)
        for it in self.items.all():
            total += _D(it.separate_price)
        return total

    def savings(self):
        """Выгода базового набора = separate_price − price (может быть 0/отриц.)."""
        return self.separate_price() - _D(self.price)


class LunchSizeItem(models.Model):
    """Строка состава размера. Привязка к компоненту опциональна."""

    ROLE_SOUP = "soup"
    ROLE_MAIN = "main"
    ROLE_SIDE = "side"
    ROLE_SALAD = "salad"
    ROLE_DRINK = "drink"
    ROLE_BREAD = "bread"
    ROLE_SAUCE = "sauce"
    ROLE_DESSERT = "dessert"
    ROLE_OTHER = "other"
    ROLE_CHOICES = [
        (ROLE_SOUP, "Суп"),
        (ROLE_MAIN, "Горячее"),
        (ROLE_SIDE, "Гарнир"),
        (ROLE_SALAD, "Салат"),
        (ROLE_DRINK, "Напиток"),
        (ROLE_BREAD, "Хлеб"),
        (ROLE_SAUCE, "Соус"),
        (ROLE_DESSERT, "Десерт"),
        (ROLE_OTHER, "Другое"),
    ]

    size = models.ForeignKey(
        LunchSize, on_delete=models.CASCADE, related_name="items"
    )
    role = models.CharField(
        max_length=20, choices=ROLE_CHOICES, default=ROLE_OTHER
    )
    name = models.CharField(max_length=255, blank=True, default="")
    # Граммовка строкой для фронта ("120 г"); пусто, если неприменимо.
    weight = models.CharField(max_length=40, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)

    # Привязка для расчёта себестоимости (любая ОДНА или ни одной).
    dish = models.ForeignKey(
        Dish, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    preparation = models.ForeignKey(
        Preparation, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    product = models.ForeignKey(
        Product, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    # net — расход в единице компонента: заготовка — в КГ, продукт — в его
    # единице (kg/l/pcs...). Для блюда берётся quantity (число порций).
    net = models.DecimalField(max_digits=10, decimal_places=3, default=0)
    quantity = models.DecimalField(max_digits=10, decimal_places=3, default=1)
    # Ручная себестоимость для текстовой строки (без привязки к компоненту).
    cost_override = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    # --- выгода + доп. порции (аддендум) ---
    # Отдельная (à la carte) цена позиции в базовом наборе → в separate_price размера.
    separate_price = models.DecimalField(
        max_digits=12, decimal_places=2, default=0
    )
    # Цена ОДНОЙ доп. порции этого пункта; null → доп. порция запрещена.
    extra_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    # Граммовка одной доп. порции ("120 г") — подпись, опционально.
    extra_weight = models.CharField(max_length=40, blank=True, default="")
    # Максимум доп. порций на пункт; null → без жёсткого лимита.
    extra_max = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.display_name()

    def display_name(self):
        if self.name:
            return self.name
        if self.dish_id and self.dish:
            return self.dish.name
        if self.preparation_id and self.preparation:
            return self.preparation.name
        if self.product_id and self.product:
            return self.product.name
        return "—"

    def component_cost(self):
        """Себестоимость ОДНОЙ порции строки по привязке (или ручная/0)."""
        if self.dish_id and self.dish:
            return _D(self.dish.cached_total_cost) * _D(self.quantity or 1)
        if self.preparation_id and self.preparation:
            return _D(self.net) * _D(self.preparation.cost_per_kg())
        if self.product_id and self.product:
            price = self.product.get_price()
            if not price:
                return Decimal(0)
            return _D(self.net) * _D(price.price)
        if self.cost_override is not None:
            return _D(self.cost_override)
        return Decimal(0)

    def extra_allowed(self):
        """Разрешена ли доп. порция (extra_price задан)."""
        return self.extra_price is not None

    def extra_unit_cost(self):
        """Себестоимость одной доп. порции (= себестоимость порции компонента)."""
        return self.component_cost()
