"""P2 per-tenant колбэки платежей: подпись/авторизация проверяется секретом
АРЕНДАТОРА (по slug в callback-URL). Тесты не требуют фикстуры заказа —
проверяют именно стадию верификации.

Click:  подпись секретом арендатора валидна на его URL (дальше заказ не найден
        → USER_DOES_NOT_EXIST), а на легаси-URL (settings) → SIGN_CHECK_FAILED.
Payme:  Basic с ключом арендатора авторизуется на его URL (НЕ -32504),
        а на легаси-URL → -32504.
"""
import base64
import json

from django.test import TestCase, override_settings

from foodcost.models import Country
from foodcost.models_integrations import TenantIntegrationSettings
from foodcost.payments import click as click_mod
from foodcost.payments.click import ERROR_SIGN_CHECK_FAILED, ERROR_USER_DOES_NOT_EXIST
from foodcost.payments.payme import ERROR_INSUFFICIENT_PRIVILEGE


@override_settings(CLICK_SECRET_KEY="ENV-CLICK", CLICK_SERVICE_ID="ENV-SVC")
class ClickCallbackPerTenantTests(TestCase):
    def setUp(self):
        Country.objects.create(name="UZ", slug="uzbekistan")   # дефолтный арендатор
        tenant = Country.objects.create(name="Клиент Б", slug="b")
        TenantIntegrationSettings.objects.create(
            country=tenant, click_secret_key="T-CLICK", click_service_id="T-SVC")

    def _body(self, secret, svc):
        base = dict(click_trans_id="1", merchant_trans_id="NOPE", merchant_prepare_id="",
                    amount="100.00", action="0", sign_time="t")
        sign = click_mod._compute_click_signature(secret_key=secret, service_id=svc, **base)
        return {"click_trans_id": "1", "service_id": svc, "merchant_trans_id": "NOPE",
                "merchant_prepare_id": "", "amount": "100.00", "action": "0",
                "sign_time": "t", "sign_string": sign}

    def test_tenant_secret_valid_on_tenant_url(self):
        r = self.client.post("/api/payments/click/b/callback/", self._body("T-CLICK", "T-SVC"))
        self.assertEqual(r.json()["error"], ERROR_USER_DOES_NOT_EXIST)  # подпись ОК, заказа нет

    def test_tenant_secret_rejected_on_legacy_url(self):
        r = self.client.post("/api/payments/click/callback/", self._body("T-CLICK", "T-SVC"))
        self.assertEqual(r.json()["error"], ERROR_SIGN_CHECK_FAILED)    # не тот секрет


@override_settings(PAYME_SECRET_KEY="ENV-PAYME")
class PaymeCallbackPerTenantTests(TestCase):
    def setUp(self):
        Country.objects.create(name="UZ", slug="uzbekistan")
        tenant = Country.objects.create(name="Клиент Б", slug="b")
        TenantIntegrationSettings.objects.create(country=tenant, payme_secret_key="T-PAYME")

    def _rpc(self):
        return json.dumps({"id": 1, "method": "CheckPerformTransaction",
                           "params": {"account": {"order_id": "NOPE"}, "amount": 1}})

    def _auth(self, key):
        return "Basic " + base64.b64encode(f"Paycom:{key}".encode()).decode()

    def _err_code(self, resp):
        return (resp.json().get("error") or {}).get("code")

    def test_tenant_secret_authorizes_on_tenant_url(self):
        r = self.client.post("/api/payments/payme/b/callback/", self._rpc(),
                             content_type="application/json",
                             HTTP_AUTHORIZATION=self._auth("T-PAYME"))
        self.assertNotEqual(self._err_code(r), ERROR_INSUFFICIENT_PRIVILEGE)  # авторизация прошла

    def test_tenant_secret_rejected_on_legacy_url(self):
        r = self.client.post("/api/payments/payme/callback/", self._rpc(),
                             content_type="application/json",
                             HTTP_AUTHORIZATION=self._auth("T-PAYME"))
        self.assertEqual(self._err_code(r), ERROR_INSUFFICIENT_PRIVILEGE)     # чужой ключ
