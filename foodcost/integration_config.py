"""
Резолвер настроек интеграций с фолбэком на глобальный env.

Правило: берём значение арендатора (TenantIntegrationSettings); если оно пустое
или строки нет вовсе — откатываемся на settings.* (как работает сейчас).
Весь платёжный/SMS/Telegram-код должен читать креды ТОЛЬКО через эти функции,
а не напрямую из settings — тогда переключение на per-tenant будет прозрачным.
"""
from django.conf import settings


def _tenant_settings(country):
    if country is None:
        return None
    try:
        return country.integration_settings
    except Exception:
        # Нет строки настроек у арендатора — это нормально (работаем на env).
        return None


def _resolve(country, field, settings_name, default=""):
    ts = _tenant_settings(country)
    if ts is not None:
        val = (getattr(ts, field, "") or "").strip()
        if val:
            return val
    return getattr(settings, settings_name, default) or default


# ---- Payme ----
def payme_config(country):
    return {
        "merchant_id": _resolve(country, "payme_merchant_id", "PAYME_MERCHANT_ID"),
        "secret_key": _resolve(country, "payme_secret_key", "PAYME_SECRET_KEY"),
        "checkout_url": _resolve(country, "payme_checkout_url", "PAYME_CHECKOUT_URL",
                                 "https://checkout.paycom.uz"),
        "account_field": _resolve(country, "payme_account_field", "PAYME_ACCOUNT_FIELD",
                                  "order_id"),
        "success_url": _resolve(country, "payme_success_url", "PAYME_SUCCESS_URL"),
        "fail_url": _resolve(country, "payme_fail_url", "PAYME_FAIL_URL"),
    }


# ---- Click ----
def click_config(country):
    return {
        "service_id": _resolve(country, "click_service_id", "CLICK_SERVICE_ID"),
        "merchant_id": _resolve(country, "click_merchant_id", "CLICK_MERCHANT_ID"),
        "merchant_user_id": _resolve(country, "click_merchant_user_id", "CLICK_MERCHANT_USER_ID"),
        "secret_key": _resolve(country, "click_secret_key", "CLICK_SECRET_KEY"),
        "success_url": _resolve(country, "click_success_url", "CLICK_SUCCESS_URL"),
        "fail_url": _resolve(country, "click_fail_url", "CLICK_FAIL_URL"),
    }


# ---- Eskiz ----
def eskiz_config(country):
    return {
        "email": _resolve(country, "eskiz_email", "ESKIZ_EMAIL"),
        "password": _resolve(country, "eskiz_password", "ESKIZ_PASSWORD"),
        "from": _resolve(country, "eskiz_from", "ESKIZ_FROM", "4546"),
        "otp_template": _resolve(country, "eskiz_otp_template", "ESKIZ_OTP_TEMPLATE", "Kod: {code}"),
        "base_url": _resolve(country, "eskiz_base_url", "ESKIZ_BASE_URL", "https://notify.eskiz.uz/api"),
    }


# ---- Telegram ----
def telegram_config(country):
    return {
        "bot_token": _resolve(country, "telegram_bot_token", "TELEGRAM_BOT_TOKEN"),
        "chat_id": _resolve(country, "telegram_chat_id", "TELEGRAM_CHAT_ID"),
    }
