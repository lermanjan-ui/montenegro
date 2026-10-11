# -*- coding: utf-8 -*-
"""
Тесты задания логина/пароля в личном кабинете.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from foodcost.models import Country, UserProfile


class AccountCredentialsTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.url = f"/c/{self.country.slug}/account/"
        # аккаунт «только по коду» — без пароля
        self.user = User.objects.create_user("auto123", email="a@b.uz")
        self.user.set_unusable_password()
        self.user.save()
        p = UserProfile.objects.create(user=self.user, role=UserProfile.ROLE_ADMIN)
        p.countries.set([self.country])

    def test_set_login(self):
        self.client.force_login(self.user)
        self.client.post(self.url, {"action": "set_login", "login": "myshop"})
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "myshop")

    def test_duplicate_login_rejected(self):
        User.objects.create_user("taken", password="x")
        self.client.force_login(self.user)
        resp = self.client.post(self.url, {"action": "set_login", "login": "taken"})
        self.assertContains(resp, "занят")
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "auto123")

    def test_code_only_user_sets_password_without_old(self):
        self.client.force_login(self.user)
        self.client.post(self.url, {
            "action": "change_password",
            "new_password": "newpass123", "new_password2": "newpass123"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.has_usable_password())
        self.assertTrue(self.user.check_password("newpass123"))

    def test_user_with_password_needs_old(self):
        self.user.set_password("oldpass123")
        self.user.save()
        self.client.force_login(self.user)
        self.client.post(self.url, {
            "action": "change_password", "old_password": "WRONG",
            "new_password": "newpass123", "new_password2": "newpass123"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("oldpass123"))  # не изменён
