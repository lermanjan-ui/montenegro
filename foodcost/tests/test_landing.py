# -*- coding: utf-8 -*-
"""Тест публичного лендинга (/welcome/)."""
from django.test import TestCase

from foodcost.models_billing import Plan


class LandingTests(TestCase):
    def test_landing_public(self):
        Plan.objects.create(code="start", name="Старт", monthly_price=500000)
        resp = self.client.get("/welcome/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "FoodCost")
        self.assertContains(resp, "/signup/")   # CTA ведёт на регистрацию
        self.assertContains(resp, "Старт")       # тариф из БД
