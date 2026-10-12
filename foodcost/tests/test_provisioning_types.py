# -*- coding: utf-8 -*-
"""POS 0.2 — дефолтные справочники зависят от типа бизнеса."""
from django.test import TestCase
from foodcost.models import Tenant, PaymentMethod, OrderSource
from foodcost.provisioning import provision_tenant_defaults


class ProvisioningByTypeTests(TestCase):
    def _srcs(self, t):
        return set(OrderSource.objects.filter(country=t).values_list("name", flat=True))

    def test_restaurant_sources(self):
        t = Tenant.objects.create(name="Чайхана", slug="chaihana",
                                  business_type=Tenant.BUSINESS_RESTAURANT)
        provision_tenant_defaults(t)
        self.assertEqual(self._srcs(t), {"Зал", "Телефон", "Доставка", "Самовывоз"})

    def test_retail_sources(self):
        t = Tenant.objects.create(name="Магазин", slug="shop",
                                  business_type=Tenant.BUSINESS_RETAIL)
        provision_tenant_defaults(t)
        self.assertEqual(self._srcs(t), {"Касса"})

    def test_services_sources_and_transfer(self):
        t = Tenant.objects.create(name="Салон", slug="salon",
                                  business_type=Tenant.BUSINESS_SERVICES)
        provision_tenant_defaults(t)
        self.assertEqual(self._srcs(t), {"Запись", "На месте"})
        self.assertTrue(PaymentMethod.objects.filter(country=t, name="Перевод").exists())

    def test_business_type_argument_overrides(self):
        t = Tenant.objects.create(name="X", slug="x")  # default restaurant
        provision_tenant_defaults(t, business_type=Tenant.BUSINESS_RETAIL)
        self.assertEqual(self._srcs(t), {"Касса"})

    def test_idempotent(self):
        t = Tenant.objects.create(name="Y", slug="y",
                                  business_type=Tenant.BUSINESS_RETAIL)
        provision_tenant_defaults(t)
        provision_tenant_defaults(t)
        self.assertEqual(OrderSource.objects.filter(country=t, name="Касса").count(), 1)
