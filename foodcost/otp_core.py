# -*- coding: utf-8 -*-
"""
OTP для веб-входа юрлиц (сессионный, два канала: SMS и e-mail).

Код не хранится в БД — только его HMAC-хеш в сессии вместе со сроком жизни,
каналом, назначением (signup/login) и доп. данными. SMS — через Eskiz,
письма — через настроенный SMTP (django.core.mail). Если канал не настроен
или включён OTP_EXPOSE_CODE_FOR_TESTING — код возвращается для показа на экране
(режим разработки).
"""
import hashlib
import hmac
import secrets
import time

from django.conf import settings
from django.core.mail import send_mail

from . import sms_eskiz

CODE_LENGTH = 5
TTL_SECONDS = 300
MAX_ATTEMPTS = 5


def normalize_phone(raw):
    digits = "".join(ch for ch in str(raw or "") if ch.isdigit())
    return "+" + digits if digits else ""


def _hash(value):
    secret = (getattr(settings, "SECRET_KEY", "") or "").encode("utf-8")
    return hmac.new(secret, str(value).encode("utf-8"), hashlib.sha256).hexdigest()


def _send_email(email, code):
    if not getattr(settings, "EMAIL_HOST", ""):
        return False
    try:
        send_mail(
            "Код входа · FoodCost",
            f"Ваш код подтверждения: {code}\nКод действует 5 минут.",
            getattr(settings, "DEFAULT_FROM_EMAIL", "no-reply@foodcost.uz"),
            [email],
            fail_silently=False,
        )
        return True
    except Exception:
        return False


def _send_sms(phone, code):
    try:
        res = sms_eskiz.send_sms_result(phone, f"FoodCost kod: {code}")
        if isinstance(res, dict):
            return bool(res.get("ok"))
        return bool(getattr(res, "ok", False))
    except Exception:
        return False


def start_otp(request, identifier, channel, purpose, extra=None):
    """Сгенерировать код, положить в сессию, отправить. Вернуть (ok, debug_code)."""
    code = f"{secrets.randbelow(10 ** CODE_LENGTH):0{CODE_LENGTH}d}"
    request.session["otp"] = {
        "hash": _hash(f"{purpose}:{identifier}:{code}"),
        "identifier": identifier,
        "channel": channel,
        "purpose": purpose,
        "expires": time.time() + TTL_SECONDS,
        "attempts": 0,
        "extra": extra or {},
    }
    if channel == "email":
        sent = _send_email(identifier, code)
    else:
        sent = _send_sms(identifier, code)

    expose = (not sent) or bool(
        getattr(settings, "OTP_EXPOSE_CODE_FOR_TESTING", False))
    return True, (code if expose else None)


def verify_otp(request, code, purpose):
    """Проверить код. Вернуть (ok, error, data). При успехе чистит сессию."""
    data = request.session.get("otp")
    if not data or data.get("purpose") != purpose:
        return False, "Код не запрашивался — начните заново", None
    if time.time() > data.get("expires", 0):
        request.session.pop("otp", None)
        return False, "Код истёк — запросите новый", None
    if data.get("attempts", 0) >= MAX_ATTEMPTS:
        request.session.pop("otp", None)
        return False, "Слишком много попыток — запросите новый код", None

    data["attempts"] = data.get("attempts", 0) + 1
    request.session["otp"] = data

    clean = "".join(ch for ch in str(code or "") if ch.isdigit())
    if not clean or not hmac.compare_digest(
            data["hash"], _hash(f"{purpose}:{data['identifier']}:{clean}")):
        return False, "Неверный код", None

    out = {
        "identifier": data["identifier"],
        "channel": data["channel"],
        "extra": data.get("extra", {}),
    }
    request.session.pop("otp", None)
    return True, None, out
