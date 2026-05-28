from decimal import Decimal

from django.db import models
from django.contrib.auth.models import User
from django.utils.text import slugify


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

    def unit_label(self):
        labels = {
            "kg": "кг",
            "g": "г",
            "l": "л",
            "ml": "мл",
            "pcs": "шт",
        }
        return labels.get(self.unit, self.unit)


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
    
    cached_total_cost = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0
    )

    cached_cost_per_kg = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0
    )

    def __str__(self):
        return self.name

    def calculate_cost(self):
        return (
            sum(item.calculate_cost() for item in self.items.all())
            + sum(item.calculate_cost() for item in self.subitems.all())
        )

    def cost_per_kg(self):
        if self.final_weight == 0:
            return 0
        return self.calculate_cost() / self.final_weight
        
    def recalculate_cache(self):
        total_cost = self.calculate_cost()

        cost_per_kg = 0

        if self.final_weight:
            cost_per_kg = total_cost / self.final_weight

        self.cached_total_cost = total_cost
        self.cached_cost_per_kg = cost_per_kg

        self.save(
            update_fields=[
                "cached_total_cost",
                "cached_cost_per_kg",
            ]
        )

    def total_cooking_minutes(self):
        total = self.cooking_minutes

        for item in self.subitems.all():
            if item.sub_preparation.final_weight == 0:
                continue

            total += item.sub_preparation.total_cooking_minutes() * (
                item.net / item.sub_preparation.final_weight
            )

        return total


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

    def unit_label(self):
        return self.product.unit_label()


class PreparationSubItem(models.Model):
    preparation = models.ForeignKey(
        Preparation,
        on_delete=models.CASCADE,
        related_name="subitems"
    )

    sub_preparation = models.ForeignKey(
        Preparation,
        on_delete=models.CASCADE,
        related_name="used_in_preparations"
    )

    gross = models.DecimalField(max_digits=10, decimal_places=3)
    net = models.DecimalField(max_digits=10, decimal_places=3)

    def calculate_cost(self):
        return self.net * self.sub_preparation.cost_per_kg()

    def unit_label(self):
        return "кг"


