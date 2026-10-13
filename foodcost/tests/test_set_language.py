# -*- coding: utf-8 -*-
"""i18n C — переключатель языка (сохраняет в профиль / cookie)."""
from django.contrib.auth.models import User
from django.test import TestCase

from foodcost.models import UserProfile


class SetLanguageTests(TestCase):
    def setUp(self):
        self.u = User.objects.create_user("u", password="x")
        self.p = UserProfile.objects.create(user=self.u, role=UserProfile.ROLE_ADMIN)
        self.client.force_login(self.u)

    def test_sets_profile_language(self):
        r = self.client.post("/i18n/set/", {"language": "uz", "next": "/"})
        self.assertEqual(r.status_code, 302)
        self.p.refresh_from_db()
        self.assertEqual(self.p.language, "uz")

    def test_invalid_language_ignored(self):
        self.client.post("/i18n/set/", {"language": "xx", "next": "/"})
        self.p.refresh_from_db()
        self.assertEqual(self.p.language, "")

    def test_anonymous_sets_cookie(self):
        self.client.logout()
        r = self.client.post("/i18n/set/", {"language": "ru", "next": "/"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.cookies.get("django_language").value, "ru")
