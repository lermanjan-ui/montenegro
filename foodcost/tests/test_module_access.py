"""Эффективный набор модулей арендатора и правило доступа."""
from django.test import TestCase

from foodcost.models import Country
from foodcost.models_billing import Module, Plan, TenantSubscription
from foodcost.module_access import tenant_allowed_modules, tenant_has_module


class ModuleAccessTests(TestCase):
    def setUp(self):
        self.m_orders = Module.objects.create(code="orders", name="Заказы")
        self.m_fin = Module.objects.create(code="finance", name="Финансы", is_addon=True)
        self.m_inv = Module.objects.create(code="inventory", name="Инвентаризация")
        self.plan = Plan.objects.create(code="start", name="Старт")
        self.plan.modules.set([self.m_orders, self.m_inv])
        self.country = Country.objects.create(name="A", slug="a")

    def test_no_subscription_means_no_limits(self):
        self.assertIsNone(tenant_allowed_modules(self.country))
        self.assertTrue(tenant_has_module(self.country, "anything"))

    def test_effective_modules_plan_plus_extra_minus_disabled(self):
        sub = TenantSubscription.objects.create(country=self.country, plan=self.plan)
        sub.extra_modules.set([self.m_fin])          # доп. модуль сверх тарифа
        sub.disabled_modules.set([self.m_inv])       # точечно отключили
        codes = tenant_allowed_modules(self.country)
        self.assertEqual(codes, {"orders", "finance"})
        self.assertTrue(tenant_has_module(self.country, "orders"))
        self.assertTrue(tenant_has_module(self.country, "finance"))
        self.assertFalse(tenant_has_module(self.country, "inventory"))   # отключён
        self.assertFalse(tenant_has_module(self.country, "dishes"))      # не в тарифе

    def test_usable_status(self):
        sub = TenantSubscription.objects.create(country=self.country, plan=self.plan,
                                                status=TenantSubscription.STATUS_SUSPENDED)
        self.assertFalse(sub.is_usable())
