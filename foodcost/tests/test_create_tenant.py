"""Онбординг: команда create_tenant создаёт изолированный кабинет,
а админ клиента не является Django-суперюзером и не видит чужих арендаторов."""
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from foodcost.models import Country, Location, OrderSource, PaymentMethod, UserProfile
from foodcost.views import user_can_access_country


class CreateTenantCommandTests(TestCase):
    def test_provisions_isolated_workspace(self):
        call_command("create_tenant", name="Клиент А", slug="client-a",
                     admin_username="admin_a", admin_password="secret12345")
        tenant = Country.objects.get(slug="client-a")
        self.assertTrue(Location.objects.filter(country=tenant).exists())
        self.assertEqual(PaymentMethod.objects.filter(country=tenant).count(), 2)
        self.assertTrue(OrderSource.objects.filter(country=tenant).exists())

        admin = get_user_model().objects.get(username="admin_a")
        # Ключевое: админ клиента — НЕ Django-суперюзер
        self.assertFalse(admin.is_superuser)
        self.assertFalse(admin.is_staff)
        self.assertEqual(admin.profile.role, UserProfile.ROLE_ADMIN)
        self.assertTrue(len(admin.profile.allowed_sections) > 0)
        self.assertTrue(user_can_access_country(admin, tenant))

    def test_two_tenants_are_isolated(self):
        call_command("create_tenant", name="A", slug="a",
                     admin_username="aa", admin_password="secret12345")
        call_command("create_tenant", name="B", slug="b",
                     admin_username="bb", admin_password="secret12345")
        admin_a = get_user_model().objects.get(username="aa")
        tenant_b = Country.objects.get(slug="b")
        # Админ арендатора A не имеет доступа к арендатору B
        self.assertFalse(user_can_access_country(admin_a, tenant_b))

    def test_duplicate_slug_rejected(self):
        call_command("create_tenant", name="A", slug="dup",
                     admin_username="a1", admin_password="secret12345")
        with self.assertRaises(CommandError):
            call_command("create_tenant", name="A2", slug="dup",
                         admin_username="a2", admin_password="secret12345")