# 🏷 КАТЕГОРИЯ БЛЮДА
class DishCategory(models.Model):
    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="dish_categories",
        null=True,
        blank=True,
    )

    name = models.CharField(max_length=255)

    # ===== Public website fields =====
    public_name = models.CharField(
        max_length=255,
        blank=True,
        default=""
    )

    slug = models.SlugField(
        max_length=255,
        blank=True,
        default="",
        allow_unicode=True
    )

    photo = models.ImageField(
        upload_to="category_photos/",
        null=True,
        blank=True
    )

    # 🔗 External photo URL (CDN / Telegram / any direct link).
    # Has PRIORITY over the uploaded `photo` field — see public_api.
    # Useful when we don't want to depend on local media storage.
    photo_url = models.URLField(
        blank=True,
        default=""
    )

    is_visible_on_site = models.BooleanField(
        default=False
    )

    site_sort_order = models.PositiveIntegerField(
        default=0
    )

    class Meta:
        verbose_name = "Категория блюда"
        verbose_name_plural = "Категории блюд"
        ordering = ["name"]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Safe slug auto-generation: only fill if blank, never overwrite.
        # allow_unicode=True keeps Cyrillic names readable in the URL
        # ("Пицца" -> "пицца") instead of being stripped to empty.
        if not self.slug:
            base = slugify(self.public_name or self.name or "", allow_unicode=True)
            if base:
                self.slug = base[:255]
        super().save(*args, **kwargs)


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

    category = models.ForeignKey(
        DishCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dishes"
    )

    # 🌐 Multiple public website categories (one dish can appear in many).
    # If empty, the public API falls back to the legacy `category` FK above.
    public_categories = models.ManyToManyField(
        DishCategory,
        blank=True,
        related_name="public_dishes"
    )

    final_weight = models.DecimalField(max_digits=10, decimal_places=3)
    selling_price = models.DecimalField(max_digits=10, decimal_places=2)
    cooking_minutes = models.DecimalField(max_digits=8, decimal_places=2, default=0)

    tech_card = models.TextField("Техкарта приготовления", blank=True, default="")
    
    cached_ingredient_cost = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0
    )

    cached_total_cost = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0
    )

    cached_foodcost = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0
    )

    cached_margin = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0
    )

    # ===== Public website fields =====
    public_name = models.CharField(
        max_length=255,
        blank=True,
        default=""
    )

    slug = models.SlugField(
        max_length=255,
        blank=True,
        default="",
        allow_unicode=True
    )

    public_description = models.TextField(
        blank=True,
        default=""
    )

    short_description = models.CharField(
        max_length=500,
        blank=True,
        default=""
    )

    composition = models.TextField(
        blank=True,
        default=""
    )

    photo = models.ImageField(
        upload_to="dishes/",
        null=True,
        blank=True
    )

    # 🔗 External photo URL (CDN / Telegram / any direct link).
    # Has PRIORITY over the uploaded `photo` field — see public_api.
    # Useful when we don't want to depend on local media storage.
    photo_url = models.URLField(
        blank=True,
        default=""
    )

    gallery = models.JSONField(
        default=list,
        blank=True
    )

    public_weight = models.CharField(
        max_length=100,
        blank=True,
        default=""
    )

    cooking_time = models.CharField(
        max_length=100,
        blank=True,
        default=""
    )

    badge = models.CharField(
        max_length=50,
        blank=True,
        default=""
    )

    spice_level = models.CharField(
        max_length=100,
        blank=True,
        default=""
    )

    is_visible_on_site = models.BooleanField(
        default=False
    )

    is_stop_list = models.BooleanField(
        default=False
    )

    is_featured = models.BooleanField(
        default=False
    )

    is_new = models.BooleanField(
        default=False
    )

    is_spicy = models.BooleanField(
        default=False
    )

    is_vegetarian = models.BooleanField(
        default=False
    )

    site_sort_order = models.PositiveIntegerField(
        default=0
    )

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        # Safe slug auto-generation: only fill if blank, never overwrite an
        # existing or manually-entered slug. Whitespace-only counts as blank.
        if self._slug_is_blank():
            self.slug = self._generate_unique_slug()
        super().save(*args, **kwargs)

    # 🔗 Public slug used by the website for product detail pages, banner
    # product action_value and homepage product cards. If it's missing the
    # frontend routing breaks, so save() guarantees a non-empty, per-country
    # unique slug whenever the field is left blank.
    SLUG_FALLBACK = "dish"

    def _slug_is_blank(self):
        """
        True when the slug must be (re)generated.

        Covers all three "empty" cases from the spec:
          - empty string ""
          - NULL / None
          - a string containing only whitespace ("   ")
        """
        if self.slug is None:
            return True
        return self.slug.strip() == ""

    def _generate_unique_slug(self):
        """
        Build a slug that is unique WITHIN THE SAME COUNTRY.

        Source priority:
          1. public_name
          2. name
          3. "dish" — ONLY when slugify() yields an empty string (e.g. a name
             made only of emoji or punctuation). Cyrillic and other Unicode
             names are preserved via allow_unicode=True, so "Пепперони"
             becomes "пепперони", NOT the "dish" fallback.

        Collisions get a numeric suffix: pepperoni, pepperoni-2, pepperoni-3…
        Uniqueness is scoped to self.country, never global, so the same slug
        can exist in different countries.
        """
        base = slugify(self.public_name or self.name or "", allow_unicode=True)
        if not base:
            base = self.SLUG_FALLBACK
        base = base[:255]

        candidate = base
        suffix = 2
        while (
            Dish.objects
            .filter(country=self.country, slug=candidate)
            .exclude(pk=self.pk)
            .exists()
        ):
            # Keep the whole thing within the 255-char SlugField limit even
            # after appending "-<n>".
            tail = f"-{suffix}"
            candidate = f"{base[:255 - len(tail)]}{tail}"
            suffix += 1

        return candidate

    def ingredient_cost(self):
        return (
            sum(item.calculate_cost() for item in self.product_items.all())
            + sum(item.calculate_cost() for item in self.preparation_items.all())
        )

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
        total = self.cooking_minutes

        for item in self.preparation_items.all():
            preparation = item.preparation

            if preparation.final_weight == 0:
                continue

            total += preparation.total_cooking_minutes() * (
                item.net / preparation.final_weight
            )

        return total

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
            return 0
        return (self.ingredient_cost() / self.selling_price) * 100

    def margin(self):
        return self.selling_price - self.calculate_cost()
        
    def recalculate_cache(self):
        ingredient_cost = Decimal(self.ingredient_cost())
        total_cost = Decimal(self.calculate_cost())

        selling_price = Decimal(self.selling_price or 0)

        foodcost = Decimal("0")

        if selling_price > 0:
            foodcost = (ingredient_cost / selling_price) * 100

        margin = selling_price - total_cost

        self.cached_ingredient_cost = ingredient_cost
        self.cached_total_cost = total_cost
        self.cached_foodcost = foodcost
        self.cached_margin = margin

        self.save(
            update_fields=[
                "cached_ingredient_cost",
                "cached_total_cost",
                "cached_foodcost",
                "cached_margin",
            ]
        )

# 🔥 ШАГИ ТЕХКАРТЫ
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

    def unit_label(self):
        return self.product.unit_label()


class DishPreparationItem(models.Model):
    dish = models.ForeignKey(Dish, on_delete=models.CASCADE, related_name="preparation_items")
    preparation = models.ForeignKey(Preparation, on_delete=models.CASCADE)
    gross = models.DecimalField(max_digits=10, decimal_places=3)
    net = models.DecimalField(max_digits=10, decimal_places=3)

    def calculate_cost(self):
        return self.net * self.preparation.cost_per_kg()

    def unit_label(self):
        return "кг"


