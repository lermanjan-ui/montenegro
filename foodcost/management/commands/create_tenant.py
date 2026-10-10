"""
Провижининг нового арендатора (онбординг — шаг к продаже продукта).

Создаёт изолированный кабинет «под ключ»:
  - Country (= арендатор) с уникальным slug для /c/<slug>/
  - первый филиал (Location)
  - базовые справочники: способы оплаты, источники заказа, служба доставки
  - админа клиента: обычный пользователь с ролью admin и доступом ТОЛЬКО к
    этому арендатору. ВАЖНО: он НЕ Django-суперюзер — иначе видел бы всех
    арендаторов (это и есть ключевое отличие продукта от «инструмента для себя»).

Идемпотентность: падает с понятной ошибкой, если slug или username заняты —
ничего не перезаписывает.

Пример:
    python manage.py create_tenant \
        --name "Кафе Ромашка" --slug romashka \
        --admin-username romashka_admin --admin-password 'СменитеМеня123'
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from foodcost.models import (
    Country, Location, PaymentMethod, OrderSource, DeliveryProvider, UserProfile,
)


class Command(BaseCommand):
    help = "Создать нового арендатора (кабинет + филиал + справочники + админ клиента)."

    def add_arguments(self, parser):
        parser.add_argument("--name", required=True, help="Название арендатора")
        parser.add_argument("--slug", required=True, help="Слаг для URL: /c/<slug>/")
        parser.add_argument("--admin-username", required=True, help="Логин админа клиента")
        parser.add_argument("--admin-password", required=True, help="Пароль админа клиента")
        parser.add_argument("--location", default="Главный филиал", help="Название первого филиала")

    @transaction.atomic
    def handle(self, *args, **o):
        slug = slugify(o["slug"])
        if not slug:
            raise CommandError("Пустой slug после нормализации — укажите латиницей.")
        if Country.objects.filter(slug=slug).exists():
            raise CommandError(f"Арендатор со slug '{slug}' уже существует.")

        User = get_user_model()
        username = o["admin_username"]
        if User.objects.filter(username=username).exists():
            raise CommandError(f"Пользователь '{username}' уже существует.")

        # 1) Арендатор + филиал
        tenant = Country.objects.create(name=o["name"], slug=slug)
        location = Location.objects.create(country=tenant, name=o["location"])

        # 2) Базовые справочники
        PaymentMethod.objects.create(country=tenant, name="Наличные", is_cash=True)
        PaymentMethod.objects.create(country=tenant, name="Карта", is_cash=False)
        OrderSource.objects.create(country=tenant, name="Сайт")
        OrderSource.objects.create(country=tenant, name="Телефон")
        DeliveryProvider.objects.create(country=tenant, name="Свои курьеры")

        # 3) Админ клиента — НЕ Django-суперюзер
        admin = User.objects.create_user(username=username, password=o["admin_password"])
        admin.is_staff = False
        admin.is_superuser = False
        admin.save(update_fields=["is_staff", "is_superuser"])

        all_sections = [code for code, _ in UserProfile.SECTION_CHOICES]
        profile, _ = UserProfile.objects.update_or_create(
            user=admin,
            defaults={
                "role": UserProfile.ROLE_ADMIN,
                "allowed_sections": all_sections,
                "location": location,
            },
        )
        profile.countries.add(tenant)

        self.stdout.write(self.style.SUCCESS(
            f"✓ Арендатор «{tenant.name}» создан.\n"
            f"  URL кабинета: /c/{slug}/\n"
            f"  Админ: {admin.username} (роль admin, доступ только к этому арендатору; не суперюзер)\n"
            f"  Филиал: {location.name}; справочники: оплата/источники/доставка заведены."
        ))
