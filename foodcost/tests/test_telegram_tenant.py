"""Telegram теперь per-tenant: send_new_order_to_telegram берёт бот-токен
из настроек арендатора (через integration_config), а не из глобального env."""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from foodcost.models import Country
from foodcost.models_integrations import TenantIntegrationSettings
from foodcost import shift_views


class _Items:
    def select_related(self, *a, **k):
        return self
    def all(self):
        return []


def _fake_order(country):
    return SimpleNamespace(
        country=country,
        location=SimpleNamespace(name="Филиал", telegram_thread_id=None),
        public_order_number="N-1", id=1,
        customer_name="Клиент", customer_phone="+998",
        uzum_eats_id="", payment_method=None, delivery_address="",
        items=_Items(), subtotal_amount=0, promo_code=None,
        discount_amount=0, customer_delivery_amount=0, total_amount=0,
    )


class TelegramPerTenantTests(TestCase):
    @override_settings(TELEGRAM_BOT_TOKEN="ENV-BOT", TELEGRAM_CHAT_ID="env-chat")
    def test_uses_tenant_bot_token(self):
        country = Country.objects.create(name="Клиент А", slug="ca")
        TenantIntegrationSettings.objects.create(
            country=country, telegram_bot_token="TENANT-BOT", telegram_chat_id="tenant-chat"
        )
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            return SimpleNamespace(read=lambda: b"", close=lambda: None)

        with patch("foodcost.shift_views.urllib.request.urlopen", side_effect=fake_urlopen):
            shift_views.send_new_order_to_telegram(_fake_order(country))

        # В URL ушёл токен АРЕНДАТОРА, а не из env
        self.assertIn("TENANT-BOT", captured.get("url", ""))
        self.assertNotIn("ENV-BOT", captured.get("url", ""))

    @override_settings(TELEGRAM_BOT_TOKEN="ENV-BOT", TELEGRAM_CHAT_ID="env-chat")
    def test_falls_back_to_env_without_tenant_settings(self):
        country = Country.objects.create(name="Клиент Б", slug="cb")
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            return SimpleNamespace(read=lambda: b"", close=lambda: None)

        with patch("foodcost.shift_views.urllib.request.urlopen", side_effect=fake_urlopen):
            shift_views.send_new_order_to_telegram(_fake_order(country))

        self.assertIn("ENV-BOT", captured.get("url", ""))
