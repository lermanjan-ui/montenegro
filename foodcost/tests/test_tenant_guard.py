# -*- coding: utf-8 -*-
"""
Тесты применения тарифа «вглубь»:
  • middleware блокирует заход в раздел, которого нет у компании по тарифу;
  • суперюзер и компании без подписки — не ограничены;
  • служебные разделы (settings/users) тарифом не закрываются;
  • меню (context_processor) скрывает недоступные модули.
"""
from django.contrib.auth.models import AnonymousUser, User
from django.test import TestCase, RequestFactory

from foodcost.models import Country, UserProfile
from foodcost.models_billing import Module, Plan, TenantSubscription
from foodcost.tenant_guard import (
    TenantModuleMiddleware, section_for_segment, current_country,
)
from foodcost.context_processors import menu_context


def _mw():
    return TenantModuleMiddleware(lambda request: "PASSED")


class TenantGuardUnitTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        # тариф без finance
        m_orders = Module.objects.create(code="orders", name="Заказы")
        m_fin = Module.objects.create(code="finance", name="Финансы", is_addon=True)
        plan = Plan.objects.create(code="start", name="Старт")
        plan.modules.set([m_orders])
        TenantSubscription.objects.create(
            country=self.country, plan=plan,
            status=TenantSubscription.STATUS_ACTIVE)

        self.user = User.objects.create_user("cadmin", password="x")
        p = UserProfile.objects.create(
            user=self.user, role=UserProfile.ROLE_ADMIN,
            allowed_sections=["orders", "finance", "settings"])
        p.countries.set([self.country])
        self.superuser = User.objects.create_user("owner", password="x", is_superuser=True)

    def _req(self, path, user):
        r = self.rf.get(path)
        r.user = user
        return r

    def test_segment_mapping(self):
        self.assertEqual(section_for_segment("finance"), "finance")
        self.assertIsNone(section_for_segment("settings"))  # служебный — не мапится
        self.assertIsNone(section_for_segment("unknown-xyz"))

    def test_current_country(self):
        r = self._req("/c/chaihana/finance/expenses/", self.user)
        self.assertEqual(current_country(r).id, self.country.id)
        self.assertIsNone(current_country(self._req("/platform/", self.user)))

    def test_blocks_module_not_in_plan(self):
        mw = _mw()
        resp = mw(self._req("/c/chaihana/finance/expenses/", self.user))
        self.assertNotEqual(resp, "PASSED")        # перехвачено
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp["Location"], "/c/chaihana/")

    def test_allows_module_in_plan(self):
        mw = _mw()
        self.assertEqual(mw(self._req("/c/chaihana/orders/", self.user)), "PASSED")

    def test_superuser_not_restricted(self):
        mw = _mw()
        self.assertEqual(
            mw(self._req("/c/chaihana/finance/expenses/", self.superuser)), "PASSED")

    def test_core_section_not_blocked(self):
        mw = _mw()
        # settings не мапится → не блокируется тарифом
        self.assertEqual(mw(self._req("/c/chaihana/settings/", self.user)), "PASSED")

    def test_anonymous_not_touched(self):
        mw = _mw()
        self.assertEqual(
            mw(self._req("/c/chaihana/finance/expenses/", AnonymousUser())), "PASSED")


class NoSubscriptionTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.free = Country.objects.create(name="Free", slug="free")
        self.user = User.objects.create_user("u", password="x")
        p = UserProfile.objects.create(
            user=self.user, role=UserProfile.ROLE_ADMIN,
            allowed_sections=["finance"])
        p.countries.set([self.free])

    def test_no_subscription_not_restricted(self):
        mw = _mw()
        r = self.rf.get("/c/free/finance/expenses/")
        r.user = self.user
        self.assertEqual(mw(r), "PASSED")


class MenuContextTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        m_orders = Module.objects.create(code="orders", name="Заказы")
        m_settings = Module.objects.create(code="settings", name="Настройки")
        Module.objects.create(code="finance", name="Финансы")
        plan = Plan.objects.create(code="start", name="Старт")
        plan.modules.set([m_orders, m_settings])   # без finance
        TenantSubscription.objects.create(
            country=self.country, plan=plan,
            status=TenantSubscription.STATUS_ACTIVE)
        self.user = User.objects.create_user("cadmin", password="x")
        p = UserProfile.objects.create(
            user=self.user, role=UserProfile.ROLE_ADMIN,
            allowed_sections=["orders", "finance", "settings"])
        p.countries.set([self.country])

    def test_menu_hides_unpaid_module(self):
        r = self.rf.get("/c/chaihana/")
        r.user = self.user
        ctx = menu_context(r)
        self.assertTrue(ctx["can_menu_orders"])     # в тарифе
        self.assertFalse(ctx["can_menu_finance"])   # нет в тарифе → скрыт
        self.assertTrue(ctx["can_menu_settings"])   # служебный → всегда