# 👨‍🍳 СОТРУДНИК
class Employee(models.Model):
    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="employees",
        null=True,
        blank=True,
    )

    name = models.CharField(
        max_length=255,
        blank=True,
        default=""
    )
    user = models.OneToOneField(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_profile"
    )

    position = models.CharField(
        max_length=120,
        blank=True,
        default=""
    )

    location = models.ForeignKey(
        "Location",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employees"
    )

    hourly_rate_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    shift_fixed_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )
    
    shift_rate_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    shift_kpi_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    default_shift_hours = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=12
    )

    tax_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0
    )

    is_active = models.BooleanField(
        default=True
    )

    monthly_salary = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )
    monthly_hours = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0
    )

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

# 📍 ТОЧКА / ФИЛИАЛ
class Location(models.Model):
    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="locations"
    )

    name = models.CharField(max_length=255)

    is_active = models.BooleanField(default=True)
    telegram_thread_id = models.BigIntegerField(
        null=True,
        blank=True
    )

    # ===== Public website fields =====
    public_name = models.CharField(
        max_length=255,
        blank=True,
        default=""
    )

    address = models.CharField(
        max_length=500,
        blank=True,
        default=""
    )

    phone = models.CharField(
        max_length=100,
        blank=True,
        default=""
    )

    latitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True
    )

    longitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True
    )

    working_hours = models.CharField(
        max_length=255,
        blank=True,
        default=""
    )

    supports_delivery = models.BooleanField(
        default=True
    )

    supports_pickup = models.BooleanField(
        default=True
    )

    is_visible_on_site = models.BooleanField(
        default=False
    )

    site_sort_order = models.PositiveIntegerField(
        default=0
    )

    def __str__(self):
        return self.name

class UserProfile(models.Model):
    ROLE_SUPER_ADMIN = "super_admin"
    ROLE_ADMIN = "admin"
    ROLE_VIEWER = "viewer"
    ROLE_KITCHEN_STAFF = "kitchen_staff"

    ROLE_CHOICES = [
        (ROLE_SUPER_ADMIN, "Главный админ"),
        (ROLE_ADMIN, "Администратор"),
        (ROLE_VIEWER, "Просмотр"),
        (ROLE_KITCHEN_STAFF, "Сотрудник кухни"),
    ]

    SECTION_DISHES = "dishes"
    SECTION_PRODUCTS = "products"
    SECTION_PREPARATIONS = "preparations"
    SECTION_EMPLOYEES = "employees"
    SECTION_PACKAGING = "packaging"
    SECTION_UTILITIES = "utilities"
    SECTION_USERS = "users"
    SECTION_WRITE_OFFS = "writeoffs"
    SECTION_WRITE_OFF_ANALYTICS = "writeoff_analytics"
    SECTION_SHIFT_HANDOVER = "shift_handover"
    SECTION_ORDERS = "orders"
    SECTION_SETTINGS = "settings"
    SECTION_CUSTOMERS = "customers"
    SECTION_ORDER_ANALYTICS = "order_analytics"
    SECTION_ALL_ORDERS = "all_orders"
    SECTION_SHIFT_HANDOVER_ADMIN = "shift_handover_admin"
    SECTION_PURCHASES = "purchases"
    SECTION_SUPPLIERS = "suppliers"
    SECTION_INVENTORY = "inventory"
    SECTION_FINANCE = "finance"

    SECTION_CHOICES = [
        (SECTION_DISHES, "Блюда"),
        (SECTION_PRODUCTS, "Продукты"),
        (SECTION_PREPARATIONS, "Заготовки"),
        (SECTION_EMPLOYEES, "Сотрудники"),
        (SECTION_PACKAGING, "Упаковка"),
        (SECTION_UTILITIES, "Коммуналка"),
        (SECTION_USERS, "Пользователи"),
        (SECTION_WRITE_OFFS, "Списания"),
        (SECTION_WRITE_OFF_ANALYTICS, "Аналитика списаний"),
        (SECTION_SHIFT_HANDOVER, "Передача смены"),
        (SECTION_ORDERS, "Заказы"),
        (SECTION_SETTINGS, "Настройки"),
        (SECTION_CUSTOMERS, "Клиенты"),
        (SECTION_ORDER_ANALYTICS, "Аналитика заказов"),
        (SECTION_ALL_ORDERS, "Все заказы"),
        (SECTION_SHIFT_HANDOVER_ADMIN, "Передачи смен"),
        (SECTION_PURCHASES, "Закупки"),
        (SECTION_SUPPLIERS, "Поставщики"),
        (SECTION_INVENTORY, "Остатки"),
        (SECTION_FINANCE, "Финансы"),
    ]

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile"
    )

    role = models.CharField(
        max_length=20,
        choices=ROLE_CHOICES,
        default=ROLE_VIEWER
    )

    countries = models.ManyToManyField(
        Country,
        blank=True,
        related_name="user_profiles"
    )

    allowed_sections = models.JSONField(
        default=list,
        blank=True
    )
    
    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="user_profiles"
    )

    def __str__(self):
        return f"{self.user.username} — {self.get_role_display()}"

    def can_edit(self):
        return self.role in [
            self.ROLE_SUPER_ADMIN,
            self.ROLE_ADMIN,
        ]

    def can_access_country(self, country):
        if self.role == self.ROLE_SUPER_ADMIN:
            return True

        return self.countries.filter(id=country.id).exists()

    def is_super_admin(self):
        return self.role == self.ROLE_SUPER_ADMIN

    def is_kitchen_staff(self):
        return self.role == self.ROLE_KITCHEN_STAFF

    def can_access_section(self, section):
        if self.role == self.ROLE_SUPER_ADMIN:
            return True

        return section in self.allowed_sections


