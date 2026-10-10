"""Смоук-тесты денежно-критичной верификации платежей Click и Payme.

Это чистые функции проверки подписи/авторизации — именно их легче всего
случайно сломать рефакторингом, а цена поломки — пропущенный/поддельный
платёж. Тесты фиксируют happy-path и отказы.
"""
import base64
from decimal import Decimal
from types import SimpleNamespace

from django.test import SimpleTestCase, RequestFactory, override_settings

from foodcost.payments import click, payme


@override_settings(CLICK_SECRET_KEY="test-secret", CLICK_SERVICE_ID="12345")
class ClickSignatureTests(SimpleTestCase):
    base = dict(
        click_trans_id="999", service_id="12345", merchant_trans_id="42",
        merchant_prepare_id="", amount="172200.00", action="0", sign_time="2026-10-10 10:00:00",
    )

    def _good_sign(self, **over):
        p = {**self.base, **over}
        return click._compute_click_signature(secret_key="test-secret", **p)

    def test_valid_signature_accepts(self):
        sign = self._good_sign()
        self.assertTrue(click.verify_click_signature(sign_string=sign, **self.base))

    def test_tampered_amount_rejected(self):
        sign = self._good_sign()  # подпись на 172200.00
        bad = {**self.base, "amount": "1.00"}  # а сумму подменили
        self.assertFalse(click.verify_click_signature(sign_string=sign, **bad))

    def test_wrong_service_id_rejected(self):
        sign = self._good_sign(service_id="99999")
        bad = {**self.base, "service_id": "99999"}
        self.assertFalse(click.verify_click_signature(sign_string=sign, **bad))

    def test_empty_sign_rejected(self):
        self.assertFalse(click.verify_click_signature(sign_string="", **self.base))

    @override_settings(CLICK_SECRET_KEY="")
    def test_unconfigured_secret_fails_closed(self):
        self.assertFalse(click.verify_click_signature(sign_string="whatever", **self.base))

    def test_amounts_match(self):
        self.assertTrue(click.amounts_match(Decimal("172200"), "172200.00"))
        self.assertTrue(click.amounts_match(Decimal("172200.00"), "172200"))
        self.assertFalse(click.amounts_match(Decimal("172200"), "172199.99"))
        self.assertFalse(click.amounts_match(None, "1"))


@override_settings(PAYME_SECRET_KEY="paysecret")
class PaymeAuthTests(SimpleTestCase):
    def setUp(self):
        self.rf = RequestFactory()

    def _req(self, header):
        return self.rf.post("/api/payments/payme/", HTTP_AUTHORIZATION=header)

    def _basic(self, login, key):
        raw = base64.b64encode(f"{login}:{key}".encode()).decode()
        return "Basic " + raw

    def test_valid_basic_auth(self):
        self.assertTrue(payme.verify_payme_basic_auth(self._req(self._basic("Paycom", "paysecret"))))

    def test_wrong_key_rejected(self):
        self.assertFalse(payme.verify_payme_basic_auth(self._req(self._basic("Paycom", "nope"))))

    def test_wrong_login_rejected(self):
        self.assertFalse(payme.verify_payme_basic_auth(self._req(self._basic("Admin", "paysecret"))))

    def test_missing_header_rejected(self):
        self.assertFalse(payme.verify_payme_basic_auth(self.rf.post("/x/")))

    @override_settings(PAYME_SECRET_KEY="")
    def test_unconfigured_fails_closed(self):
        self.assertFalse(payme.verify_payme_basic_auth(self._req(self._basic("Paycom", "anything"))))

    def test_amount_to_tiyin_rounds_half_up(self):
        self.assertEqual(payme.amount_to_tiyin(Decimal("100.50")), 10050)
        self.assertEqual(payme.amount_to_tiyin(Decimal("172200")), 17220000)

    def test_amount_to_tiyin_rejects_nonpositive(self):
        with self.assertRaises(Exception):
            payme.amount_to_tiyin(Decimal("0"))

    def test_amount_matches_order(self):
        order = SimpleNamespace(total_amount=Decimal("100.50"))
        self.assertTrue(payme.amount_matches_order(order, 10050))
        self.assertFalse(payme.amount_matches_order(order, 10049))
        self.assertFalse(payme.amount_matches_order(order, True))   # bool != int
        self.assertFalse(payme.amount_matches_order(order, "10050"))  # строка
        self.assertFalse(payme.amount_matches_order(SimpleNamespace(total_amount=None), 10050))
