# -*- coding: utf-8 -*-
"""POS 0.2b — выбор типа бизнеса как пред-регистрационный шаг (/start/)."""
import re
from django.core import mail
from django.test import TestCase

from foodcost.models import Tenant, OrderSource


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


class BusinessTypeFunnelTests(_Otp, TestCase):
    def test_start_page_renders(self):
        r = self.client.get("/start/")
        self.assertEqual(r.status_code, 200)

    def test_valid_choice_sets_session_and_redirects(self):
        r = self.client.post("/start/", {"business_type": "retail"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.session["signup_funnel"]["business_type"], "retail")

    def test_invalid_choice_rejected(self):
        r = self.client.post("/start/", {"business_type": "bogus"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("signup_funnel", self.client.session)

    def test_signup_get_without_funnel_redirects_to_start(self):
        r = self.client.get("/signup/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/start/", r["Location"])

    def test_full_flow_applies_business_type_and_defaults(self):
        self.client.post("/start/", {"business_type": "retail"})
        self.client.post("/signup/", {
            "company_name": "Shop", "inn": "123456789",
            "phone": "+998901112233", "email": "a@b.uz", "channel": "email",
            "password": "secret123", "password2": "secret123"})
        self.client.post("/signup/verify/", {"code": self._code()})
        t = Tenant.objects.get(name="Shop")
        self.assertEqual(t.business_type, Tenant.BUSINESS_RETAIL)
        self.assertTrue(OrderSource.objects.filter(country=t, name="Касса").exists())
        self.assertFalse(OrderSource.objects.filter(country=t, name="Зал").exists())