# 🧾 СПИСАНИЯ
class WriteOff(models.Model):
    ITEM_TYPE_PRODUCT = "product"
    ITEM_TYPE_PREPARATION = "preparation"

    ITEM_TYPE_CHOICES = [
        (ITEM_TYPE_PRODUCT, "Продукт"),
        (ITEM_TYPE_PREPARATION, "Заготовка"),
    ]

    REASON_EXPIRED = "expired"
    REASON_SPOILED = "spoiled"
    REASON_COOKING_ERROR = "cooking_error"
    REASON_RETURN = "return"
    REASON_TEST = "test"
    REASON_REGRADING = "regrading"
    REASON_STAFF_MEAL = "staff_meal"
    REASON_OTHER = "other"

    REASON_CHOICES = [
        (REASON_EXPIRED, "Просрочка"),
        (REASON_SPOILED, "Порча"),
        (REASON_COOKING_ERROR, "Ошибка приготовления"),
        (REASON_RETURN, "Возврат"),
        (REASON_TEST, "Тест"),
        (REASON_REGRADING, "Пересорт"),
        (REASON_STAFF_MEAL, "Еда для персонала"),
        (REASON_OTHER, "Другое"),
    ]

    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="writeoffs"
    )

    writeoff_date = models.DateField()

    item_type = models.CharField(
        max_length=20,
        choices=ITEM_TYPE_CHOICES
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="writeoffs"
    )

    preparation = models.ForeignKey(
        Preparation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="writeoffs"
    )

    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=3
    )

    reason = models.CharField(
        max_length=30,
        choices=REASON_CHOICES
    )

    comment = models.TextField(
        blank=True
    )

    cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="writeoffs"
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    def __str__(self):
        if self.item_type == self.ITEM_TYPE_PRODUCT and self.product:
            item_name = self.product.name
        elif self.item_type == self.ITEM_TYPE_PREPARATION and self.preparation:
            item_name = self.preparation.name
        else:
            item_name = "Списание"

        return f"{item_name} — {self.quantity} — {self.get_reason_display()}"

    def calculate_cost(self):
        if self.item_type == self.ITEM_TYPE_PRODUCT and self.product:
            price = self.product.get_price()

            if price:
                return self.quantity * price.price

        if self.item_type == self.ITEM_TYPE_PREPARATION and self.preparation:
            return self.quantity * self.preparation.cost_per_kg()

        return 0

    def save(self, *args, **kwargs):
        self.cost = self.calculate_cost()
        super().save(*args, **kwargs)
        
        
# 🔁 ПЕРЕДАЧА СМЕНЫ
class ShiftHandover(models.Model):
    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="shift_handovers"
    )
    
    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shift_handovers"
    )

    shift_date = models.DateField()

    responsible = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shift_handovers"
    )

    comment = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        responsible_name = self.responsible.username if self.responsible else "—"
        return f"Передача смены {self.shift_date} — {responsible_name}"


class ShiftPurchaseNeed(models.Model):
    handover = models.ForeignKey(
        ShiftHandover,
        on_delete=models.CASCADE,
        related_name="purchase_needs"
    )

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE
    )

    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        default=0
    )

    comment = models.CharField(
        max_length=255,
        blank=True
    )


class ShiftPreparationNeed(models.Model):
    handover = models.ForeignKey(
        ShiftHandover,
        on_delete=models.CASCADE,
        related_name="preparation_needs"
    )

    preparation = models.ForeignKey(
        Preparation,
        on_delete=models.CASCADE
    )

    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        default=0
    )

    comment = models.CharField(
        max_length=255,
        blank=True
    )


class ShiftStopItem(models.Model):
    handover = models.ForeignKey(
        ShiftHandover,
        on_delete=models.CASCADE,
        related_name="stop_items"
    )

    dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE
    )

    comment = models.CharField(
        max_length=255,
        blank=True
    )    
    
    
