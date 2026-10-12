# -*- coding: utf-8 -*-
"""3.7 — модель называется Tenant, Country остаётся алиасом, таблица переименована."""
from django.test import TestCase

from foodcost import models as m


class RenameTenantTests(TestCase):
    def test_tenant_is_canonical(self):
        self.assertTrue(hasattr(m, "Tenant"))

    def test_country_alias_points_to_tenant(self):
        self.assertIs(m.Country, m.Tenant)

    def test_table_renamed(self):
        self.assertEqual(m.Tenant._meta.db_table, "foodcost_tenant")

    def test_crud_works_through_both_names(self):
        t = m.Tenant.objects.create(name="Чайхана", slug="chaihana")
        self.assertEqual(m.Country.objects.get(pk=t.pk), t)
