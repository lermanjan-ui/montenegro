# -*- coding: utf-8 -*-
"""Шаг 3.5 — ловушка: админ арендатора НЕ Django-суперюзер.

Закрепляет контракт: права «админа клиента» идут от РОЛИ (UserProfile.ROLE_ADMIN),
а не от is_superuser; доступ при этом ограничен своими арендаторами. is_superuser
остаётся только шорткатом платформенного супер-админа.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from foodcost.models import Country, UserProfile
from foodcost.views import (
    user_can_edit, user_can_access_country, user_can_access_section,
)


class RoleNotSuperuserTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.other = Country.objects.create(name="Плов", slug="plov")
        self.admin = User.objects.create_user("cadmin", password="x")  # is_superuser=False
        self.profile = UserProfile.objects.create(
            user=self.admin, role=UserProfile.ROLE_ADMIN)
        self.profile.countries.set([self.country])

    def test_client_admin_is_not_django_superuser(self):
        self.assertFalse(self.admin.is_superuser)

    def test_admin_can_edit_from_role_not_superuser(self):
        # Права на редактирование даёт роль, без Django-суперюзера.
        self.assertTrue(user_can_edit(self.admin))

    def test_admin_access_scoped_to_own_tenant(self):
        self.assertTrue(user_can_access_country(self.admin, self.country))
        self.assertFalse(user_can_access_country(self.admin, self.other))
