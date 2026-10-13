# -*- coding: utf-8 -*-
"""POS 0.2c — опросник модулей по типу бизнеса."""
import re
from django.core import mail
from django.test import TestCase
from foodcost.models import Tenant


class _Otp:
    def _code(self):
        c = self.client.session.get("otp_debug") or ""
        if c:
            return c
        if mail.outbox:
            m = re.search(r"\d{4,8}", mail.outbox[-1].body)
            if m:
                return m.group(0)
        return ""


class OnboardingQuestionsTests(_Otp, TestCase):
    def test_questions_requires_type(self):
        r = self.client.get("/start/questions/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/start/", r["Location"])

    def test_start_redirects_to_questions(self):
        r = self.client.post("/start/", {"business_type": "restaurant"})
        self.assertEqual(r.status_code, 302)
        self.assertIn("/start/questions/", r["Location"])

    def test_questions_page_renders_for_type(self):
        self.client.post("/start/", {"business_type": "retail"})
        r = self.client.get("/start/questions/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "warehouse")  # ключ модуля магазина

    def test_selected_modules_saved_to_funnel_and_tenant(self):
        self.client.post("/start/", {"business_type": "restaurant"})
        self.client.post("/start/questions/", {"modules": ["hall", "kds", "bogus"]})
        self.assertEqual(
            set(self.client.session["signup_funnel"]["modules"]), {"hall", "kds"})
        self.client.post("/signup/", {
            "company_name": "Rest", "inn": "123456789",
            "phone": "+998901112233", "email": "a@b.uz", "channel": "email",
            "password": "secret123", "password2": "secret123"})
        self.client.post("/signup/verify/", {"code": self._code()})
        t = Tenant.objects.get(name="Rest")
        self.assertEqual(set(t.onboarding_modules), {"hall", "kds"})
