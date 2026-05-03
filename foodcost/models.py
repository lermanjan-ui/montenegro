from decimal import Decimal
from django.db import models


class Product(models.Model):
    UNIT_CHOICES = [
        ("kg", "кг"),
        ("g", "г"),
        ("l", "л"),
        ("ml", "мл"),
        ("pcs", "шт"),
    ]

    name = models.CharField(max_length=255)
    unit = models.CharField(max_length=10, choices=UNIT_CHOICES)

    def __str__(self):
        return self.name

    def get_price(self):
        return self.prices.order_by("-date_from", "-id").first()


class ProductPrice(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="prices")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    date_from = models.DateField()

    def __str__(self):
        return f"{self.product.name} - {self.price}"


class Preparation(models.Model):
    name = models.CharField(max_length=255)
    final_weight = models.DecimalField(max_digits=10, decimal_places=3)
    cooking_minutes = models.DecimalField("Время приготовления, мин", max_digits=8, decimal_places=2, default=0)

    def __str__(self):
        return self.name

    def calculate_cost(self):
        total = Decimal("0")
        for item in self.items.all():
            total += item.calculate_cost()
        return total

    def cost_per_kg(self):
        if self.final_weight == 0:
            return Decimal("0")
        return self.calculate_cost() / self.final_weight

    def minutes_per_kg(self):
        if self.final_weight == 0:
            return Decimal("0")
        return self.cooking_minutes / self.final_weight


class PreparationItem(models.Model):
    preparation = models.ForeignKey(Preparation, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    gross = models.DecimalField(max_digits=10, decimal_places=3)
    net = models.DecimalField(max_digits=10, decimal_places=3)

    def calculate_cost(self):
        price = self.product.get_price()
        if not price:
            return Decimal("0")
        return self.gross * price.price


class Employee(models.Model):
    name = models.CharField("Имя сотрудника", max_length=255)
    monthly_salary = models.DecimalField("Зарплата в месяц", max_digits=12, decimal_places=2)
    monthly_hours = models.DecimalField("Часов в месяц", max_digits=8, decimal_places=2)

    def __str__(self):
        return self.name

    def hourly_rate(self):
        if self.monthly_hours == 0:
            return Decimal("0")
        return self.monthly_salary / self.monthly_hours

    def minute_rate(self):
        return self.hourly_rate() / Decimal("60")


class Packaging(models.Model):
    name = models.CharField("Название упаковки", max_length=255)
    cost = models.DecimalField("Стоимость упаковки", max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.name} — {self.cost}"


class MonthlyUtilityExpense(models.Model):
    month = models.DateField("Месяц")
    water = models.DecimalField("Вода", max_digits=12, decimal_places=2, default=0)
    electricity = models.DecimalField("Электричество", max_digits=12, decimal_places=2, default=0)
    rent = models.DecimalField("Аренда", max_digits=12, decimal_places=2, default=0)
    working_hours = models.DecimalField("Рабочих часов кухни в месяц", max_digits=10, decimal_places=2, default=1)

    def __str__(self):
        return f"Коммуналка за {self.month}"

    def total(self):
        return self.water + self.electricity + self.rent

    def minute_rate(self):
        if self.working_hours == 0:
            return Decimal("0")
        return self.total() / (self.working_hours * Decimal("60"))


class Dish(models.Model):
    name = models.CharField(max_length=255)
    final_weight = models.DecimalField(max_digits=10, decimal_places=3)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)
    cooking_minutes = models.DecimalField("Время приготовления блюда, мин", max_digits=8, decimal_places=2, default=0)

    def __str__(self):
        return self.name

    def product_cost(self):
        total = Decimal("0")
        for item in self.product_items.all():
            total += item.calculate_cost()
        return total

    def preparation_cost(self):
        total = Decimal("0")
        for item in self.preparation_items.all():
            total += item.calculate_cost()
        return total

    def ingredient_cost(self):
        return self.product_cost() + self.preparation_cost()

    def packaging_cost(self):
        total = Decimal("0")
        for item in self.packaging_items.all():
            total += item.calculate_cost()
        return total

    def labor_cost(self):
        total = Decimal("0")
        for item in self.labor_items.all():
            total += item.calculate_cost()
        return total

    def additional_expenses_cost(self):
        total = Decimal("0")
        for item in self.additional_expenses.all():
            total += item.cost
        return total

    def preparations_cooking_minutes(self):
        total = Decimal("0")
        for item in self.preparation_items.all():
            total += item.gross * item.preparation.minutes_per_kg()
        return total

    def direct_labor_minutes(self):
        total = Decimal("0")
        for item in self.labor_items.all():
            total += item.minutes
        return total

    def total_cooking_minutes(self):
        return self.cooking_minutes + self.preparations_cooking_minutes() + self.direct_labor_minutes()

    def utilities_cost(self):
        utility = MonthlyUtilityExpense.objects.order_by("-month", "-id").first()
        if not utility:
            return Decimal("0")
        return self.total_cooking_minutes() * utility.minute_rate()

    def calculate_cost(self):
        return (
            self.ingredient_cost()
            + self.packaging_cost()
            + self.labor_cost()
            + self.utilities_cost()
            + self.additional_expenses_cost()
        )

    def foodcost(self):
        if self.selling_price == 0:
            return Decimal("0")
        return (self.calculate_cost() / self.selling_price) * Decimal("100")

    def margin(self):
        return self.selling_price - self.calculate_cost()


class DishProductItem(models.Model):
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="product_items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    gross = models.DecimalField(max_digits=10, decimal_places=3)
    net = models.DecimalField(max_digits=10, decimal_places=3)

    def calculate_cost(self):
        price = self.product.get_price()
        if not price:
            return Decimal("0")
        return self.gross * price.price


class DishPreparationItem(models.Model):
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="preparation_items")
    preparation = models.ForeignKey(Preparation, on_delete=models.CASCADE)
    gross = models.DecimalField(max_digits=10, decimal_places=3)
    net = models.DecimalField(max_digits=10, decimal_places=3)

    def calculate_cost(self):
        return self.gross * self.preparation.cost_per_kg()


class DishPackagingItem(models.Model):
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="packaging_items")
    packaging = models.ForeignKey(Packaging, on_delete=models.CASCADE)
    quantity = models.DecimalField("Количество", max_digits=10, decimal_places=3, default=1)

    def calculate_cost(self):
        return self.quantity * self.packaging.cost


class DishLaborItem(models.Model):
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="labor_items")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    minutes = models.DecimalField("Минуты", max_digits=8, decimal_places=2)

    def calculate_cost(self):
        return self.minutes * self.employee.minute_rate()


class DishAdditionalExpense(models.Model):
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="additional_expenses")
    comment = models.CharField("Комментарий", max_length=255)
    cost = models.DecimalField("Стоимость", max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.dish.name}: {self.comment} — {self.cost}"