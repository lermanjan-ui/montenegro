"""Шаг 3.5 — роли вместо Django-суперюзера.
Админ клиента (роль admin) и tenant super_admin получают право на
admin-действия; viewer/kitchen/без профиля — нет. Django-суперюзер — да."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from foodcost.models import UserProfile
from foodcost.views import user_can_edit


class RoleGatingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        U = get_user_model()
        cls.superuser = U.objects.create_superuser("root", "", "x")
        cls.admin = U.objects.create_user("adm", password="x")
        UserProfile.objects.create(user=cls.admin, role=UserProfile.ROLE_ADMIN)
        cls.super_admin = U.objects.create_user("sa", password="x")
        UserProfile.objects.create(user=cls.super_admin, role=UserProfile.ROLE_SUPER_ADMIN)
        cls.viewer = U.objects.create_user("vw", password="x")
        UserProfile.objects.create(user=cls.viewer, role=UserProfile.ROLE_VIEWER)
        cls.kitchen = U.objects.create_user("kt", password="x")
        UserProfile.objects.create(user=cls.kitchen, role=UserProfile.ROLE_KITCHEN_STAFF)
        cls.noprofile = U.objects.create_user("np", password="x")

    def test_can_edit_matrix(self):
        self.assertTrue(user_can_edit(self.superuser))
        self.assertTrue(user_can_edit(self.admin))       # ← админ клиента теперь может
        self.assertTrue(user_can_edit(self.super_admin))
        self.assertFalse(user_can_edit(self.viewer))
        self.assertFalse(user_can_edit(self.kitchen))
        self.assertFalse(user_can_edit(self.noprofile))