class Customer(models.Model):

    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="customers"
    )

    phone = models.CharField(
        max_length=30,
        db_index=True
    )

    telegram = models.CharField(
        max_length=120,
        blank=True
    )

    name = models.CharField(
        max_length=255
    )

    comment = models.TextField(
        blank=True
    )

    is_problematic = models.BooleanField(
        default=False
    )

    is_regular = models.BooleanField(
        default=False
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    def __str__(self):
        return f"{self.name} ({self.phone})"


class CustomerAddress(models.Model):

    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="addresses"
    )

    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    address = models.TextField()

    comment = models.CharField(
        max_length=255,
        blank=True
    )

    is_default = models.BooleanField(
        default=False
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    # ===== Public website fields =====
    apartment = models.CharField(
        max_length=50,
        blank=True,
        default=""
    )

    entrance = models.CharField(
        max_length=50,
        blank=True,
        default=""
    )

    floor = models.CharField(
        max_length=50,
        blank=True,
        default=""
    )

    intercom = models.CharField(
        max_length=50,
        blank=True,
        default=""
    )

    landmark = models.CharField(
        max_length=255,
        blank=True,
        default=""
    )

    courier_comment = models.CharField(
        max_length=255,
        blank=True,
        default=""
    )

    latitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True
    )

    longitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True
    )

    def __str__(self):
        return self.address


class OrderSource(models.Model):

    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="order_sources"
    )

    name = models.CharField(
        max_length=120
    )

    is_active = models.BooleanField(
        default=True
    )
    
    commission_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0
    )
    
    default_payment_method = models.ForeignKey(
        "PaymentMethod",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_payment_methods",
    )

    def __str__(self):
        return self.name


class DeliveryProvider(models.Model):

    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="delivery_providers"
    )

    name = models.CharField(
        max_length=120
    )

    is_active = models.BooleanField(
        default=True
    )

    def __str__(self):
        return self.name


class PromoCode(models.Model):

    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="promo_codes"
    )

    code = models.CharField(
        max_length=50,
        unique=True
    )

    percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0
    )

    is_active = models.BooleanField(
        default=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return self.code
        
            




class PaymentMethod(models.Model):

    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="payment_methods"
    )

    name = models.CharField(
        max_length=120
    )

    is_cash = models.BooleanField(
        default=False
    )

    is_active = models.BooleanField(
        default=True
    )

    def __str__(self):
        return self.name


class OrderCancelReason(models.Model):

    country = models.ForeignKey(

        Country,

        on_delete=models.CASCADE,

        related_name="order_cancel_reasons"

    )

    name = models.CharField(

        max_length=255

    )

    is_active = models.BooleanField(

        default=True

    )

    def __str__(self):

        return self.name

class Order(models.Model):

    STATUS_NEW = "new"
    STATUS_COOKING = "cooking"
    STATUS_DELIVERY = "delivery"
    STATUS_DONE = "done"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_NEW, "Новый"),
        (STATUS_COOKING, "Готовится"),
        (STATUS_DELIVERY, "Доставка"),
        (STATUS_DONE, "Завершен"),
        (STATUS_CANCELLED, "Отменен"),
    ]

    # ===== Public website constants =====
    FULFILLMENT_DELIVERY = "delivery"
    FULFILLMENT_PICKUP = "pickup"

    PAYMENT_STATUS_PENDING = "pending"
    PAYMENT_STATUS_PAID = "paid"
    PAYMENT_STATUS_CASH = "cash"
    PAYMENT_STATUS_FAILED = "failed"

    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="orders"
    )

    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders"
    )

    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="orders"
    )

    customer_address = models.ForeignKey(
        CustomerAddress,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    source = models.ForeignKey(
        OrderSource,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    delivery_provider = models.ForeignKey(
        DeliveryProvider,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    
    payment_method = models.ForeignKey(
        PaymentMethod,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )
    

    promo_code = models.ForeignKey(
        PromoCode,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_orders"
    )
    
    commission_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    net_revenue = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    order_date = models.DateTimeField()

    customer_name = models.CharField(
        max_length=255
    )

    customer_phone = models.CharField(
        max_length=30
    )

    customer_telegram = models.CharField(
        max_length=120,
        blank=True
    )

    delivery_address = models.TextField()

    address_comment = models.CharField(
        max_length=255,
        blank=True
    )

    customer_comment = models.TextField(
        blank=True
    )

    cashier_comment = models.TextField(
        blank=True
    )

    subtotal_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0
    )

    discount_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0
    )

    delivery_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0
    )
    
    customer_delivery_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0
    )
    
    free_customer_delivery = models.BooleanField(

        default=False

    )

    total_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0
    )
    
    is_cancelled = models.BooleanField(
        default=False
    )

    cancel_reason = models.ForeignKey(
        OrderCancelReason,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    status = models.CharField(
        max_length=30,
        choices=STATUS_CHOICES,
        default=STATUS_NEW
    )

    # ===== Public website fields =====
    fulfillment_method = models.CharField(
        max_length=30,
        default=FULFILLMENT_DELIVERY
    )

    payment_status = models.CharField(
        max_length=30,
        default=PAYMENT_STATUS_PENDING
    )

    public_order_number = models.CharField(
        max_length=50,
        blank=True,
        default="",
        db_index=True
    )

    leave_at_door = models.BooleanField(
        default=False
    )

    delivery_apartment = models.CharField(
        max_length=50,
        blank=True,
        default=""
    )

    delivery_entrance = models.CharField(
        max_length=50,
        blank=True,
        default=""
    )

    delivery_floor = models.CharField(
        max_length=50,
        blank=True,
        default=""
    )

    delivery_intercom = models.CharField(
        max_length=50,
        blank=True,
        default=""
    )

    delivery_landmark = models.CharField(
        max_length=255,
        blank=True,
        default=""
    )

    courier_comment = models.CharField(
        max_length=255,
        blank=True,
        default=""
    )

    delivery_latitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True
    )

    delivery_longitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    def __str__(self):
        return f"Заказ #{self.id}"


