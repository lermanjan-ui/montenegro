"""
📲 Eskiz SMS client (urllib only) — отправка OTP-кодов.

Авторизация: POST {base}/auth/login (email,password) -> token (живёт ~30 дней).
Токен кэшируется в процессе и пере-запрашивается по истечении/при 401.
Отправка: POST {base}/message/sms/send (Authorization: Bearer <token>).

Если ESKIZ_EMAIL/ESKIZ_PASSWORD пустые — клиент «не настроен»: send_otp()
ничего не шлёт и возвращает False (вызывающий код уходит в тестовый режим).

ВАЖНО: в боевом режиме Eskiz отправляет ТОЛЬКО заранее одобренные шаблоны.
Текст задаётся в settings.ESKIZ_OTP_TEMPLATE и ДОЛЖЕН совпадать с одобренным
в кабинете Eskiz ({code} подставляется). До модерации работают только тестовый
отправитель/номера.
"""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

# Кэш токена Eskiz в пределах процесса. Eskiz-токен живёт ~30 дней; пере-логин
# заметно раньше и на 401.
_token_cache = {"token": "", "fetched_at": 0.0}
_TOKEN_REFRESH_AFTER = 25 * 24 * 3600  # 25 дней


def is_configured():
    return bool(
        (getattr(settings, "ESKIZ_EMAIL", "") or "").strip()
        and (getattr(settings, "ESKIZ_PASSWORD", "") or "").strip()
    )


def _base():
    return (getattr(settings, "ESKIZ_BASE_URL", "") or "https://notify.eskiz.uz/api").rstrip("/")


def _post(path, data, token=None, timeout=15):
    url = _base() + path
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", "replace")
    return json.loads(raw) if raw else {}


def _login():
    email = (getattr(settings, "ESKIZ_EMAIL", "") or "").strip()
    password = (getattr(settings, "ESKIZ_PASSWORD", "") or "").strip()
    resp = _post("/auth/login", {"email": email, "password": password})
    token = ((resp or {}).get("data") or {}).get("token") or ""
    if not token:
        raise RuntimeError("Eskiz login failed: no token in response")
    _token_cache["token"] = token
    _token_cache["fetched_at"] = time.time()
    return token


def _get_token(force=False):
    if (
        not force
        and _token_cache["token"]
        and (time.time() - _token_cache["fetched_at"]) < _TOKEN_REFRESH_AFTER
    ):
        return _token_cache["token"]
    return _login()


def _digits(phone):
    return "".join(ch for ch in str(phone or "") if ch.isdigit())


def send_sms(phone, text):
    """Отправить произвольный текст. Возвращает True при успехе, иначе False.
    Никогда не бросает наружу — логирует и возвращает False."""
    if not is_configured():
        return False
    mobile = _digits(phone)
    if not mobile:
        return False
    sender = (getattr(settings, "ESKIZ_FROM", "") or "4546").strip()
    payload = {"mobile_phone": mobile, "message": text, "from": sender}
    try:
        token = _get_token()
        try:
            _post("/message/sms/send", payload, token=token)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                # токен протух — один повторный логин
                token = _get_token(force=True)
                _post("/message/sms/send", payload, token=token)
            else:
                raise
        return True
    except Exception:
        logger.exception("Eskiz send_sms failed")
        return False


def send_otp(phone, code):
    """Отправить OTP по шаблону settings.ESKIZ_OTP_TEMPLATE. Текст никогда не
    логируем целиком (в нём код)."""
    template = (getattr(settings, "ESKIZ_OTP_TEMPLATE", "") or "Kod: {code}")
    try:
        text = template.format(code=code)
    except Exception:
        text = f"Kod: {code}"
    return send_sms(phone, text)
