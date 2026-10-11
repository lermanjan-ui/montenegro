# -*- coding: utf-8 -*-
"""
Тесты роли «платформенный менеджер»:
  • менеджер видит консоль клиентов, но НЕ видит страницу «Команда»;
  • менеджер не может создать менеджера;
  • только владелец (супер-админ) создаёт/снимает менеджеров;
  • созданный менеджер — не Django-суперюзер.
"""
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from foodcost.models import UserProfile


class PlatformTeamTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", password="x", is_superuser=True)
        # менеджер
        self.manager_user = User.objects.create_user("manager", password="x")
        UserProfile.objects.create(
            user=self.manager_user, role=UserProfile.ROLE_PLATFORM_MANAGER)

    # ---- доступ к консоли ---------------------------------------------------
    def test_manager_sees_clients(self):
        self.client.force_login(self.manager_user)
        resp = self.client.get(reverse("platform_dashboard"))
        self.assertEqual(resp.status_code, 200)

    def test_manager_cannot_open_team(self):
        self.client.force_login(self.manager_user)
        resp = self.client.get(reverse("platform_team"))
        self.assertEqual(resp.status_code, 403)

    def test_owner_sees_team(self):
        self.client.force_login(self.owner)
        resp = self.client.get(reverse("platform_team"))
        self.assertEqual(resp.status_code, 200)

    # ---- создание менеджера -------------------------------------------------
    def test_owner_creates_manager(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("platform_team"), {
            "action": "create_manager",
            "login": "newmgr",
            "password": "secret123",
        })
        u = User.objects.get(username="newmgr")
        self.assertFalse(u.is_superuser)
        self.assertEqual(u.profile.role, UserProfile.ROLE_PLATFORM_MANAGER)

    def test_manager_cannot_create_manager(self):
        self.client.force_login(self.manager_user)
        resp = self.client.post(reverse("platform_team"), {
            "action": "create_manager",
            "login": "sneaky",
            "password": "secret123",
        })
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(User.objects.filter(username="sneaky").exists())

    # ---- снятие менеджера ---------------------------------------------------
    def test_owner_removes_manager(self):
        self.client.force_login(self.owner)
        self.client.post(
            reverse("platform_team_remove", args=[self.manager_user.id]))
        self.manager_user.profile.refresh_from_db()
        self.assertEqual(self.manager_user.profile.role, UserProfile.ROLE_VIEWER)
