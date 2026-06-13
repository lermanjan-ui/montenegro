"""
📲 Eskiz SMS client (urllib only) — отправка OTP-кодов.

Авторизация: POST {base}/auth/login (email,password) -> token (живёт ~30 дней).
Токен кэшируется в процессе и пере-запрашивается по истечении/при 401.
Отправка: POST {base}/message/sms/send (Authorization: Bearer <token>).

Если ESKIZ_EMAIL/ESKIZ_PASSWORD пустые — клиент «не настроен»: функции
отправки сообщают configured=False (вызывающий код решает, что делать).

ВАЖНО: в боевом режиме Eskiz отправляет ТОЛЬКО заранее одобренные шаблоны.
Текст задаётся в settings.ESKIZ_OTP_TEMPLATE и ДОЛЖЕН совпадать с одобренным
в кабинете Eskiz ({code} подставляется). До модерации работают только тестовый
отправитель/номера.

Контракт результата отправки (send_sms_result / send_otp_result):
  {
    "ok": bool,            # поставлено ли в очередь провайдера
    "configured": bool,    # настроен ли клиент (есть email/password)
    "status": str,         # статус провайдера ("waiting"/"success"/...) или ""
    "message_id": str,     # id сообщения Eskiz (если есть)
    "error": str,          # текст ошибки при ok=False
    "http_status": int|None,
  }
ВАЖНО: текст сообщения (в нём OTP-код) НИКОГДА не логируется.
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


def _get(path, token=None, timeout=15):
    url = _base() + path
    req = urllib.request.Request(url, method="GET")
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


def _extract_http_error(exc):
    """Достать понятный текст ошибки из тела HTTPError (Eskiz отдаёт JSON)."""
    try:
        body = exc.read().decode("utf-8", "replace")
    except Exception:
        body = ""
    try:
        data = json.loads(body) if body else {}
        return str(data.get("message") or data.get("error") or body[:200] or f"HTTP {exc.code}")
    except Exception:
        return (body[:200] or f"HTTP {getattr(exc, 'code', '?')}")


def _result(ok, configured=True, status="", message_id="", error="", http_status=None):
    return {
        "ok": bool(ok), "configured": bool(configured), "status": status,
        "message_id": message_id, "error": error, "http_status": http_status,
    }


def send_sms_result(phone, text):
    """Отправить произвольный текст с ДЕТАЛЬНЫМ результатом (см. контракт в шапке).
    Никогда не бросает наружу и не логирует сам текст."""
    if not is_configured():
        return _result(False, configured=False,
                       error="Eskiz не настроен (нет ESKIZ_EMAIL/ESKIZ_PASSWORD)")
    mobile = _digits(phone)
    if not mobile:
        return _result(False, error="Пустой или некорректный номер")

    sender = (getattr(settings, "ESKIZ_FROM", "") or "4546").strip()
    payload = {"mobile_phone": mobile, "message": text, "from": sender}

    try:
        token = _get_token()
        try:
            resp = _post("/message/sms/send", payload, token=token)
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                # токен протух — один повторный логин и ретрай
                token = _get_token(force=True)
                resp = _post("/message/sms/send", payload, token=token)
            else:
                return _result(False, error=_extract_http_error(exc), http_status=exc.code)

        resp = resp or {}
        node = resp.get("data") if isinstance(resp.get("data"), dict) else resp
        status = str(node.get("status") or resp.get("status") or "").lower()
        msg_id = str(node.get("id") or resp.get("id") or node.get("message_id") or "")
        ok = status in ("waiting", "success", "ok") or bool(msg_id)
        if ok:
            logger.info("Eskiz queued OTP/SMS (status=%s id=%s)", status or "?", msg_id or "?")
            return _result(True, status=status or "queued", message_id=msg_id, http_status=200)
        err = str(node.get("message") or resp.get("message") or "Неожиданный ответ Eskiz")
        logger.warning("Eskiz send not accepted: status=%s msg=%s", status or "?", err)
        return _result(False, status=status, error=err, http_status=200)

    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        logger.warning("Eskiz network error: %s", reason)
        return _result(False, error=f"Сеть Eskiz недоступна: {reason}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Eskiz send_sms failed")
        return _result(False, error=str(exc) or "Ошибка отправки")


def send_otp_result(phone, code):
    """Отправить OTP по шаблону settings.ESKIZ_OTP_TEMPLATE с детальным результатом.
    Текст (с кодом) не логируем."""
    template = (getattr(settings, "ESKIZ_OTP_TEMPLATE", "") or "Kod: {code}")
    try:
        text = template.format(code=code)
    except Exception:
        text = f"Kod: {code}"
    return send_sms_result(phone, text)


# --- bool-обёртки (обратная совместимость со старым кодом) ---
def send_sms(phone, text):
    return bool(send_sms_result(phone, text).get("ok"))


def send_otp(phone, code):
    return bool(send_otp_result(phone, code).get("ok"))


# --- диагностика (для management-команды check_eskiz) ---
def account_info():
    """GET /auth/user — проверка валидности токена + данные аккаунта."""
    token = _get_token()
    try:
        return _get("/auth/user", token=token)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            token = _get_token(force=True)
            return _get("/auth/user", token=token)
        raise


def get_balance():
    """GET /user/get-limit — остаток/лимит SMS."""
    token = _get_token()
    try:
        return _get("/user/get-limit", token=token)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            token = _get_token(force=True)
            return _get("/user/get-limit", token=token)
        raise
