"""Шаг 3.1 — ловушка на изоляцию арендаторов.

Закрепляет РЕАЛЬНЫЙ контракт изоляции, который уже есть в коде
(foodcost.views.get_country / user_can_access_country): пользователь одного
арендатора не должен получать доступ к данным другого. Эти тесты — страховка
на время рефакторинга под мультиарендность (Country→Tenant, центральный
скоупинг): если какой-то шаг сломает изоляцию, они покраснеют.

Заметка про super_admin / Django-суперюзера: СЕЙЧАС они видят всех арендаторов
(глобальный обход). Для продукта это поведение будем менять — тест
test_django_superuser_bypass_is_documented фиксирует текущее поведение, чтобы
смена была осознанной, а не случайной.
"""
from django.contrib.auth.models import User
from django.http import Http404
from django.test import TestCase

from foodcost.models import Country, OrderSource, UserProfile
from foodcost.views import get_country, user_can_access_country


class TenantIsolationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant_a = Country.objects.create(name="Арендатор A", slug="a")
        cls.tenant_b = Country.objects.create(name="Арендатор B", slug="b")

        # Пользователь, привязанный ТОЛЬКО к арендатору A
        cls.user_a = User.objects.create_user("user_a", password="x")
        prof_a = UserProfile.objects.create(user=cls.user_a, role=UserProfile.ROLE_ADMIN)
        prof_a.countries.add(cls.tenant_a)

        # Django-суперюзер (глобальный доступ — пока так)
        cls.super_user = User.objects.create_superuser("root", "", "x")

        # Пользователь без профиля вовсе
        cls.user_no_profile = User.objects.create_user("ghost", password="x")

        # Данные, скоупленные по арендатору
        OrderSource.objects.create(country=cls.tenant_a, name="Сайт A")
        OrderSource.objects.create(country=cls.tenant_b, name="Сайт B")

    # --- контракт входа (get_country) ---
    def test_user_can_open_own_tenant(self):
        self.assertEqual(get_country("a", self.user_a), self.tenant_a)

    def test_user_cannot_open_other_tenant(self):
        # Главная гарантия изоляции: чужой арендатор = 404, а не чужие данные.
        with self.assertRaises(Http404):
            get_country("b", self.user_a)

    def test_user_without_profile_is_blocked(self):
        with self.assertRaises(Http404):
            get_country("a", self.user_no_profile)

    def test_unknown_slug_is_404(self):
        with self.assertRaises(Http404):
            get_country("does-not-exist", self.user_a)

    # --- helper напрямую ---
    def test_access_helper_matrix(self):
        self.assertTrue(user_can_access_country(self.user_a, self.tenant_a))
        self.assertFalse(user_can_access_country(self.user_a, self.tenant_b))
        self.assertFalse(user_can_access_country(self.user_no_profile, self.tenant_a))

    # --- скоуп данных (эталон для 243 ручных .filter(country=...)) ---
    def test_country_scoped_queryset_does_not_bleed(self):
        a_sources = OrderSource.objects.filter(country=self.tenant_a)
        self.assertEqual(a_sources.count(), 1)
        self.assertEqual(a_sources.first().name, "Сайт A")
        # Ни одна чужая строка не попадает в выборку арендатора A
        self.assertFalse(a_sources.filter(country=self.tenant_b).exists())

    # --- текущее (временное) поведение суперюзера — зафиксировано осознанно ---
    def test_django_superuser_bypass_is_documented(self):
        # TODO(SaaS): в продукте «админ клиента» не должен видеть всех.
        self.assertEqual(get_country("b", self.super_user), self.tenant_b)
        self.assertTrue(user_can_access_country(self.super_user, self.tenant_a))
