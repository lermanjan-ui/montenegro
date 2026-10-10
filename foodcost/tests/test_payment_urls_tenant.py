"""P3: платёжная ссылка строится на мерчанте АРЕНДАТОРА (из integration_config),
с фолбэком на settings."""
import base64
from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase, override_settings

from foodcost.models import Country
from foodcost.models_integrations import TenantIntegrationSettings
from foodcost.payments import payme, click


@override_settings(
    PAYME_MERCHANT_ID="ENV-M", PAYME_CHECKOUT_URL="https://checkout.paycom.uz",
    PAYME_ACCOUNT_FIELD="order_id",
    CLICK_SERVICE_ID="ENV-S", CLICK_MERCHANT_ID="ENV-CM",
)
class PaymentUrlPerTenantTests(TestCase):
    def _order(self, country):
        return SimpleNamespace(country=country, total_amount=Decimal("100.00"),
                               public_order_number="RCN-1", id=1)

    def _payme_decoded(self, url):
        return base64.b64decode(url.rsplit("/", 1)[-1]).decode()

    def test_payme_uses_tenant_merchant(self):
        c = Country.objects.create(name="A", slug="a")
        TenantIntegrationSettings.objects.create(
            country=c, payme_merchant_id="T-M", payme_checkout_url="https://checkout.paycom.uz")
        decoded = self._payme_decoded(payme.build_payme_payment_url(self._order(c)))
        self.assertIn("m=T-M", decoded)
        self.assertNotIn("m=ENV-M", decoded)

    def test_payme_fallback_to_settings(self):
        c = Country.objects.create(name="B", slug="b")  # без настроек арендатора
        decoded = self._payme_decoded(payme.build_payme_payment_url(self._order(c)))
        self.assertIn("m=ENV-M", decoded)

    def test_click_uses_tenant_creds(self):
        c = Country.objects.create(name="C", slug="c")
        TenantIntegrationSettings.objects.create(
            country=c, click_service_id="T-S", click_merchant_id="T-CM")
        url = click.build_click_payment_url(self._order(c))
        self.assertIn("service_id=T-S", url)
        self.assertIn("merchant_id=T-CM", url)

    def test_click_fallback_to_settings(self):
        c = Country.objects.create(name="D", slug="d")
        url = click.build_click_payment_url(self._order(c))
        self.assertIn("service_id=ENV-S", url)
        self.assertIn("merchant_id=ENV-CM", url)
