# -*- coding: utf-8 -*-
"""i18n B — язык интерфейса на пользователе (UserProfile.language)."""
from django.contrib.auth.models import User, AnonymousUser
from django.test import TestCase, RequestFactory
from django.utils import translation

from foodcost.models import UserProfile
from foodcost.i18n_middleware import UserLanguageMiddleware


class UserLanguageTests(TestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def _active_lang_for(self, user):
        captured = {}
        def get_response(req):
            captured["lang"] = translation.get_language()
            return "ok"
        mw = UserLanguageMiddleware(get_response)
        req = self.rf.get("/")
        req.user = user
        mw(req)
        return captured["lang"]

    def test_stored_language_applied(self):
        u = User.objects.create_user("u1", password="x")
        UserProfile.objects.create(user=u, role=UserProfile.ROLE_ADMIN, language="en")
        self.assertEqual(self._active_lang_for(u), "en")

    def test_uz_language_applied(self):
        u = User.objects.create_user("u2", password="x")
        UserProfile.objects.create(user=u, role=UserProfile.ROLE_ADMIN, language="uz")
        self.assertEqual(self._active_lang_for(u), "uz")

    def test_blank_language_does_not_force(self):
        u = User.objects.create_user("u3", password="x")
        UserProfile.objects.create(user=u, role=UserProfile.ROLE_ADMIN, language="")
        self.assertTrue(self._active_lang_for(u))  # какой-то дефолт, без краша

    def test_anonymous_no_crash(self):
        self.assertTrue(self._active_lang_for(AnonymousUser()) or True)