class OrderItem(models.Model):

    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name="items"
    )

    dish = models.ForeignKey(
        Dish,
        on_delete=models.SET_NULL,
        null=True
    )

    quantity = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        default=1
    )

    price_snapshot = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0
    )

    cost_snapshot = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0
    )

    total_price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        if self.dish:
            return self.dish.name

        return f"Позиция #{self.id}"
        
        
        
class FinancialExpense(models.Model):

    EXPENSE_RENT = "rent"
    EXPENSE_UTILITIES = "utilities"
    EXPENSE_MARKETING = "marketing"
    EXPENSE_OTHER = "other"

    EXPENSE_TYPES = [
        (EXPENSE_RENT, "Аренда"),
        (EXPENSE_UTILITIES, "Коммуналка"),
        (EXPENSE_MARKETING, "Реклама"),
        (EXPENSE_OTHER, "Прочее"),
    ]

    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="financial_expenses"
    )

    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="financial_expenses",
        null=True,
        blank=True
    )

    expense_type = models.CharField(
        max_length=30,
        choices=EXPENSE_TYPES
    )

    name = models.CharField(
        max_length=255
    )

    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0
    )

    expense_date = models.DateField()

    comment = models.TextField(
        blank=True
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    def __str__(self):
        return f"{self.name} - {self.amount}"
   
   
   
class EmployeeShift(models.Model):

    STATUS_PLANNED = "planned"
    STATUS_DONE = "done"
    STATUS_CANCELLED = "cancelled"

    STATUS_CHOICES = [
        (STATUS_PLANNED, "Запланирована"),
        (STATUS_DONE, "Отработана"),
        (STATUS_CANCELLED, "Отменена"),
    ]

    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="employee_shifts",
    )

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="shifts",
    )

    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="employee_shifts",
    )

    shift_date = models.DateField()

    hours = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=0
    )
    
    planned_hours = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=12
    )

    actual_hours = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=0
    )

    kpi_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=100
    )

    fixed_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    kpi_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    hourly_rate_snapshot = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    tax_percent_snapshot = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PLANNED
    )

    comment = models.TextField(
        blank=True,
        default=""
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def salary_before_penalties(self):
        base_salary = Decimal("0")

        if self.planned_hours > 0:
            base_salary = (
                self.fixed_amount
                / self.planned_hours
                * self.actual_hours
            )

        kpi_salary = (
            self.kpi_amount
            * self.kpi_percent
            / Decimal("100")
        )

        return base_salary + kpi_salary

    def tax_amount(self):
        return (
            self.salary_before_penalties()
            * self.tax_percent_snapshot
            / Decimal("100")
        )

    def total_company_cost(self):
        return (
            self.salary_before_penalties()
            + self.tax_amount()
        )

    def __str__(self):
        return f"{self.employee} — {self.shift_date}"


class EmployeePenaltyType(models.Model):

    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="employee_penalty_types",
    )

    name = models.CharField(max_length=255)

    default_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    is_active = models.BooleanField(default=True)

    def __str__(self):
        return self.name


class EmployeePenalty(models.Model):

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="penalties",
    )

    penalty_type = models.ForeignKey(
        EmployeePenaltyType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="penalties",
    )

    penalty_date = models.DateField()

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    reason = models.CharField(
        max_length=255,
        blank=True,
        default=""
    )

    comment = models.TextField(
        blank=True,
        default=""
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.employee} — {self.amount}"


class EmployeePayment(models.Model):

    employee = models.ForeignKey(
        Employee,
        on_delete=models.CASCADE,
        related_name="payments",
    )

    payment_date = models.DateField()

    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    comment = models.TextField(
        blank=True,
        default=""
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.employee} — {self.amount}"


