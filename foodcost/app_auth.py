"""
🔐 Авторизация мобильного приложения: вход по SMS (OTP) + токены.

Эндпоинты (все POST, JSON):
  /api/app/auth/request-code  — запросить код (шлём через Eskiz)
  /api/app/auth/verify-code   — проверить код -> выдать токены, найти/создать Customer
  /api/app/auth/refresh       — обновить access по refresh
  /api/app/auth/logout        — отозвать токен текущего устройства

Безопасность:
  - коды и токены хранятся ТОЛЬКО в виде HMAC-SHA256 хешей (см. _hash);
  - на проде код НИКОГДА не возвращается в ответе; только при явном флаге
    settings.OTP_EXPOSE_CODE_FOR_TESTING (для стейджа, пока шаблон Eskiz не одобрен);
  - лимиты: повтор не чаще OTP_RESEND_COOLDOWN_SECONDS, не более OTP_MAX_PER_HOUR
    на номер в час, не более OTP_MAX_ATTEMPTS попыток ввода.

Переиспользует helpers из public_api: api_success / api_error / _parse_json_body
/ _get_country_from_payload.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Customer, CustomerToken, OtpCode
from .public_api import (
    api_success,
    api_error,
    _parse_json_body,
    _get_country_from_payload,
)
from . import sms_eskiz

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _hash(value):
    secret = (getattr(settings, "SECRET_KEY", "") or "").encode("utf-8")
    return hmac.new(secret, str(value).encode("utf-8"), hashlib.sha256).hexdigest()


def _normalize_phone(raw):
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not digits:
        return ""
    return "+" + digits


def _cfg_int(name, default):
    try:
        return int(getattr(settings, name, default))
    except (TypeError, ValueError):
        return default


def _issue_tokens(customer, payload=None):
    now = timezone.now()
    access = secrets.token_urlsafe(32)
    refresh = secrets.token_urlsafe(32)
    access_days = _cfg_int("APP_ACCESS_TOKEN_TTL_DAYS", 30)
    refresh_days = _cfg_int("APP_REFRESH_TOKEN_TTL_DAYS", 180)
    platform = str((payload or {}).get("platform") or "")[:20]
    device_name = str((payload or {}).get("device_name") or "")[:120]
    row = CustomerToken.objects.create(
        customer=customer,
        access_hash=_hash(access),
        refresh_hash=_hash(refresh),
        access_expires_at=now + timedelta(days=access_days),
        refresh_expires_at=now + timedelta(days=refresh_days),
        platform=platform,
        device_name=device_name,
    )
    return access, refresh, row


def _authenticate(request):
    """Возвращает (customer, token_row, None) или (None, None, error_response)."""
    header = (
        request.META.get("HTTP_AUTHORIZATION")
        or request.headers.get("Authorization")
        or ""
    ).strip()
    if not header.lower().startswith("bearer "):
        return None, None, api_error("UNAUTHORIZED", "Требуется вход", status=401)
    token = header[7:].strip()
    if not token:
        return None, None, api_error("UNAUTHORIZED", "Требуется вход", status=401)
    now = timezone.now()
    row = (
        CustomerToken.objects.select_related("customer")
        .filter(access_hash=_hash(token), revoked=False, access_expires_at__gte=now)
        .first()
    )
    if row is None:
        return None, None, api_error(
            "UNAUTHORIZED", "Сессия истекла, войдите снова", status=401
        )
    return row.customer, row, None


def customer_from_request(request):
    """Хелпер для будущих /api/app/* эндпоинтов: вошедший Customer или None."""
    customer, _row, err = _authenticate(request)
    return None if err else customer


# ---------------------------------------------------------------------------
# endpoints
# ---------------------------------------------------------------------------
@csrf_exempt
@require_POST
def auth_request_code(request):
    payload, err = _parse_json_body(request)
    if err:
        return err
    country, err = _get_country_from_payload(payload)
    if err:
        return err

    phone = _normalize_phone(payload.get("phone"))
    if len(phone) < 10:  # "+" + минимум 9 цифр
        return api_error("INVALID_PHONE", "Неверный номер телефона", status=400)

    now = timezone.now()
    cooldown = _cfg_int("OTP_RESEND_COOLDOWN_SECONDS", 60)
    max_per_hour = _cfg_int("OTP_MAX_PER_HOUR", 5)

    too_soon = OtpCode.objects.filter(
        country=country, phone=phone,
        created_at__gte=now - timedelta(seconds=cooldown),
    ).exists()
    if too_soon:
        return api_error(
            "TOO_SOON", "Код уже отправлен, подождите перед повторной отправкой.",
            details={"resend_after": cooldown}, status=429,
        )

    last_hour = OtpCode.objects.filter(
        country=country, phone=phone,
        created_at__gte=now - timedelta(hours=1),
    ).count()
    if last_hour >= max_per_hour:
        return api_error(
            "TOO_MANY_REQUESTS",
            "Слишком много запросов кода. Попробуйте позже.", status=429,
        )

    ttl = _cfg_int("OTP_CODE_TTL_SECONDS", 300)
    code_len = _cfg_int("OTP_CODE_LENGTH", 4)
    code = f"{secrets.randbelow(10 ** code_len):0{code_len}d}"
    OtpCode.objects.create(
        country=country,
        phone=phone,
        code_hash=_hash(f"{phone}:{code}"),
        expires_at=now + timedelta(seconds=ttl),
    )

    sent = False
    try:
        sent = sms_eskiz.send_otp(phone, code)
    except Exception:
        logger.exception("send_otp raised")
        sent = False

    data = {"ttl": ttl, "resend_after": cooldown, "delivery": "sms" if sent else "pending"}
    if not sent:
        logger.warning("OTP not delivered via SMS (Eskiz off/failed/not approved).")
        # Только для отладки на стейдже (НЕ включать на проде).
        if getattr(settings, "OTP_EXPOSE_CODE_FOR_TESTING", False):
            data["debug_code"] = code
    return api_success(data)


@csrf_exempt
@require_POST
def auth_verify_code(request):
    payload, err = _parse_json_body(request)
    if err:
        return err
    country, err = _get_country_from_payload(payload)
    if err:
        return err

    phone = _normalize_phone(payload.get("phone"))
    code = "".join(ch for ch in str(payload.get("code") or "") if ch.isdigit())
    if not phone or not code:
        return api_error("INVALID_REQUEST", "Укажите телефон и код", status=400)

    now = timezone.now()
    max_attempts = _cfg_int("OTP_MAX_ATTEMPTS", 5)

    with transaction.atomic():
        otp = (
            OtpCode.objects.select_for_update()
            .filter(country=country, phone=phone, is_used=False, expires_at__gte=now)
            .order_by("-created_at")
            .first()
        )
        if otp is None:
            return api_error(
                "CODE_EXPIRED", "Код не найден или истёк. Запросите новый.", status=400
            )
        if otp.attempts >= max_attempts:
            return api_error(
                "TOO_MANY_ATTEMPTS",
                "Слишком много попыток. Запросите новый код.", status=429,
            )

        otp.attempts += 1
        if not hmac.compare_digest(otp.code_hash, _hash(f"{phone}:{code}")):
            otp.save(update_fields=["attempts"])
            return api_error(
                "INVALID_CODE", "Неверный код",
                details={"attempts_left": max(0, max_attempts - otp.attempts)},
                status=400,
            )

        otp.is_used = True
        otp.save(update_fields=["attempts", "is_used"])

        customer = (
            Customer.objects.filter(country=country, phone=phone).order_by("id").first()
        )
        is_new = False
        if customer is None:
            customer = Customer.objects.create(country=country, phone=phone, name="")
            is_new = True

        access, refresh, row = _issue_tokens(customer, payload)

    return api_success({
        "access_token": access,
        "refresh_token": refresh,
        "access_expires_at": row.access_expires_at.isoformat(),
        "refresh_expires_at": row.refresh_expires_at.isoformat(),
        "is_new": is_new,
        "customer": {
            "id": customer.id,
            "phone": customer.phone,
            "name": customer.name or "",
        },
    })


@csrf_exempt
@require_POST
def auth_refresh(request):
    payload, err = _parse_json_body(request)
    if err:
        return err
    refresh = str(payload.get("refresh_token") or "").strip()
    if not refresh:
        return api_error("INVALID_REQUEST", "Нет refresh_token", status=400)

    now = timezone.now()
    with transaction.atomic():
        row = (
            CustomerToken.objects.select_for_update()
            .filter(refresh_hash=_hash(refresh), revoked=False, refresh_expires_at__gte=now)
            .first()
        )
        if row is None:
            return api_error("INVALID_TOKEN", "Сессия истекла, войдите снова", status=401)

        access = secrets.token_urlsafe(32)
        access_days = _cfg_int("APP_ACCESS_TOKEN_TTL_DAYS", 30)
        row.access_hash = _hash(access)
        row.access_expires_at = now + timedelta(days=access_days)
        row.last_used_at = now
        row.save(update_fields=["access_hash", "access_expires_at", "last_used_at"])

    return api_success({
        "access_token": access,
        "access_expires_at": row.access_expires_at.isoformat(),
    })


@csrf_exempt
@require_POST
def auth_logout(request):
    customer, row, err = _authenticate(request)
    if err:
        return err
    row.revoked = True
    row.save(update_fields=["revoked"])
    return api_success({"ok": True})
