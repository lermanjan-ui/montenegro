# -*- coding: utf-8 -*-
"""
Тесты кабинета «Сотрудники компании».
Ключевое: админ клиента выдаёт доступ ТОЛЬКО к разделам, доступным компании
по тарифу. Плюс доступ по разделу, запрет для просмотра, изоляция.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from foodcost.models import Country, UserProfile
from foodcost.models_billing import Module, Plan, TenantSubscription


class CompanyTeamTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.url = f"/c/{self.country.slug}/team/"

        # тариф: только orders + customers + users (finance НЕ входит)
        m_orders = Module.objects.create(code="orders", name="Заказы")
        m_customers = Module.objects.create(code="customers", name="Клиенты")
        m_users = Module.objects.create(code="users", name="Пользователи")
        Module.objects.create(code="finance", name="Финансы", is_addon=True)
        plan = Plan.objects.create(code="start", name="Старт")
        plan.modules.set([m_orders, m_customers, m_users])
        TenantSubscription.objects.create(
            country=self.country, plan=plan,
            status=TenantSubscription.STATUS_ACTIVE)

        # админ компании
        self.admin = User.objects.create_user("cadmin", password="x")
        p = UserProfile.objects.create(
            user=self.admin, role=UserProfile.ROLE_ADMIN,
            allowed_sections=["users", "orders", "customers"])
        p.countries.set([self.country])

    def test_admin_can_open(self):
        self.client.force_login(self.admin)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)

    def test_user_without_users_section_forbidden(self):
        u = User.objects.create_user("plain", password="x")
        p = UserProfile.objects.create(
            user=u, role=UserProfile.ROLE_VIEWER, allowed_sections=[])
        p.countries.set([self.country])
        self.client.force_login(u)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_create_employee_is_module_bounded(self):
        self.client.force_login(self.admin)
        self.client.post(self.url, {
            "action": "create_employee",
            "username": "waiter",
            "password": "secret123",
            "role": "viewer",
            # пытаемся выдать finance, которого НЕТ у компании по тарифу
            "allowed_sections": ["orders", "customers", "finance"],
        })
        emp = User.objects.get(username="waiter")
        granted = set(emp.profile.allowed_sections)
        self.assertEqual(granted, {"orders", "customers"})  # finance отрезан
        self.assertNotIn("finance", granted)

    def test_viewer_cannot_create(self):
        # раздел «users» есть, но прав на правку нет
        u = User.objects.create_user("ro", password="x")
        p = UserProfile.objects.create(
            user=u, role=UserProfile.ROLE_VIEWER, allowed_sections=["users"])
        p.countries.set([self.country])
        self.client.force_login(u)
        resp = self.client.post(self.url, {
            "action": "create_employee", "username": "x1",
            "password": "x", "role": "viewer", "allowed_sections": ["orders"],
        })
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(User.objects.filter(username="x1").exists())

    def test_remove_employee_detaches(self):
        emp_user = User.objects.create_user("waiter2", password="x")
        ep = UserProfile.objects.create(
            user=emp_user, role=UserProfile.ROLE_VIEWER, allowed_sections=["orders"])
        ep.countries.set([self.country])
        self.client.force_login(self.admin)
        self.client.post(self.url, {
            "action": "remove_employee", "user_id": emp_user.id})
        ep.refresh_from_db()
        self.assertFalse(ep.countries.filter(id=self.country.id).exists())

    def test_isolation_other_tenant(self):
        other = Country.objects.create(name="Другой", slug="other")
        u = User.objects.create_user("stranger", password="x")
        p = UserProfile.objects.create(
            user=u, role=UserProfile.ROLE_ADMIN, allowed_sections=["users"])
        p.countries.set([other])
        self.client.force_login(u)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_no_subscription_all_sections_available(self):
        # компания без подписки — доступны все разделы (back-compat)
        free = Country.objects.create(name="Free", slug="free")
        admin = User.objects.create_user("freeadmin", password="x")
        p = UserProfile.objects.create(
            user=admin, role=UserProfile.ROLE_ADMIN, allowed_sections=["users"])
        p.countries.set([free])
        self.client.force_login(admin)
        self.client.post(f"/c/{free.slug}/team/", {
            "action": "create_employee", "username": "fw",
            "password": "x", "role": "viewer",
            "allowed_sections": ["orders", "finance"],  # оба пройдут
        })
        emp = User.objects.get(username="fw")
        self.assertEqual(set(emp.profile.allowed_sections), {"orders", "finance"})