# =============================================================================
# 🌐 PUBLIC WEBSITE MODELS
# Added in Part 1 of website API integration.
# No public API endpoints yet — pure data model layer.
# =============================================================================


# 🚦 ДОСТУПНОСТЬ БЛЮДА ПО ФИЛИАЛАМ (СТОП-ЛИСТ)
class DishAvailability(models.Model):
    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="dish_availabilities"
    )

    dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE,
        related_name="availabilities"
    )

    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="dish_availabilities"
    )

    is_available = models.BooleanField(default=True)
    is_stop_list = models.BooleanField(default=False)

    comment = models.CharField(
        max_length=255,
        blank=True,
        default=""
    )

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["location__name", "dish__name"]
        constraints = [
            models.UniqueConstraint(
                fields=["country", "dish", "location"],
                name="uniq_dish_availability_country_dish_location",
            )
        ]

    def __str__(self):
        return f"{self.dish} — {self.location}"


# ➕ ГРУППА ДОПОЛНЕНИЙ (АДДОНЫ)
class AddonGroup(models.Model):
    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="addon_groups"
    )

    name = models.CharField(max_length=255)

    code = models.SlugField(
        max_length=255,
        blank=True,
        default=""
    )

    is_active = models.BooleanField(default=True)

    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


# ➕ ПОЗИЦИЯ ДОПОЛНЕНИЯ
class AddonItem(models.Model):
    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="addon_items"
    )

    group = models.ForeignKey(
        AddonGroup,
        on_delete=models.CASCADE,
        related_name="items"
    )

    name = models.CharField(max_length=255)

    price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0
    )

    is_available = models.BooleanField(default=True)

    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return f"{self.group.name} — {self.name}"


# 🔗 СВЯЗЬ БЛЮДА И ГРУППЫ ДОПОЛНЕНИЙ
class DishAddonGroup(models.Model):
    dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE,
        related_name="addon_group_links"
    )

    group = models.ForeignKey(
        AddonGroup,
        on_delete=models.CASCADE,
        related_name="dish_links"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["dish", "group"],
                name="uniq_dish_addon_group",
            )
        ]

    def __str__(self):
        return f"{self.dish} — {self.group}"


# 🔗 СВЯЗЬ КАТЕГОРИИ И ГРУППЫ ДОПОЛНЕНИЙ
class CategoryAddonGroup(models.Model):
    category = models.ForeignKey(
        DishCategory,
        on_delete=models.CASCADE,
        related_name="addon_group_links"
    )

    group = models.ForeignKey(
        AddonGroup,
        on_delete=models.CASCADE,
        related_name="category_links"
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["category", "group"],
                name="uniq_category_addon_group",
            )
        ]

    def __str__(self):
        return f"{self.category} — {self.group}"


# 🚚 ЗОНА ДОСТАВКИ
class DeliveryZone(models.Model):
    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="delivery_zones"
    )

    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="delivery_zones"
    )

    name = models.CharField(max_length=255)

    delivery_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=15000
    )

    free_delivery_threshold = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=150000
    )

    estimated_time = models.CharField(
        max_length=100,
        blank=True,
        default="35–45 мин"
    )

    radius_km = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True
    )

    # 📍 Centre of the circular delivery zone (for the haversine match).
    # Nullable so the existing rows migrate cleanly; a zone without a centre
    # is simply skipped by the matcher.
    center_latitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True
    )

    center_longitude = models.DecimalField(
        max_digits=10,
        decimal_places=7,
        null=True,
        blank=True
    )

    is_active = models.BooleanField(default=True)

    # Lower value = higher priority when several zones overlap.
    site_sort_order = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    class Meta:
        ordering = ["location__name", "name"]

    def __str__(self):
        return f"{self.location.name} — {self.name}"


# ❤️ ИЗБРАННОЕ КЛИЕНТА
class CustomerFavorite(models.Model):
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="favorites"
    )

    dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE,
        related_name="favorited_by"
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["customer", "dish"],
                name="uniq_customer_favorite_customer_dish",
            )
        ]

    def __str__(self):
        return f"{self.customer} — {self.dish}"


# =============================================================================
# 🖼 ГАЛЕРЕЯ БЛЮДА — multiple uploaded images per dish (Part 4)
# =============================================================================
class DishGalleryImage(models.Model):
    dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE,
        related_name="gallery_images",
    )

    image = models.ImageField(upload_to="dishes/gallery/")

    sort_order = models.PositiveIntegerField(default=0)

    # Reserved for future crop UI. Free-form JSON so we can store
    # {"x": 0, "y": 0, "width": 0, "height": 0} or anything similar later.
    crop_data = models.JSONField(default=dict, blank=True)

    alt_text = models.CharField(
        max_length=255,
        blank=True,
        default=""
    )

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.dish} #{self.id}"


