# -*- coding: utf-8 -*-
"""3.6 (добор) — клиент, созданный в консоли платформы, получает дефолты."""
from django.contrib.auth.models import User
from django.test import TestCase

from foodcost.models import Country, PaymentMethod, OrderSource


class PlatformProvisioningTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", password="x", is_superuser=True)
        self.client.force_login(self.owner)

    def test_create_client_seeds_defaults(self):
        resp = self.client.post("/platform/clients/new/", {
            "name": "Новый Клиент", "slug": "novyi-klient",
            "admin_login": "admin_nk", "admin_password": "secret123",
            "location_name": "Основной филиал"})
        self.assertEqual(resp.status_code, 302)
        country = Country.objects.get(slug="novyi-klient")
        self.assertTrue(PaymentMethod.objects.filter(
            country=country, name="Наличные", is_cash=True).exists())
        self.assertTrue(OrderSource.objects.filter(
            country=country, name="Телефон").exists())
