# -*- coding: utf-8 -*-
"""Шаг 3.3 (добор) — POST-резолвер арендатора без «permissive fallback».

`_get_country_from_payload` читает country_slug из тела запроса (POST/JSON).
Закрепляем тот же контракт, что и для get_public_country: неизвестный слаг → 404,
без подстановки чужого арендатора. Дополняет test_public_country.py (там покрыт
GET-резолвер).
"""
from django.test import TestCase

from foodcost.models import Country
from foodcost import public_api


class PayloadCountryResolutionTests(TestCase):
    def test_known_slug_resolves(self):
        c = Country.objects.create(name="Узбекистан", slug="uzbekistan")
        country, err = public_api._get_country_from_payload({"country_slug": "uzbekistan"})
        self.assertIsNone(err)
        self.assertEqual(country, c)

    def test_default_slug_used_when_absent(self):
        c = Country.objects.create(name="Узбекистан", slug="uzbekistan")
        country, err = public_api._get_country_from_payload({})
        self.assertIsNone(err)
        self.assertEqual(country, c)

    def test_unknown_slug_is_404_no_fuzzy_fallback(self):
        # Арендатор с "uzbek" в названии есть, но слаг другой — не подставляется.
        Country.objects.create(name="Узбекистан", slug="tenant-x")
        country, err = public_api._get_country_from_payload({"country_slug": "no-such-slug"})
        self.assertIsNone(country)
        self.assertIsNotNone(err)
