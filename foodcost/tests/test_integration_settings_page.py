"""Экран настроек интеграций: доступен из ERP, показывает callback-URL,
сохраняет обычные поля, а пустой секрет не затирает уже заданный."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from foodcost.models import Country, UserProfile
from foodcost.models_integrations import TenantIntegrationSettings


class IntegrationSettingsPageTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Клиент А", slug="a")
        admin = get_user_model().objects.create_user("adm", password="pw")
        prof = UserProfile.objects.create(
            user=admin, role=UserProfile.ROLE_ADMIN,
            allowed_sections=[UserProfile.SECTION_SETTINGS])
        prof.countries.add(self.country)
        self.client.force_login(admin)

    def test_get_shows_tenant_callback_url(self):
        r = self.client.get("/c/a/settings/integrations/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "/api/payments/payme/a/callback/")
        self.assertContains(r, "/api/payments/click/a/callback/")

    def test_post_saves_plain_field(self):
        r = self.client.post("/c/a/settings/integrations/", {"payme_merchant_id": "M123"})
        self.assertEqual(r.status_code, 302)
        s = TenantIntegrationSettings.objects.get(country=self.country)
        self.assertEqual(s.payme_merchant_id, "M123")

    def test_blank_secret_keeps_existing(self):
        TenantIntegrationSettings.objects.create(country=self.country, payme_secret_key="SECRET")
        self.client.post("/c/a/settings/integrations/", {"payme_merchant_id": "M"})
        s = TenantIntegrationSettings.objects.get(country=self.country)
        self.assertEqual(s.payme_secret_key, "SECRET")   # пусто в форме → не тронуто

    def test_new_secret_is_saved(self):
        self.client.post("/c/a/settings/integrations/",
                         {"payme_merchant_id": "M", "payme_secret_key": "NEW"})
        s = TenantIntegrationSettings.objects.get(country=self.country)
        self.assertEqual(s.payme_secret_key, "NEW")
