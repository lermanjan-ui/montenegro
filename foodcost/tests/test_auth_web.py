# -*- coding: utf-8 -*-
"""
Тесты веб-входа/регистрации.

Вход — по ПАРОЛЮ (e-mail / телефон / логин + пароль).
Восстановление — код (OTP) на e-mail/телефон → новый пароль.
Регистрация — задаёт пароль + OTP подтверждает e-mail/телефон; после неё у
компании есть пароль (можно сразу входить).

Код OTP достаётся НЕЗАВИСИМО от настроек: из session['otp_debug'] или из текста
отправленного письма (mail.outbox). Требует поле UserProfile.phone (+миграция).
"""
import re

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase

from foodcost.models import Country, UserProfile
from foodcost.models_billing import Plan, TenantSubscription


class _OtpMixin:
    def _otp_code(self):
        code = self.client.session.get("otp_debug") or ""
        if code:
            return code
        if mail.outbox:
            m = re.search(r"\d{4,8}", mail.outbox[-1].body)
            if m:
                return m.group(0)
        return ""


class SignupTests(_OtpMixin, TestCase):
    def setUp(self):
        Plan.objects.create(code="start", name="Старт")

    def _signup(self, channel="email", inn="123456789", email="a@b.uz",
                phone="+998901112233", name="Navruz", password="secret123"):
        return self.client.post("/signup/", {
            "company_name": name, "inn": inn, "phone": phone,
            "email": email, "channel": channel,
            "password": password, "password2": password})

    def test_signup_requires_valid_inn(self):
        resp = self._signup(inn="123")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "9 цифр")
        self.assertFalse(Country.objects.filter(name="Navruz").exists())

    def test_signup_requires_password(self):
        resp = self.client.post("/signup/", {
            "company_name": "Navruz", "inn": "123456789",
            "phone": "+998901112233", "email": "a@b.uz", "channel": "email",
            "password": "123", "password2": "123"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "не короче")
        self.assertFalse(Country.objects.filter(name="Navruz").exists())

    def test_signup_password_mismatch(self):
        resp = self.client.post("/signup/", {
            "company_name": "Navruz", "inn": "123456789",
            "phone": "+998901112233", "email": "a@b.uz", "channel": "email",
            "password": "secret123", "password2": "other123"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "не совпад")

    def test_full_signup_flow(self):
        r1 = self._signup()
        self.assertEqual(r1.status_code, 302)
        code = self._otp_code()
        self.assertTrue(code)
        r2 = self.client.post("/signup/verify/", {"code": code})
        self.assertEqual(r2.status_code, 302)

        country = Country.objects.get(name="Navruz")
        admin = User.objects.get(email="a@b.uz")
        self.assertFalse(admin.is_superuser)
        self.assertTrue(admin.has_usable_password())          # пароль задан при регистрации
        self.assertTrue(admin.check_password("secret123"))
        self.assertEqual(admin.profile.role, UserProfile.ROLE_ADMIN)
        self.assertEqual(admin.profile.phone, "+998901112233")
        self.assertEqual(country.company_profile.inn, "123456789")
        sub = TenantSubscription.objects.get(country=country)
        self.assertEqual(sub.status, TenantSubscription.STATUS_TRIAL)
        self.assertEqual(int(self.client.session["_auth_user_id"]), admin.id)

    def test_can_login_with_password_after_signup(self):
        self._signup()
        self.client.post("/signup/verify/", {"code": self._otp_code()})
        self.client.logout()
        r = self.client.post("/login/", {"identifier": "a@b.uz", "password": "secret123"})
        self.assertEqual(r.status_code, 302)
        admin = User.objects.get(email="a@b.uz")
        self.assertEqual(int(self.client.session["_auth_user_id"]), admin.id)

    def test_duplicate_inn_rejected(self):
        self._signup()
        self.client.post("/signup/verify/", {"code": self._otp_code()})
        self.client.logout()
        resp = self._signup(email="c@d.uz", phone="+998905556677")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "ИНН")

    def test_wrong_code_rejected(self):
        self._signup()
        resp = self.client.post("/signup/verify/", {"code": "00000"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Неверный код")
        self.assertFalse(Country.objects.filter(name="Navruz").exists())


class LoginPasswordTests(TestCase):
    """Вход по паролю: e-mail / телефон / логин."""
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.user = User.objects.create_user(
            "admin1", email="me@shop.uz", password="secret123")
        p = UserProfile.objects.create(
            user=self.user, role=UserProfile.ROLE_ADMIN, phone="+998901234567")
        p.countries.set([self.country])

    def test_login_by_email(self):
        r = self.client.post("/login/", {"identifier": "me@shop.uz", "password": "secret123"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.id)

    def test_login_by_phone(self):
        r = self.client.post("/login/", {"identifier": "+998 90 123 45 67", "password": "secret123"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.id)

    def test_login_by_username(self):
        r = self.client.post("/login/", {"identifier": "admin1", "password": "secret123"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.id)

    def test_wrong_password(self):
        r = self.client.post("/login/", {"identifier": "me@shop.uz", "password": "nope"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Неверные данные")
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_unknown_identifier(self):
        r = self.client.post("/login/", {"identifier": "nobody@x.uz", "password": "x"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Неверные данные")

    def test_password_login_page_still_available(self):
        self.assertEqual(self.client.get("/login/password/").status_code, 200)


class ForgotPasswordTests(_OtpMixin, TestCase):
    """Восстановление пароля по коду (e-mail/телефон → новый пароль)."""
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.user = User.objects.create_user(
            "admin2", email="boss@shop.uz", password="oldpass123")
        p = UserProfile.objects.create(
            user=self.user, role=UserProfile.ROLE_ADMIN, phone="+998900001122")
        p.countries.set([self.country])

    def test_reset_by_email(self):
        r1 = self.client.post("/login/forgot/", {"identifier": "boss@shop.uz"})
        self.assertEqual(r1.status_code, 302)
        code = self._otp_code()
        self.assertTrue(code)
        r2 = self.client.post("/login/forgot/verify/", {"code": code, "password": "newpass123"})
        self.assertEqual(r2.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newpass123"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.id)

    def test_reset_by_phone(self):
        r1 = self.client.post("/login/forgot/", {"identifier": "+998900001122"})
        self.assertEqual(r1.status_code, 302)
        code = self._otp_code()
        self.assertTrue(code)
        r2 = self.client.post("/login/forgot/verify/", {"code": code, "password": "newpass123"})
        self.assertEqual(r2.status_code, 302)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("newpass123"))

    def test_forgot_unknown_identifier(self):
        r = self.client.post("/login/forgot/", {"identifier": "ghost@x.uz"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "не найден")

    def test_forgot_wrong_code(self):
        self.client.post("/login/forgot/", {"identifier": "boss@shop.uz"})
        r = self.client.post("/login/forgot/verify/", {"code": "00000", "password": "newpass123"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Неверный код")

    def test_forgot_short_password_rejected(self):
        self.client.post("/login/forgot/", {"identifier": "boss@shop.uz"})
        code = self._otp_code()
        r = self.client.post("/login/forgot/verify/", {"code": code, "password": "123"})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "не короче")
