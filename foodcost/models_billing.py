"""
Тарификация и доступ к модулям (фундамент для кабинета супер-админа и для
ограничения доступов у админа клиента).

Модель:
  Module  — продаваемый/подключаемый модуль системы. code совпадает с
            разделами (UserProfile.SECTION_*), чтобы переиспользовать уже
            существующую механику allowed_sections.
  Plan    — тариф: набор модулей (base) + цена.
  TenantSubscription — подписка арендатора: тариф + доп. модули (за отд. плату)
            − точечно отключённые. Даёт «эффективный» набор модулей клиента.

Правило доступа (см. foodcost.module_access):
  нет подписки у арендатора → ограничений нет (все модули) — обратная
  совместимость с текущим единственным клиентом.
"""
from django.db import models

from .models import Country


class Module(models.Model):
    code = models.CharField(max_length=50, unique=True)      # == раздел системы
    name = models.CharField(max_length=120)
    description = models.CharField(max_length=255, blank=True, default="")
    is_addon = models.BooleanField(default=False)            # можно продавать отдельно
    monthly_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Plan(models.Model):
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=120)
    monthly_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    modules = models.ManyToManyField(Module, blank=True, related_name="plans")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class TenantSubscription(models.Model):
    STATUS_TRIAL = "trial"
    STATUS_ACTIVE = "active"
    STATUS_SUSPENDED = "suspended"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_TRIAL, "Триал"),
        (STATUS_ACTIVE, "Активна"),
        (STATUS_SUSPENDED, "Приостановлена"),
        (STATUS_CANCELLED, "Отменена"),
    ]

    country = models.OneToOneField(
        Country, on_delete=models.CASCADE, related_name="subscription"
    )
    plan = models.ForeignKey(
        Plan, on_delete=models.SET_NULL, null=True, blank=True, related_name="subscriptions"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_TRIAL)
    trial_ends_at = models.DateTimeField(null=True, blank=True)
    current_period_end = models.DateTimeField(null=True, blank=True)

    # Доп. модули сверх тарифа (за отдельную плату) и точечно отключённые.
    extra_modules = models.ManyToManyField(
        Module, blank=True, related_name="extra_subscriptions")
    disabled_modules = models.ManyToManyField(
        Module, blank=True, related_name="disabled_subscriptions")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Подписка · {self.country.name}"

    def effective_module_codes(self):
        """Итоговый набор кодов модулей: (тариф ∪ доп) − отключённые."""
        codes = set()
        if self.plan_id:
            codes |= set(self.plan.modules.values_list("code", flat=True))
        codes |= set(self.extra_modules.values_list("code", flat=True))
        codes -= set(self.disabled_modules.values_list("code", flat=True))
        return codes

    def is_usable(self):
        return self.status in (self.STATUS_TRIAL, self.STATUS_ACTIVE)
