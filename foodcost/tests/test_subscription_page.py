# -*- coding: utf-8 -*-
"""
Тесты клиентской страницы «Моя подписка» (/c/<slug>/subscription/).
"""
from django.contrib.auth.models import User
from django.test import TestCase

from foodcost.models import Country, UserProfile
from foodcost.models_billing import Module, Plan, TenantSubscription


class SubscriptionPageTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.url = f"/c/{self.country.slug}/subscription/"
        m = Module.objects.create(code="orders", name="Заказы")
        plan = Plan.objects.create(code="start", name="Старт")
        plan.modules.set([m])
        TenantSubscription.objects.create(
            country=self.country, plan=plan,
            status=TenantSubscription.STATUS_ACTIVE)

        self.admin = User.objects.create_user("cadmin", password="x")
        p = UserProfile.objects.create(
            user=self.admin, role=UserProfile.ROLE_ADMIN,
            allowed_sections=["settings"])
        p.countries.set([self.country])

    def test_admin_sees_plan(self):
        self.client.force_login(self.admin)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Старт")
        self.assertContains(resp, "Заказы")

    def test_without_settings_forbidden(self):
        u = User.objects.create_user("plain", password="x")
        p = UserProfile.objects.create(
            user=u, role=UserProfile.ROLE_VIEWER, allowed_sections=[])
        p.countries.set([self.country])
        self.client.force_login(u)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_isolation(self):
        other = Country.objects.create(name="Другой", slug="other")
        u = User.objects.create_user("stranger", password="x")
        p = UserProfile.objects.create(
            user=u, role=UserProfile.ROLE_ADMIN, allowed_sections=["settings"])
        p.countries.set([other])
        self.client.force_login(u)
        self.assertEqual(self.client.get(self.url).status_code, 404)
