"""POS (касса) — новые таблицы настроек.

Всё здесь аддитивно: только новые таблицы, никаких изменений в foodcost.
Связь с основным приложением — по строковым FK ("foodcost.Tenant" и т.д.),
чтобы не импортировать модели foodcost и не создавать циклов.

Арендатор в foodcost называется Tenant (есть алиас Country = Tenant).
Новые модели ссылаются на канонический "foodcost.Tenant".
Привязка к точке (филиалу) — через "foodcost.Location"; null = «для всех точек».
"""

from django.db import models
from django.utils.translation import gettext_lazy as _


# =========================================================================
# КУХНЯ: экраны (KDS) и пороги времени
# =========================================================================

class KitchenScreen(models.Model):
    """Экран кухни (KDS) = цех. Заводится в админке, подключается по коду.

    Какие категории/блюда показывать — через M2M categories/dishes.
    Привязан к точке (location); null = экран обслуживает все точки.
    """

    tenant = models.ForeignKey(
        "foodcost.Tenant", on_delete=models.CASCADE,
        related_name="kitchen_screens",
    )
    location = models.ForeignKey(
        "foodcost.Location", on_delete=models.CASCADE,
        null=True, blank=True, related_name="kitchen_screens",
        help_text="Точка экрана. Пусто — экран общий для всех точек.",
    )

    name = models.CharField(max_length=120)

    # Какие категории/блюда выводятся на этот экран.
    categories = models.ManyToManyField(
        "foodcost.DishCategory", blank=True, related_name="kitchen_screens",
    )
    dishes = models.ManyToManyField(
        "foodcost.Dish", blank=True, related_name="kitchen_screens",
        help_text="Точечно отдельные блюда (помимо категорий).",
    )

    # --- Подключение устройства ---
    pairing_code = models.CharField(
        max_length=12, blank=True, default="",
        help_text="Код для привязки планшета/монитора.",
    )
    is_connected = models.BooleanField(default=False)
    device_label = models.CharField(max_length=120, blank=True, default="")
    last_seen_at = models.DateTimeField(null=True, blank=True)

    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Кухонный экран (KDS)"
        verbose_name_plural = "Кухонные экраны (KDS)"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.name


class KitchenTimeThreshold(models.Model):
    """Порог времени для таймеров KDS: когда тикет желтеет и краснеет.

    Область определяется заполненными FK:
      - screen пусто + category пусто + dish пусто → общий порог (default)
      - screen задан → порог для конкретного экрана
      - category / dish заданы → переопределение по категории / блюду
    Чем конкретнее заполнение, тем выше приоритет (разруливается в коде кассы).
    """

    tenant = models.ForeignKey(
        "foodcost.Tenant", on_delete=models.CASCADE,
        related_name="kitchen_thresholds",
    )
    screen = models.ForeignKey(
        KitchenScreen, on_delete=models.CASCADE,
        null=True, blank=True, related_name="thresholds",
        help_text="Экран KDS. Пусто — все экраны.",
    )
    category = models.ForeignKey(
        "foodcost.DishCategory", on_delete=models.CASCADE,
        null=True, blank=True, related_name="kitchen_thresholds",
    )
    dish = models.ForeignKey(
        "foodcost.Dish", on_delete=models.CASCADE,
        null=True, blank=True, related_name="kitchen_thresholds",
    )

    warn_minutes = models.PositiveIntegerField(
        default=8, help_text="Через сколько минут тикет желтеет.",
    )
    late_minutes = models.PositiveIntegerField(
        default=15, help_text="Через сколько минут тикет краснеет.",
    )

    class Meta:
        verbose_name = "Порог времени KDS"
        verbose_name_plural = "Пороги времени KDS"
        ordering = ["id"]

    def __str__(self):
        target = self.dish or self.category or self.screen or "общий"
        return f"Порог {target}: {self.warn_minutes}/{self.late_minutes} мин"


# =========================================================================
# ОБСЛУЖИВАНИЕ И ЧАЕВЫЕ
# =========================================================================

