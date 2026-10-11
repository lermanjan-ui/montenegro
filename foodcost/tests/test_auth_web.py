# -*- coding: utf-8 -*-
"""
Тесты веб-входа/регистрации по коду (OTP).
Код в тестах не отправляется (каналы не настроены) → возвращается для показа,
лежит в session['otp_debug'] — им и подтверждаем.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from foodcost.models import Country, UserProfile
from foodcost.models_billing import Plan, TenantSubscription


class SignupOtpTests(TestCase):
    def setUp(self):
        Plan.objects.create(code="start", name="Старт")

    def _signup(self, channel="email", inn="123456789", email="a@b.uz",
                phone="+998901112233", name="Navruz"):
        return self.client.post("/signup/", {
            "company_name": name, "inn": inn, "phone": phone,
            "email": email, "channel": channel})

    def test_signup_requires_valid_inn(self):
        resp = self._signup(inn="123")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "9 цифр")
        self.assertFalse(Country.objects.filter(name="Navruz").exists())

    def test_full_signup_flow(self):
        r1 = self._signup()
        self.assertEqual(r1.status_code, 302)
        code = self.client.session["otp_debug"]
        self.assertTrue(code)
        r2 = self.client.post("/signup/verify/", {"code": code})
        self.assertEqual(r2.status_code, 302)

        country = Country.objects.get(name="Navruz")
        admin = User.objects.get(email="a@b.uz")
        self.assertFalse(admin.is_superuser)
        self.assertFalse(admin.has_usable_password())     # вход только по коду
        self.assertEqual(admin.profile.role, UserProfile.ROLE_ADMIN)
        self.assertEqual(admin.profile.phone, "+998901112233")
        self.assertEqual(country.company_profile.inn, "123456789")
        sub = TenantSubscription.objects.get(country=country)
        self.assertEqual(sub.status, TenantSubscription.STATUS_TRIAL)
        # залогинен
        self.assertEqual(int(self.client.session["_auth_user_id"]), admin.id)

    def test_duplicate_inn_rejected(self):
        self._signup()
        code = self.client.session["otp_debug"]
        self.client.post("/signup/verify/", {"code": code})
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


class LoginOtpTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.user = User.objects.create_user("admin1", email="me@shop.uz")
        self.user.set_unusable_password()
        self.user.save()
        p = UserProfile.objects.create(
            user=self.user, role=UserProfile.ROLE_ADMIN, phone="+998901234567")
        p.countries.set([self.country])

    def test_login_by_email(self):
        r1 = self.client.post("/login/", {"identifier": "me@shop.uz"})
        self.assertEqual(r1.status_code, 302)
        code = self.client.session["otp_debug"]
        r2 = self.client.post("/login/verify/", {"code": code})
        self.assertEqual(r2.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.id)

    def test_login_by_phone(self):
        r1 = self.client.post("/login/", {"identifier": "+998 90 123 45 67"})
        self.assertEqual(r1.status_code, 302)
        code = self.client.session["otp_debug"]
        r2 = self.client.post("/login/verify/", {"code": code})
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.id)

    def test_unknown_identifier(self):
        resp = self.client.post("/login/", {"identifier": "nobody@x.uz"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "не найден")

    def test_password_login_still_available(self):
        self.assertEqual(self.client.get("/login/password/").status_code, 200)
