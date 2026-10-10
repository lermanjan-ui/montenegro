"""Шаг 3.3 — публичный резолв арендатора без «permissive fallback».

Закрепляет фикс: неизвестный слаг на публичном API даёт 404, а НЕ подставляет
первого попавшегося арендатора с «uzbek» в названии. Штатный путь (точный слаг)
работает как раньше.
"""
from django.test import TestCase, RequestFactory

from foodcost.models import Country
from foodcost import public_api


class PublicCountryResolutionTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def test_known_slug_resolves(self):
        c = Country.objects.create(name="Узбекистан", slug="uzbekistan")
        country, err = public_api.get_public_country(
            self.rf.get("/api/public/x/", {"country_slug": "uzbekistan"})
        )
        self.assertIsNone(err)
        self.assertEqual(country, c)

    def test_default_slug_used_when_absent(self):
        c = Country.objects.create(name="Узбекистан", slug="uzbekistan")
        country, err = public_api.get_public_country(self.rf.get("/api/public/x/"))
        self.assertIsNone(err)
        self.assertEqual(country, c)

    def test_unknown_slug_is_404_no_fuzzy_fallback(self):
        # Арендатор с "uzbek" в названии существует, но слаг у него ДРУГОЙ —
        # он НЕ должен подставляться на неизвестный слаг.
        Country.objects.create(name="Узбекистан", slug="tenant-x")
        country, err = public_api.get_public_country(
            self.rf.get("/api/public/x/", {"country_slug": "no-such-slug"})
        )
        self.assertIsNone(country)
        self.assertIsNotNone(err)
