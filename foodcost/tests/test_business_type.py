# -*- coding: utf-8 -*-
"""POS 0.1 — тип бизнеса на арендаторе."""
from django.test import TestCase
from foodcost.models import Tenant


class BusinessTypeTests(TestCase):
    def test_default_is_restaurant(self):
        t = Tenant.objects.create(name="Чайхана", slug="chaihana")
        self.assertEqual(t.business_type, Tenant.BUSINESS_RESTAURANT)
        self.assertTrue(t.is_restaurant)
        self.assertFalse(t.is_retail)

    def test_set_retail(self):
        t = Tenant.objects.create(name="Магазин", slug="shop",
                                  business_type=Tenant.BUSINESS_RETAIL)
        self.assertTrue(t.is_retail)
        self.assertFalse(t.is_services)

    def test_set_services(self):
        t = Tenant.objects.create(name="Салон", slug="salon",
                                  business_type=Tenant.BUSINESS_SERVICES)
        self.assertTrue(t.is_services)

    def test_choices_are_three(self):
        self.assertEqual(len(Tenant.BUSINESS_TYPE_CHOICES), 3)
