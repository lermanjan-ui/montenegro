# -*- coding: utf-8 -*-
"""
Тесты личного кабинета (/c/<slug>/account/):
доступ своего арендатора, смена пароля (успех/ошибка), изоляция.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from foodcost.models import Country, UserProfile


class MyAccountTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.url = f"/c/{self.country.slug}/account/"
        self.user = User.objects.create_user("waiter", password="oldpass123")
        p = UserProfile.objects.create(
            user=self.user, role=UserProfile.ROLE_VIEWER,
            allowed_sections=["orders", "customers"])
        p.countries.set([self.country])

    def test_opens_for_own_tenant(self):
        self.client.force_login(self.user)
        resp = self.client.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "waiter")

    def test_change_password_success(self):
        self.client.force_login(self.user)
        resp = self.client.post(self.url, {
            "action": "change_password",
            "old_password": "oldpass123",
            "new_password": "brandnew123",
            "new_password2": "brandnew123",
        })
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("brandnew123"))

    def test_change_password_wrong_old(self):
        self.client.force_login(self.user)
        self.client.post(self.url, {
            "action": "change_password",
            "old_password": "WRONG",
            "new_password": "brandnew123",
            "new_password2": "brandnew123",
        })
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("oldpass123"))  # не изменён

    def test_change_password_mismatch(self):
        self.client.force_login(self.user)
        self.client.post(self.url, {
            "action": "change_password",
            "old_password": "oldpass123",
            "new_password": "brandnew123",
            "new_password2": "different999",
        })
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("oldpass123"))

    def test_other_tenant_forbidden(self):
        other = Country.objects.create(name="Другой", slug="other")
        self.client.force_login(self.user)
        resp = self.client.get(f"/c/{other.slug}/account/")
        self.assertEqual(resp.status_code, 404)
