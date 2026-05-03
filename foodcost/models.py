from django.db import models


# 🌍 СТРАНА
class Country(models.Model):
    name = models.CharField(max_length=100)
    slug = models.SlugField(unique=True)

    def __str__(self):
        return self.name


# 🥦 ПРОДУКТ
class Product(models.Model):
    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="products",
        null=True,
        blank=True,
    )

    name = models.CharField(max_length=255)
    unit = models.CharField(max_length=20)

    def __str__(self):
        return self.name

    def get_price(self):
        return self.prices.order_by("-date_from").first()


class ProductPrice(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="prices")
    price = models.DecimalField(max_digits=10, decimal_places=2)
    date_from = models.DateField()

    def __str__(self):
        return f"{self.product.name} — {self.price}"


# 🧪 ЗАГОТОВКА
class Preparation(models.Model):
    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="preparations",
        null=True,
        blank=True,
    )

    name = models.CharField(max_length=255)
    final_weight = models.DecimalField(max_digits=10, decimal_places=3)
    cooking_minutes = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    def __str__(self):
        return self.name

    def calculate_cost(self):
        return sum(item.calculate_cost() for item in self.items.all())

    def cost_per_kg(self):
        if self.final_weight == 0:
            return 0
        return self.calculate_cost() / self.final_weight


class PreparationItem(models.Model):
    preparation = models.ForeignKey(Preparation, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    gross = models.DecimalField(max_digits=10, decimal_places=3)
    net = models.DecimalField(max_digits=10, decimal_places=3)

    def calculate_cost(self):
        price = self.product.get_price()
        if not price:
            return 0
        return self.net * price.price


# 🍽 БЛЮДО
class Dish(models.Model):
    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="dishes",
        null=True,
        blank=True,
    )

    name = models.CharField(max_length=255)
    final_weight = models.DecimalField(max_digits=10, decimal_places=3)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)
    cooking_minutes = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    # старая техкарта (оставляем)
    tech_card = models.TextField("Техкарта приготовления", blank=True, default="")

    def __str__(self):
        return self.name

    def ingredient_cost(self):
        return sum(item.calculate_cost() for item in self.product_items.all()) + \
               sum(item.calculate_cost() for item in self.preparation_items.all())

    def packaging_cost(self):
        return sum(item.calculate_cost() for item in self.packaging_items.all())

    def labor_cost(self):
        return sum(item.calculate_cost() for item in self.labor_items.all())

    def utilities_cost(self):
        utilities = MonthlyUtilityExpense.objects.filter(country=self.country).order_by("-month").first()
        if not utilities:
            return 0
        return utilities.cost_per_minute() * self.total_cooking_minutes()

    def additional_expenses_cost(self):
        return sum(item.cost for item in self.additional_expenses.all())

    def total_cooking_minutes(self):
        return self.cooking_minutes + sum(p.preparation.cooking_minutes for p in self.preparation_items.all())

    def calculate_cost(self):
        return self.ingredient_cost() + self.packaging_cost() + self.labor_cost() + self.utilities_cost() + self.additional_expenses_cost()

    def foodcost(self):
        if self.selling_price == 0:
            return 0
        return (self.calculate_cost() / self.selling_price) * 100

    def margin(self):
        return self.selling_price - self.calculate_cost()


# 🔥 НОВАЯ МОДЕЛЬ — ШАГИ ТЕХКАРТЫ
class DishTechStep(models.Model):
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="steps")
    step_number = models.PositiveIntegerField()
    description = models.TextField()

    def __str__(self):
        return f"{self.dish.name} — шаг {self.step_number}"

    class Meta:
        ordering = ["step_number"]


class DishProductItem(models.Model):
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="product_items")
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    gross = models.DecimalField(max_digits=10, decimal_places=3)
    net = models.DecimalField(max_digits=10, decimal_places=3)

    def calculate_cost(self):
        price = self.product.get_price()
        if not price:
            return 0
        return self.net * price.price


class DishPreparationItem(models.Model):
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="preparation_items")
    preparation = models.ForeignKey(Preparation, on_delete=models.CASCADE)
    gross = models.DecimalField(max_digits=10, decimal_places=3)
    net = models.DecimalField(max_digits=10, decimal_places=3)

    def calculate_cost(self):
        return self.net * self.preparation.cost_per_kg()


# 👨‍🍳 СОТРУДНИК
class Employee(models.Model):
    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="employees",
        null=True,
        blank=True,
    )

    name = models.CharField(max_length=255)
    monthly_salary = models.DecimalField(max_digits=10, decimal_places=2)
    monthly_hours = models.DecimalField(max_digits=8, decimal_places=2)

    def __str__(self):
        return self.name

    def hourly_rate(self):
        if self.monthly_hours == 0:
            return 0
        return self.monthly_salary / self.monthly_hours

    def minute_rate(self):
        return self.hourly_rate() / 60


class DishLaborItem(models.Model):
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="labor_items")
    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    minutes = models.DecimalField(max_digits=8, decimal_places=2)

    def calculate_cost(self):
        return self.minutes * self.employee.minute_rate()


# 📦 УПАКОВКА
class Packaging(models.Model):
    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="packagings",
        null=True,
        blank=True,
    )

    name = models.CharField(max_length=255)
    cost = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return self.name


class DishPackagingItem(models.Model):
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="packaging_items")
    packaging = models.ForeignKey(Packaging, on_delete=models.CASCADE)
    quantity = models.DecimalField(max_digits=10, decimal_places=3)

    def calculate_cost(self):
        return self.quantity * self.packaging.cost


# 💸 ДОП РАСХОДЫ
class DishAdditionalExpense(models.Model):
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="additional_expenses")
    comment = models.CharField(max_length=255)
    cost = models.DecimalField(max_digits=10, decimal_places=2)


# 💡 КОММУНАЛКА
class MonthlyUtilityExpense(models.Model):
    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="utilities",
        null=True,
        blank=True,
    )

    month = models.DateField()
    water = models.DecimalField(max_digits=10, decimal_places=2)
    electricity = models.DecimalField(max_digits=10, decimal_places=2)
    rent = models.DecimalField(max_digits=10, decimal_places=2)
    working_hours = models.DecimalField(max_digits=8, decimal_places=2)

    def total(self):
        return self.water + self.electricity + self.rent

    def cost_per_minute(self):
        if self.working_hours == 0:
            return 0
        return self.total() / (self.working_hours * 60)