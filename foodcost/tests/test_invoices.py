# -*- coding: utf-8 -*-
"""
Тесты счетов по подписке (платформенная консоль).
"""
from django.contrib.auth.models import User
from django.test import TestCase

from foodcost.models import Country, UserProfile
from foodcost.models_billing import Module, Plan, TenantSubscription
from foodcost.models_invoices import SubscriptionInvoice


class InvoiceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner", password="x", is_superuser=True)
        self.country = Country.objects.create(name="Чайхана", slug="chaihana")
        self.plan = Plan.objects.create(code="start", name="Старт", monthly_price=500000)
        self.extra = Module.objects.create(
            code="finance", name="Финансы", is_addon=True, monthly_price=100000)
        self.sub = TenantSubscription.objects.create(
            country=self.country, plan=self.plan,
            status=TenantSubscription.STATUS_TRIAL)
        self.url = f"/platform/clients/{self.country.slug}/"
        self.client.force_login(self.owner)

    def test_issue_invoice_amount(self):
        self.sub.extra_modules.add(self.extra)
        self.client.post(self.url, {"action": "issue_invoice"})
        inv = SubscriptionInvoice.objects.get(country=self.country)
        self.assertEqual(inv.status, SubscriptionInvoice.STATUS_PENDING)
        self.assertEqual(int(inv.amount), 600000)   # 500000 тариф + 100000 доп
        self.assertEqual(inv.plan_name, "Старт")

    def test_mark_paid_activates_subscription(self):
        self.client.post(self.url, {"action": "issue_invoice"})
        inv = SubscriptionInvoice.objects.get(country=self.country)
        self.client.post(self.url, {
            "action": "mark_invoice_paid", "invoice_id": inv.id})
        inv.refresh_from_db()
        self.sub.refresh_from_db()
        self.assertEqual(inv.status, SubscriptionInvoice.STATUS_PAID)
        self.assertIsNotNone(inv.paid_at)
        self.assertEqual(self.sub.status, TenantSubscription.STATUS_ACTIVE)
        self.assertIsNotNone(self.sub.current_period_end)

    def test_void_invoice(self):
        self.client.post(self.url, {"action": "issue_invoice"})
        inv = SubscriptionInvoice.objects.get(country=self.country)
        self.client.post(self.url, {
            "action": "void_invoice", "invoice_id": inv.id})
        inv.refresh_from_db()
        self.assertEqual(inv.status, SubscriptionInvoice.STATUS_VOID)

    def test_non_admin_cannot_issue(self):
        u = User.objects.create_user("plain", password="x")
        UserProfile.objects.create(user=u, role=UserProfile.ROLE_ADMIN)
        self.client.force_login(u)
        resp = self.client.post(self.url, {"action": "issue_invoice"})
        self.assertEqual(resp.status_code, 403)
        self.assertFalse(SubscriptionInvoice.objects.exists())
