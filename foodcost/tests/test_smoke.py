"""Базовая страховка: система проходит системные проверки, миграции
накатываются (факт создания тест-БД = прогон всех миграций), ORM живой."""
from django.core.management import call_command
from django.test import TestCase, SimpleTestCase

from foodcost.models import Country


class SystemCheckTests(SimpleTestCase):
    def test_django_check_passes(self):
        # Падает, если в моделях/настройках есть ошибки системных проверок.
        call_command("check")


class OrmSmokeTests(TestCase):
    def test_country_crud(self):
        c = Country.objects.create(name="Узбекистан", slug="uz")
        self.assertEqual(Country.objects.get(pk=c.pk).slug, "uz")
        self.assertEqual(Country.objects.count(), 1)