class ServiceTipSettings(models.Model):
    """Сервисный сбор и чаевые. Одна запись на (точку); location=null —
    общие настройки для всех точек (переопределяются записью по точке)."""

    TIPS_RECIPIENT_WAITER = "waiter"
    TIPS_RECIPIENT_POOL = "pool"
    TIPS_RECIPIENT_CHOICES = [
        (TIPS_RECIPIENT_WAITER, _("Официант заказа")),
        (TIPS_RECIPIENT_POOL, _("Общий фонд")),
    ]

    tenant = models.ForeignKey(
        "foodcost.Tenant", on_delete=models.CASCADE,
        related_name="service_tip_settings",
    )
    location = models.ForeignKey(
        "foodcost.Location", on_delete=models.CASCADE,
        null=True, blank=True, related_name="service_tip_settings",
        help_text="Пусто — настройки для всех точек.",
    )

    # --- Сервисный сбор (обслуживание) ---
    service_enabled = models.BooleanField(default=False)
    service_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
    )
    service_dine_in = models.BooleanField(default=True)
    service_delivery = models.BooleanField(default=False)
    service_pickup = models.BooleanField(default=False)
    service_auto_add = models.BooleanField(
        default=False, help_text="Добавлять автоматически, иначе кнопкой кассира.",
    )
    service_allow_remove = models.BooleanField(
        default=True, help_text="Кассир может снять сбор по просьбе гостя.",
    )

    # --- Чаевые ---
    tips_enabled = models.BooleanField(default=False)
    tips_presets = models.JSONField(
        default=list, blank=True,
        help_text="Список процентов быстрых чаевых, напр. [5, 10, 15].",
    )
    tips_allow_custom = models.BooleanField(default=True)
    tips_recipient = models.CharField(
        max_length=12, choices=TIPS_RECIPIENT_CHOICES,
        default=TIPS_RECIPIENT_WAITER,
    )

    class Meta:
        verbose_name = "Обслуживание и чаевые"
        verbose_name_plural = "Обслуживание и чаевые"

    def __str__(self):
        scope = self.location.name if self.location_id else "все точки"
        return f"Обслуживание/чаевые ({scope})"


# =========================================================================
# ЛОЯЛЬНОСТЬ И БАЛЛЫ (нового в коде не было)
# =========================================================================

class LoyaltyProgram(models.Model):
    """Программа лояльности арендатора (одна на арендатора)."""

    tenant = models.OneToOneField(
        "foodcost.Tenant", on_delete=models.CASCADE,
        related_name="loyalty_program",
    )

    enabled = models.BooleanField(default=False)

    # Идентификация гостя
    identify_by_phone = models.BooleanField(default=True)
    identify_by_qr = models.BooleanField(default=False)
    require_sms_on_redeem = models.BooleanField(
        default=True, help_text="Подтверждать списание баллов по SMS.",
    )

    # Начисление
    earn_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        help_text="Процент заказа, начисляемый баллами (1 балл = 1 сум).",
    )
    earn_exclude_discounted = models.BooleanField(
        default=False, help_text="Не начислять на позиции со скидкой.",
    )

    # Списание
    redeem_max_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=50,
        help_text="Макс. доля счёта, оплачиваемая баллами.",
    )
    combine_with_discounts = models.BooleanField(
        default=False, help_text="Можно совмещать списание со скидками.",
    )
    points_expiry_months = models.PositiveIntegerField(
        default=12, help_text="Срок сгорания баллов, мес. 0 — не сгорают.",
    )

    tiers_enabled = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Программа лояльности"
        verbose_name_plural = "Программы лояльности"

    def __str__(self):
        return f"Лояльность: {self.tenant_id} ({'вкл' if self.enabled else 'выкл'})"


class LoyaltyTier(models.Model):
    """Уровень гостя (бронза/серебро/золото), опционально."""

    program = models.ForeignKey(
        LoyaltyProgram, on_delete=models.CASCADE, related_name="tiers",
    )
    name = models.CharField(max_length=80)
    min_turnover = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text="Оборот гостя, с которого действует уровень.",
    )
    earn_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    color = models.CharField(max_length=20, blank=True, default="")
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Уровень лояльности"
        verbose_name_plural = "Уровни лояльности"
        ordering = ["sort_order", "min_turnover", "id"]

    def __str__(self):
        return self.name


class CustomerPoints(models.Model):
    """Баланс баллов гостя. Отдельная таблица, чтобы не трогать foodcost.Customer."""

    customer = models.OneToOneField(
        "foodcost.Customer", on_delete=models.CASCADE, related_name="loyalty_points",
    )
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Баллы гостя"
        verbose_name_plural = "Баллы гостей"

    def __str__(self):
        return f"{self.customer_id}: {self.balance}"


# =========================================================================
# ЗАЛ: зоны, столы, бронь (расстановка столов)
# =========================================================================

