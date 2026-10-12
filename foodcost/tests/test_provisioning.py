# -*- coding: utf-8 -*-
"""3.6 — провижининг дефолтных справочников нового арендатора."""
from django.test import TestCase

from foodcost.models import Country, PaymentMethod, OrderSource
from foodcost.provisioning import provision_tenant_defaults


class ProvisioningTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")

    def test_seeds_payment_methods(self):
        provision_tenant_defaults(self.country)
        cash = PaymentMethod.objects.get(country=self.country, name="Наличные")
        self.assertTrue(cash.is_cash)
        self.assertTrue(PaymentMethod.objects.filter(
            country=self.country, name="Карта / терминал").exists())

    def test_seeds_order_sources_with_default_cash(self):
        provision_tenant_defaults(self.country)
        src = OrderSource.objects.get(country=self.country, name="Телефон")
        self.assertIsNotNone(src.default_payment_method)
        self.assertTrue(src.default_payment_method.is_cash)

    def test_idempotent(self):
        provision_tenant_defaults(self.country)
        provision_tenant_defaults(self.country)
        self.assertEqual(
            PaymentMethod.objects.filter(country=self.country, name="Наличные").count(), 1)
        self.assertEqual(
            OrderSource.objects.filter(country=self.country, name="Телефон").count(), 1)

    def test_only_affects_given_tenant(self):
        other = Country.objects.create(name="Плов", slug="plov")
        provision_tenant_defaults(self.country)
        self.assertEqual(PaymentMethod.objects.filter(country=other).count(), 0)
