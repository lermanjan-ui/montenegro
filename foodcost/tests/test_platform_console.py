# -*- coding: utf-8 -*-
"""
Тесты консоли платформенного супер-админа.
Проверяют доступ (кто видит консоль), назначение тарифа, переключение
модулей, смену статуса и создание клиента.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from foodcost.models import Country, Location, UserProfile
from foodcost.models_billing import Module, Plan, TenantSubscription


class PlatformConsoleTests(TestCase):
    def setUp(self):
        # Платформенный супер-админ (Django superuser)
        self.owner = User.objects.create_user("owner", password="x", is_superuser=True)

        # Клиент
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        Location.objects.create(country=self.country, name="Филиал 1")

        # Модули и тариф
        self.m_orders = Module.objects.create(code="orders", name="Заказы")
        self.m_finance = Module.objects.create(code="finance", name="Финансы", is_addon=True)
        self.m_inventory = Module.objects.create(code="inventory", name="Инвентаризация")
        self.plan = Plan.objects.create(code="start", name="Старт")
        self.plan.modules.set([self.m_orders, self.m_inventory])

    # ---- доступ -------------------------------------------------------------
    def test_company_admin_cannot_access(self):
        user = User.objects.create_user("companyadmin", password="x")
        prof = UserProfile.objects.create(user=user, role=UserProfile.ROLE_ADMIN)
        prof.countries.set([self.country])
        self.client.force_login(user)
        resp = self.client.get(reverse("platform_dashboard"))
        self.assertEqual(resp.status_code, 403)

    def test_platform_superadmin_profile_can_access(self):
        user = User.objects.create_user("platform", password="x")
        UserProfile.objects.create(user=user, role=UserProfile.ROLE_SUPER_ADMIN)
        self.client.force_login(user)
        resp = self.client.get(reverse("platform_dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Чайхана")

    def test_anonymous_redirected_to_login(self):
        resp = self.client.get(reverse("platform_dashboard"))
        self.assertIn(resp.status_code, (301, 302))

    # ---- тариф --------------------------------------------------------------
    def test_assign_plan_creates_subscription(self):
        self.client.force_login(self.owner)
        url = reverse("platform_client_detail", args=[self.country.slug])
        self.client.post(url, {"action": "assign_plan", "plan_id": self.plan.id})
        sub = TenantSubscription.objects.get(country=self.country)
        self.assertEqual(sub.plan_id, self.plan.id)
        self.assertEqual(sub.effective_module_codes(), {"orders", "inventory"})

    # ---- модули -------------------------------------------------------------
    def test_toggle_addon_module(self):
        self.client.force_login(self.owner)
        sub = TenantSubscription.objects.create(country=self.country, plan=self.plan)
        url = reverse("platform_client_detail", args=[self.country.slug])
        # подключить доп finance
        self.client.post(url, {"action": "toggle_module", "code": "finance"})
        sub.refresh_from_db()
        self.assertIn("finance", sub.effective_module_codes())
        # убрать обратно
        self.client.post(url, {"action": "toggle_module", "code": "finance"})
        sub.refresh_from_db()
        self.assertNotIn("finance", sub.effective_module_codes())

    def test_toggle_plan_module_disables_it(self):
        self.client.force_login(self.owner)
        sub = TenantSubscription.objects.create(country=self.country, plan=self.plan)
        url = reverse("platform_client_detail", args=[self.country.slug])
        self.client.post(url, {"action": "toggle_module", "code": "inventory"})
        sub.refresh_from_db()
        self.assertNotIn("inventory", sub.effective_module_codes())
        self.assertIn("orders", sub.effective_module_codes())

    # ---- статус -------------------------------------------------------------
    def test_set_status_suspend(self):
        self.client.force_login(self.owner)
        TenantSubscription.objects.create(country=self.country, plan=self.plan)
        url = reverse("platform_client_detail", args=[self.country.slug])
        self.client.post(url, {"action": "set_status", "status": "suspended"})
        sub = TenantSubscription.objects.get(country=self.country)
        self.assertEqual(sub.status, "suspended")
        self.assertFalse(sub.is_usable())

    # ---- создание клиента ---------------------------------------------------
    def test_create_client(self):
        self.client.force_login(self.owner)
        resp = self.client.post(reverse("platform_client_create"), {
            "name": "Новый Ресторан",
            "slug": "newrest",
            "location_name": "Главный",
            "admin_login": "rest_admin",
            "admin_password": "secret123",
            "plan_id": self.plan.id,
        })
        self.assertIn(resp.status_code, (301, 302))
        country = Country.objects.get(slug="newrest")
        self.assertTrue(Location.objects.filter(country=country).exists())
        admin = User.objects.get(username="rest_admin")
        self.assertFalse(admin.is_superuser)   # админ клиента НЕ Django-суперюзер
        self.assertEqual(admin.profile.role, UserProfile.ROLE_ADMIN)
        self.assertTrue(admin.profile.countries.filter(id=country.id).exists())
        self.assertEqual(country.subscription.plan_id, self.plan.id)
