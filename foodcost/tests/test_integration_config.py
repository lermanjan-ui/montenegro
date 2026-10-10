"""Резолвер интеграций: значение арендатора имеет приоритет, пустое — фолбэк на env."""
from django.test import TestCase, override_settings

from foodcost.models import Country
from foodcost.models_integrations import TenantIntegrationSettings
from foodcost import integration_config as ic


class IntegrationConfigTests(TestCase):
    def setUp(self):
        self.country = Country.objects.create(name="Арендатор", slug="t1")

    @override_settings(PAYME_SECRET_KEY="ENV-SECRET")
    def test_fallback_to_env_when_no_row(self):
        self.assertEqual(ic.payme_config(self.country)["secret_key"], "ENV-SECRET")

    @override_settings(PAYME_SECRET_KEY="ENV-SECRET")
    def test_tenant_value_overrides_env(self):
        TenantIntegrationSettings.objects.create(
            country=self.country, payme_secret_key="TENANT-SECRET"
        )
        self.assertEqual(ic.payme_config(self.country)["secret_key"], "TENANT-SECRET")

    @override_settings(PAYME_SECRET_KEY="ENV-SECRET")
    def test_blank_tenant_field_falls_back(self):
        TenantIntegrationSettings.objects.create(
            country=self.country, payme_secret_key=""  # пусто → фолбэк
        )
        self.assertEqual(ic.payme_config(self.country)["secret_key"], "ENV-SECRET")

    @override_settings(CLICK_SERVICE_ID="111", TELEGRAM_BOT_TOKEN="env-bot")
    def test_click_and_telegram_resolve(self):
        TenantIntegrationSettings.objects.create(
            country=self.country, click_service_id="999"
        )
        self.assertEqual(ic.click_config(self.country)["service_id"], "999")      # арендатор
        self.assertEqual(ic.telegram_config(self.country)["bot_token"], "env-bot")  # фолбэк
