# -*- coding: utf-8 -*-
"""
Счета по подписке арендатора.

Платформа выставляет счёт клиенту, отмечает оплату — и подписка
активируется/продлевается (логика в platform_console).
"""
from django.db import models

from .models import Country


class SubscriptionInvoice(models.Model):
    STATUS_PENDING = "pending"
    STATUS_PAID = "paid"
    STATUS_VOID = "void"
    STATUS_CHOICES = [
        (STATUS_PENDING, "Ожидает оплаты"),
        (STATUS_PAID, "Оплачен"),
        (STATUS_VOID, "Аннулирован"),
    ]

    country = models.ForeignKey(
        Country, on_delete=models.CASCADE, related_name="invoices")
    plan_name = models.CharField(max_length=120, blank=True, default="")
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    period_start = models.DateField(null=True, blank=True)
    period_end = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    note = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Счёт {self.country.name} — {self.amount}"
