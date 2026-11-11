"""Демо-данные кассы для быстрой сквозной проверки.

Идемпотентно (get_or_create): можно запускать повторно. Ничего в foodcost не
меняет по схеме; создаёт только простые записи — способы оплаты и демо-гостя
(это обычные данные, не схема). Блюда и категории НЕ создаёт — переиспользует
существующие (их создавать рискованно: у Dish много обязательных полей).

Запуск:
    python manage.py seed_pos_defaults            # первый арендатор
    python manage.py seed_pos_defaults --tenant romashka
"""

from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from foodcost.models import Tenant, DishCategory, Dish, PaymentMethod, Customer
from pos.models import (
    KitchenScreen, KitchenTimeThreshold, ServiceTipSettings,
    LoyaltyProgram, CustomerPoints, FloorZone, FloorTable,
    FiscalizationSettings, PaymentMethodLocationConfig,
)


class Command(BaseCommand):
    help = "Создаёт демо-настройки кассы (экран KDS, зона/столы, оплаты, сервис, лояльность, фискализация)."

    def add_arguments(self, parser):
        parser.add_argument("--tenant", dest="tenant", default=None,
                            help="slug арендатора (по умолчанию — первый).")

    def handle(self, *args, **opts):
        slug = opts.get("tenant")
        if slug:
            tenant = Tenant.objects.filter(slug=slug).first()
            if not tenant:
                raise CommandError(f"Арендатор со slug «{slug}» не найден.")
        else:
            tenant = Tenant.objects.order_by("id").first()
            if not tenant:
                raise CommandError("Нет ни одного арендатора (Tenant).")

        self.stdout.write(f"Арендатор: {tenant} (slug={tenant.slug})")
        created = []

        # --- способы оплаты (foodcost.PaymentMethod) ---
        cash, c = PaymentMethod.objects.get_or_create(
            country=tenant, name="Наличные", defaults={"is_cash": True, "is_active": True})
        if c: created.append("способ оплаты «Наличные»")
        card, c = PaymentMethod.objects.get_or_create(
            country=tenant, name="Карта", defaults={"is_cash": False, "is_active": True})
        if c: created.append("способ оплаты «Карта»")
        for m in (cash, card):
            _, c = PaymentMethodLocationConfig.objects.get_or_create(
                payment_method=m, location=None,
                defaults={"is_visible": True, "fiscalization": PaymentMethodLocationConfig.FISCAL_CASHBOX})
            if c: created.append(f"видимость способа «{m.name}»")

        # --- зона + столы ---
        zone, c = FloorZone.objects.get_or_create(
            tenant=tenant, name="Основной зал", defaults={"location": None, "sort_order": 0})
        if c: created.append("зона «Основной зал»")
        for n in range(1, 5):
            # раскладываем по сетке, чтобы столы не накладывались на плане
            px = 18 + ((n - 1) % 3) * 28
            py = 25 + ((n - 1) // 3) * 35
            _, c = FloorTable.objects.get_or_create(
                zone=zone, number=str(n),
                defaults={"seats": 4, "shape": FloorTable.SHAPE_SQUARE,
                          "pos_x": px, "pos_y": py})
            if c: created.append(f"стол {n}")

        # --- экран KDS + пороги ---
        screen, c = KitchenScreen.objects.get_or_create(
            tenant=tenant, name="Горячий цех",
            defaults={"location": None, "pairing_code": "4821", "is_connected": True,
                      "is_active": True, "sort_order": 0})
        if c: created.append("экран KDS «Горячий цех»")
        cats = list(DishCategory.objects.filter(country=tenant)[:10])
        if cats:
            screen.categories.add(*cats)
        else:
            self.stdout.write(self.style.WARNING(
                "  ! Категорий блюд нет — экран KDS без категорий. Заведите блюда/категории, "
                "чтобы кухня подбиралась автоматически."))
        _, c = KitchenTimeThreshold.objects.get_or_create(
            tenant=tenant, screen=screen, category=None, dish=None,
            defaults={"warn_minutes": 8, "late_minutes": 15})
        if c: created.append("порог времени (общий 8/15 мин)")

        # --- сервис и чаевые ---
        _, c = ServiceTipSettings.objects.get_or_create(
            tenant=tenant, location=None,
            defaults={"service_enabled": True, "service_percent": Decimal("10"),
                      "service_dine_in": True, "service_allow_remove": True,
                      "tips_enabled": True, "tips_presets": [5, 10, 15],
                      "tips_allow_custom": True})
        if c: created.append("сервис 10% + чаевые 5/10/15%")

        # --- лояльность + демо-гость ---
        prog, c = LoyaltyProgram.objects.get_or_create(
            tenant=tenant,
            defaults={"enabled": True, "identify_by_phone": True,
                      "earn_percent": Decimal("5"), "redeem_max_percent": Decimal("50"),
                      "combine_with_discounts": True})
        if c: created.append("программа лояльности (начисление 5%, списание до 50%)")
        guest, c = Customer.objects.get_or_create(
            country=tenant, phone="+998901112233", defaults={"name": "Гость Демо"})
        if c: created.append("демо-гость +998901112233")
        _, c = CustomerPoints.objects.get_or_create(
            customer=guest, defaults={"balance": Decimal("50000")})
        if c: created.append("баланс демо-гостя 50 000 баллов")

        # --- фискализация (тест) ---
        _, c = FiscalizationSettings.objects.get_or_create(
            tenant=tenant, location=None,
            defaults={"mode": FiscalizationSettings.MODE_TEST,
                      "vat_mode": FiscalizationSettings.VAT_INCLUDED_12, "is_connected": True})
        if c: created.append("фискализация (тест)")

        dish_count = Dish.objects.filter(country=tenant).count()
        self.stdout.write("")
        if created:
            self.stdout.write(self.style.SUCCESS("Создано:"))
            for x in created:
                self.stdout.write(f"  + {x}")
        else:
            self.stdout.write("Всё уже было создано ранее (идемпотентно, повторный запуск).")
        self.stdout.write("")
        self.stdout.write(f"Блюд у арендатора: {dish_count}"
                          + ("" if dish_count else "  ← добавьте блюда, чтобы класть их в заказ!"))
        self.stdout.write(self.style.SUCCESS(
            f"Готово. Открой /c/{tenant.slug}/pos/order/ и пройди заказ → кухню → оплату."))
