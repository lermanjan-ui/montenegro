"""
Настройки интеграций НА АРЕНДАТОРА (per-tenant).

Зачем: сейчас креды Payme/Click/Eskiz/Telegram живут в глобальном окружении —
годится для одного бизнеса, но не для продукта: у каждого клиента свои учётки.
Эта модель хранит их по арендатору (Country). Чтение — через
foodcost.integration_config, который берёт значение арендатора, а если пусто —
откатывается на глобальный settings.* (поэтому текущий единственный арендатор
продолжает работать на env без изменений).

ВАЖНО (тех-долг): секреты тут хранятся в открытом виде — как и раньше в env.
Перед продакшн-продажей вынести в шифрование/секрет-менеджер.
"""
from django.db import models

from .models import Country


class TenantIntegrationSettings(models.Model):
    country = models.OneToOneField(
        Country,
        on_delete=models.CASCADE,
        related_name="integration_settings",
    )

    # ---- Payme ----
    payme_merchant_id = models.CharField(max_length=64, blank=True, default="")
    payme_secret_key = models.CharField(max_length=256, blank=True, default="")
    payme_checkout_url = models.CharField(max_length=255, blank=True, default="")
    payme_account_field = models.CharField(max_length=64, blank=True, default="")
    payme_success_url = models.CharField(max_length=255, blank=True, default="")
    payme_fail_url = models.CharField(max_length=255, blank=True, default="")

    # ---- Click ----
    click_service_id = models.CharField(max_length=64, blank=True, default="")
    click_merchant_id = models.CharField(max_length=64, blank=True, default="")
    click_merchant_user_id = models.CharField(max_length=64, blank=True, default="")
    click_secret_key = models.CharField(max_length=256, blank=True, default="")
    click_success_url = models.CharField(max_length=255, blank=True, default="")
    click_fail_url = models.CharField(max_length=255, blank=True, default="")

    # ---- Eskiz (SMS/OTP) ----
    eskiz_email = models.CharField(max_length=255, blank=True, default="")
    eskiz_password = models.CharField(max_length=255, blank=True, default="")
    eskiz_from = models.CharField(max_length=32, blank=True, default="")
    eskiz_otp_template = models.CharField(max_length=255, blank=True, default="")
    eskiz_base_url = models.CharField(max_length=255, blank=True, default="")

    # ---- Telegram ----
    telegram_bot_token = models.CharField(max_length=255, blank=True, default="")
    telegram_chat_id = models.CharField(max_length=64, blank=True, default="")

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Интеграции арендатора"
        verbose_name_plural = "Интеграции арендаторов"

    def __str__(self):
        return f"Интеграции · {self.country.name}"