# =============================================================================
# ➕ ДОПОЛНЕНИЕ К БЛЮДУ — addon is itself a Dish (Part 4)
#
# Replaces the previous AddonGroup/AddonItem flow for the dish-detail UI.
# The old AddonGroup / AddonItem / DishAddonGroup / CategoryAddonGroup models
# are kept in the schema for backward compatibility but are no longer wired
# into the dish-detail editor or the public_api product detail.
# =============================================================================
class DishAddon(models.Model):
    dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE,
        related_name="addon_links",
    )

    addon_dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE,
        related_name="used_as_addon",
    )

    # Free-form grouping label shown on the public site
    # (e.g. "Соусы", "Добавить к пицце"). Falls back to "Дополнительно".
    group_name = models.CharField(
        max_length=255,
        blank=True,
        default=""
    )

    is_active = models.BooleanField(default=True)

    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["group_name", "sort_order", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["dish", "addon_dish"],
                name="uniq_dish_addon_dish_addon_dish",
            )
        ]

    def __str__(self):
        return f"{self.dish} ← {self.addon_dish}"


# =============================================================================
# 🏠 HOMEPAGE CMS MODELS (Part 11 — ERP-driven homepage management)
# =============================================================================
#
# Three small models that drive the website homepage from the ERP:
#   - HomepageBanner            — hero/promo banners (CRUD in Django admin)
#   - HomepageProductBlock      — manually curated "frequently bought" blocks
#   - HomepageProductBlockItem  — dishes inside one block
#
# Bestsellers don't need their own model — they reuse Dish.is_featured.
#
# All public homepage endpoints filter by country, so adding a banner or
# a block in Uzbekistan doesn't leak into Montenegro and vice versa.


class HomepageBanner(models.Model):
    """
    ERP-managed homepage banner.

    The action_type/action_value pair tells the website what to do when
    a customer taps the banner:
      - "category"    → open the category whose slug matches action_value
      - "product"     → open the product (Dish) whose slug matches action_value
      - "external_url"→ open action_value as a full URL in a new tab
      - "none"        → display only; no action
    """

    ACTION_CATEGORY = "category"
    ACTION_PRODUCT = "product"
    ACTION_EXTERNAL_URL = "external_url"
    ACTION_NONE = "none"

    ACTION_TYPE_CHOICES = [
        (ACTION_CATEGORY,     "Категория"),
        (ACTION_PRODUCT,      "Товар"),
        (ACTION_EXTERNAL_URL, "Внешняя ссылка"),
        (ACTION_NONE,         "Без действия"),
    ]

    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="homepage_banners",
    )

    title = models.CharField(max_length=255)
    subtitle = models.CharField(max_length=500, blank=True, default="")

    # When False, the website must render the banner image ONLY — no title /
    # subtitle overlay. Click behaviour (action_type/action_value) is unaffected.
    # Default True keeps every existing banner behaving exactly as before.
    show_text = models.BooleanField(default=True)

    desktop_image = models.URLField(max_length=1000, blank=True, default="")
    mobile_image = models.URLField(max_length=1000, blank=True, default="")

    action_type = models.CharField(
        max_length=20,
        choices=ACTION_TYPE_CHOICES,
        default=ACTION_NONE,
    )
    action_value = models.CharField(max_length=500, blank=True, default="")

    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    # Optional scheduled window. Both nullable so banners can be "always on".
    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "Баннер на главной"
        verbose_name_plural = "Баннеры на главной"

    def __str__(self):
        return f"{self.title} ({self.country.slug})"


class HomepageProductBlock(models.Model):
    """
    Manually curated homepage block (e.g. "Часто заказывают вместе").

    There is no ML, no auto-recommendation. Operators add concrete dishes
    via HomepageProductBlockItem inlines.
    """

    country = models.ForeignKey(
        Country,
        on_delete=models.CASCADE,
        related_name="homepage_product_blocks",
    )

    title = models.CharField(
        max_length=255,
        default="Часто заказывают вместе",
    )
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "Блок на главной"
        verbose_name_plural = "Блоки на главной"

    def __str__(self):
        return f"{self.title} ({self.country.slug})"


class HomepageProductBlockItem(models.Model):
    """One dish inside a HomepageProductBlock, with its own sort order."""

    block = models.ForeignKey(
        HomepageProductBlock,
        on_delete=models.CASCADE,
        related_name="items",
    )
    dish = models.ForeignKey(
        Dish,
        on_delete=models.CASCADE,
        related_name="homepage_block_items",
    )

    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "id"]
        verbose_name = "Товар в блоке"
        verbose_name_plural = "Товары в блоке"
        constraints = [
            models.UniqueConstraint(
                fields=["block", "dish"],
                name="uniq_homepage_block_dish",
            ),
        ]

    def __str__(self):
        return f"{self.block.title} → {self.dish}"