class FloorZone(models.Model):
    """Зона зала (создаётся вручную, привязана к точке)."""

    tenant = models.ForeignKey(
        "foodcost.Tenant", on_delete=models.CASCADE, related_name="floor_zones",
    )
    location = models.ForeignKey(
        "foodcost.Location", on_delete=models.CASCADE,
        null=True, blank=True, related_name="floor_zones",
    )
    name = models.CharField(max_length=120)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Зона зала"
        verbose_name_plural = "Зоны зала"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return self.name


class FloorTable(models.Model):
    """Стол на плане зала."""

    SHAPE_ROUND = "round"
    SHAPE_SQUARE = "square"
    SHAPE_RECT = "rect"
    SHAPE_CHOICES = [
        (SHAPE_ROUND, _("Круглый")),
        (SHAPE_SQUARE, _("Квадратный")),
        (SHAPE_RECT, _("Длинный")),
    ]

    zone = models.ForeignKey(
        FloorZone, on_delete=models.CASCADE, related_name="tables",
    )
    number = models.CharField(max_length=20)
    seats = models.PositiveIntegerField(default=2)
    shape = models.CharField(
        max_length=10, choices=SHAPE_CHOICES, default=SHAPE_SQUARE,
    )
    # Позиция на холсте в процентах (0..100), чтобы не зависеть от размера.
    pos_x = models.DecimalField(max_digits=5, decimal_places=2, default=50)
    pos_y = models.DecimalField(max_digits=5, decimal_places=2, default=50)

    is_mergeable = models.BooleanField(default=True)
    is_bookable = models.BooleanField(default=True)
    assigned_waiter = models.ForeignKey(
        "foodcost.Employee", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="assigned_tables",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name = "Стол"
        verbose_name_plural = "Столы"
        ordering = ["zone", "number"]

    def __str__(self):
        return f"Стол {self.number}"


class TableBooking(models.Model):
    """Бронь стола."""

    STATUS_ACTIVE = "active"
    STATUS_SEATED = "seated"
    STATUS_CANCELLED = "cancelled"
    STATUS_NO_SHOW = "no_show"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, _("Активна")),
        (STATUS_SEATED, _("Гость сел")),
        (STATUS_CANCELLED, _("Отменена")),
        (STATUS_NO_SHOW, _("Не пришёл")),
    ]

    table = models.ForeignKey(
        FloorTable, on_delete=models.CASCADE, related_name="bookings",
    )
    guest_name = models.CharField(max_length=255, blank=True, default="")
    phone = models.CharField(max_length=40, blank=True, default="")
    booking_date = models.DateField()
    booking_time = models.TimeField()
    duration_minutes = models.PositiveIntegerField(default=120)
    guests = models.PositiveIntegerField(default=2)
    comment = models.CharField(max_length=500, blank=True, default="")
    status = models.CharField(
        max_length=12, choices=STATUS_CHOICES, default=STATUS_ACTIVE,
    )
    created_by = models.ForeignKey(
        "auth.User", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="table_bookings",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Бронь стола"
        verbose_name_plural = "Брони столов"
        ordering = ["-booking_date", "-booking_time"]

    def __str__(self):
        return f"{self.table} — {self.booking_date} {self.booking_time}"


# =========================================================================
# ОПЛАТЫ И ФИСКАЛИЗАЦИЯ (привязка к точке; foodcost.PaymentMethod не трогаем)
# =========================================================================

class FiscalizationSettings(models.Model):
    """Настройки фискализации по точке (реквизиты юр.лица берутся из Tenant/
    настроек, здесь только фискальная привязка)."""

    MODE_TEST = "test"
    MODE_LIVE = "live"
    MODE_CHOICES = [
        (MODE_TEST, _("Тест")),
        (MODE_LIVE, _("Боевой")),
    ]

    VAT_INCLUDED_12 = "vat12"
    VAT_ZERO = "vat0"
    VAT_NONE = "none"
    VAT_CHOICES = [
        (VAT_INCLUDED_12, _("12% — в том числе")),
        (VAT_ZERO, _("0% — без НДС")),
        (VAT_NONE, _("Не плательщик НДС")),
    ]

    tenant = models.ForeignKey(
        "foodcost.Tenant", on_delete=models.CASCADE,
        related_name="fiscalization_settings",
    )
    location = models.ForeignKey(
        "foodcost.Location", on_delete=models.CASCADE,
        null=True, blank=True, related_name="fiscalization_settings",
        help_text="Пусто — настройки для всех точек.",
    )
    mode = models.CharField(max_length=8, choices=MODE_CHOICES, default=MODE_TEST)
    vat_mode = models.CharField(
        max_length=8, choices=VAT_CHOICES, default=VAT_INCLUDED_12,
        help_text="НДС показывается только в фискальном чеке, не на кассе.",
    )
    is_connected = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Фискализация"
        verbose_name_plural = "Фискализация"

    def __str__(self):
        scope = self.location.name if self.location_id else "все точки"
        return f"Фискализация ({scope}, {self.get_mode_display()})"


class PaymentMethodLocationConfig(models.Model):
    """Привязка способа оплаты (foodcost.PaymentMethod) к точке: видимость на
    кассе, кто фискализирует, порядок. Сам PaymentMethod не изменяется."""

    FISCAL_CASHBOX = "cashbox"
    FISCAL_PROVIDER = "provider"
    FISCAL_NONE = "none"
    FISCAL_CHOICES = [
        (FISCAL_CASHBOX, _("Касса сама")),
        (FISCAL_PROVIDER, _("Платёжная система")),
        (FISCAL_NONE, _("Не фискализировать")),
    ]

    payment_method = models.ForeignKey(
        "foodcost.PaymentMethod", on_delete=models.CASCADE,
        related_name="location_configs",
    )
    location = models.ForeignKey(
        "foodcost.Location", on_delete=models.CASCADE,
        null=True, blank=True, related_name="payment_method_configs",
        help_text="Пусто — для всех точек.",
    )
    is_visible = models.BooleanField(default=True)
    fiscalization = models.CharField(
        max_length=10, choices=FISCAL_CHOICES, default=FISCAL_CASHBOX,
    )
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Способ оплаты по точке"
        verbose_name_plural = "Способы оплаты по точкам"
        ordering = ["sort_order", "id"]

    def __str__(self):
        scope = self.location.name if self.location_id else "все точки"
        return f"{self.payment_method} ({scope})"


# =========================================================================
# ЗАКАЗ НА КАССЕ (аддитивное расширение foodcost.Order / OrderItem)
# =========================================================================
# foodcost.Order заточен под доставку/самовывоз (есть customer_name/phone,
# delivery_address, fulfillment). Для зала (dine-in) нужны стол, гости,
# официант, сервисный сбор/чаевые. Мы НЕ трогаем Order — храним это в
# связанной OneToOne-таблице PosOrder. Аналогично PosOrderItem добавляет
# позиции статус для KDS (принято/готовится/готово) и курс/скидку, не трогая
# foodcost.OrderItem (там нет per-item статуса).

class PosOrder(models.Model):
    """POS-надстройка над foodcost.Order (зал/обслуживание)."""

    order = models.OneToOneField(
        "foodcost.Order", on_delete=models.CASCADE, related_name="pos",
    )
    is_dine_in = models.BooleanField(
        default=True,
        help_text="Заказ в зале (на столе). False — доставка/самовывоз.",
    )
    table = models.ForeignKey(
        "pos.FloorTable", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="orders",
    )
    guests = models.PositiveIntegerField(default=1)
    waiter = models.ForeignKey(
        "foodcost.Employee", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="pos_orders",
    )
    service_charge_amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
        help_text="Сервисный сбор (сумма), рассчитан по ServiceTipSettings.",
    )
    tips_amount = models.DecimalField(
        max_digits=14, decimal_places=2, default=0,
    )
    # Бонусы лояльности, списанные на этот счёт (1 балл = 1 ден. ед.).
    bonus_points = models.PositiveIntegerField(default=0)
    bonus_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    loyalty_customer = models.ForeignKey(
        "foodcost.Customer", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="pos_loyalty_orders",
    )

    # Деление счёта (B3.1): none — обычная оплата; positions — по позициям;
    # even — поровну на N под-счетов.
    SPLIT_NONE = "none"
    SPLIT_POSITIONS = "positions"
    SPLIT_EVEN = "even"
    SPLIT_CHOICES = [
        (SPLIT_NONE, _("Без деления")),
        (SPLIT_POSITIONS, _("По позициям")),
        (SPLIT_EVEN, _("Поровну")),
    ]
    split_mode = models.CharField(max_length=12, choices=SPLIT_CHOICES, default=SPLIT_NONE)

    opened_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "POS-заказ"
        verbose_name_plural = "POS-заказы"

    def __str__(self):
        if self.table_id:
            return f"POS-заказ #{self.order_id} · стол {self.table.number}"
        return f"POS-заказ #{self.order_id}"


