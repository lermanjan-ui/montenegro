"""OTP: хэш кода — это секьюрити-примитив входа в приложение.
Фиксируем детерминизм и зависимость от SECRET_KEY."""
from django.test import SimpleTestCase, override_settings
from foodcost import app_auth


class OtpHashTests(SimpleTestCase):
    @override_settings(SECRET_KEY="k1")
    def test_hash_is_deterministic(self):
        self.assertEqual(app_auth._hash("+998901112233:1234"),
                         app_auth._hash("+998901112233:1234"))

    @override_settings(SECRET_KEY="k1")
    def test_different_inputs_differ(self):
        self.assertNotEqual(app_auth._hash("+998901112233:1234"),
                            app_auth._hash("+998901112233:9999"))

    def test_hash_depends_on_secret(self):
        with override_settings(SECRET_KEY="k1"):
            a = app_auth._hash("phone:code")
        with override_settings(SECRET_KEY="k2"):
            b = app_auth._hash("phone:code")
        self.assertNotEqual(a, b)

    @override_settings(SECRET_KEY="k1")
    def test_normalize_phone(self):
        self.assertEqual(app_auth._normalize_phone(" +998 (90) 111-22-33 "), "+998901112233")
        self.assertEqual(app_auth._normalize_phone("abc"), "")
