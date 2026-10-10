"""P1 per-tenant платежи: проверка подписи/авторизации принимает секрет
конкретного арендатора параметром; без параметра — фолбэк на settings (как раньше)."""
import base64

from django.test import SimpleTestCase, RequestFactory, override_settings

from foodcost.payments import click, payme


@override_settings(PAYME_SECRET_KEY="env-payme")
class PaymePerTenantAuthTests(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def _h(self, login, key):
        return "Basic " + base64.b64encode(f"{login}:{key}".encode()).decode()

    def test_tenant_secret_param_is_used(self):
        r = self.rf.post("/x/", HTTP_AUTHORIZATION=self._h("Paycom", "tenant-payme"))
        self.assertTrue(payme.verify_payme_basic_auth(r, secret_key="tenant-payme"))
        # тот же запрос без параметра проверяется против env-ключа → отказ
        self.assertFalse(payme.verify_payme_basic_auth(r))

    def test_fallback_to_settings(self):
        r = self.rf.post("/x/", HTTP_AUTHORIZATION=self._h("Paycom", "env-payme"))
        self.assertTrue(payme.verify_payme_basic_auth(r))


@override_settings(CLICK_SECRET_KEY="env-click", CLICK_SERVICE_ID="111")
class ClickPerTenantSignatureTests(SimpleTestCase):
    base = dict(click_trans_id="9", merchant_trans_id="42", merchant_prepare_id="",
                amount="100.00", action="0", sign_time="t")

    def test_tenant_overrides_used(self):
        s = click._compute_click_signature(secret_key="tenant-click", service_id="222", **self.base)
        self.assertTrue(click.verify_click_signature(
            sign_string=s, service_id="222",
            override_secret="tenant-click", override_service_id="222", **self.base))
        # неверный секрет арендатора — отказ
        self.assertFalse(click.verify_click_signature(
            sign_string=s, service_id="222",
            override_secret="WRONG", override_service_id="222", **self.base))

    def test_fallback_to_settings(self):
        s = click._compute_click_signature(secret_key="env-click", service_id="111", **self.base)
        self.assertTrue(click.verify_click_signature(sign_string=s, service_id="111", **self.base))