class PosOrderItem(models.Model):
    """POS-надстройка над foodcost.OrderItem: статус для KDS, курс, скидка."""

    KS_ACCEPTED = "accepted"
    KS_COOKING = "cooking"
    KS_READY = "ready"
    KS_CANCELLED = "cancelled"
    KITCHEN_STATUS_CHOICES = [
        (KS_ACCEPTED, _("Принято")),
        (KS_COOKING, _("Готовится")),
        (KS_READY, _("Готово")),
        (KS_CANCELLED, _("Отменено")),
    ]

    order_item = models.OneToOneField(
        "foodcost.OrderItem", on_delete=models.CASCADE, related_name="pos",
    )
    kitchen_status = models.CharField(
        max_length=12, choices=KITCHEN_STATUS_CHOICES, default=KS_ACCEPTED,
    )
    screen = models.ForeignKey(
        KitchenScreen, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="items",
        help_text="На каком экране KDS готовится (определяется по категории).",
    )
    bill = models.ForeignKey(
        "pos.PosBill", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="items",
        help_text="Под-счёт при делении «по позициям» (B3.1).",
    )
    course = models.PositiveIntegerField(
        default=1, help_text="Подача (курс): 1 — первая, 2 — вторая и т.д.",
    )
    sent_to_kitchen_at = models.DateTimeField(null=True, blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)
    discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, default=0,
        help_text="Индивидуальная скидка на позицию, %.",
    )
    discount_reason = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "POS-позиция"
        verbose_name_plural = "POS-позиции"

    def __str__(self):
        return f"POS-позиция #{self.order_item_id} ({self.get_kitchen_status_display()})"

    @property
    def cook_seconds(self):
        """Сколько секунд готовится (или готовилось) позиция. None — ещё не
        отправлена на кухню."""
        if not self.sent_to_kitchen_at:
            return None
        end = self.ready_at
        if end is None:
            from django.utils import timezone
            end = timezone.now()
        return int((end - self.sent_to_kitchen_at).total_seconds())


class PosPayment(models.Model):
    """Приём оплаты по счёту. Несколько строк на один заказ = оплата
    несколькими способами. foodcost.Order/PaymentMethod не меняются."""

    FISCAL_PENDING = "pending"
    FISCAL_DONE = "fiscalized"
    FISCAL_SKIPPED = "skipped"
    FISCAL_TEST = "test"
    FISCAL_CHOICES = [
        (FISCAL_PENDING, _("Ожидает")),
        (FISCAL_DONE, _("Фискализирован")),
        (FISCAL_SKIPPED, _("Без фискализации")),
        (FISCAL_TEST, _("Тест")),
    ]

    order = models.ForeignKey(
        "foodcost.Order", on_delete=models.CASCADE, related_name="pos_payments",
    )
    bill = models.ForeignKey(
        "pos.PosBill", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="payments",
        help_text="Под-счёт, если счёт делился (B3.1). Пусто — оплата всего заказа.",
    )
    method = models.ForeignKey(
        "foodcost.PaymentMethod", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="pos_payments",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    fiscal_status = models.CharField(
        max_length=12, choices=FISCAL_CHOICES, default=FISCAL_PENDING,
    )
    # Результат фискализации от провайдера (e-pos). Заполняет провайдер через
    # pos.fiscalization.fiscalize_payment. У заглушки пусто.
    fiscal_receipt_id = models.CharField(max_length=128, blank=True, default="")
    fiscal_error = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Оплата (POS)"
        verbose_name_plural = "Оплаты (POS)"
        ordering = ["id"]

    def __str__(self):
        return f"Оплата #{self.order_id}: {self.amount} ({self.method_id})"


class PosBill(models.Model):
    """Под-счёт при делении счёта (B3.1).

    Режим «по позициям» — позиции привязаны через PosOrderItem.bill, сумма =
    их стоимость. Режим «поровну» — позиции не привязаны, сумма зафиксирована
    (равная доля итога). Заказ закрывается, когда оплачены все под-счета.
    """

    pos_order = models.ForeignKey(
        "pos.PosOrder", on_delete=models.CASCADE, related_name="bills",
    )
    label = models.CharField(max_length=60, default="")
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    is_paid = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "Под-счёт (POS)"
        verbose_name_plural = "Под-счета (POS)"
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.label or 'Счёт'} ({self.amount})"
