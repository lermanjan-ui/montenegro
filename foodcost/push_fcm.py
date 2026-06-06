"""
🔔 Отправка push через Firebase Cloud Messaging (firebase-admin, FCM HTTP v1).

Инициализация — из сервис-аккаунта:
  - settings.FIREBASE_CREDENTIALS_FILE = путь к JSON (Render Secret File,
    напр. /etc/secrets/firebase.json), ИЛИ
  - переменная окружения GOOGLE_APPLICATION_CREDENTIALS с тем же путём.
Если ничего не задано или firebase-admin не установлен — push молча
выключается (никогда не ломает сохранение заказа).

Точка входа: notify_order_status(order) — вызывается из сигнала в models.py
при смене статуса заказа.
"""

import logging
import os

from django.conf import settings

logger = logging.getLogger(__name__)

_initialized = False

# Текст уведомления по новому статусу заказа. {num} = номер заказа.
_STATUS_MESSAGES = {
    "new": ("Заказ принят ✅", "Мы приняли ваш заказ {num}."),
    "delivery": ("Заказ в пути 🚗", "Курьер уже везёт ваш заказ {num}."),
}


def is_configured():
    cred_file = (getattr(settings, "FIREBASE_CREDENTIALS_FILE", "") or "").strip()
    return bool(cred_file or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))


def _ensure_init():
    """Лениво инициализирует firebase-admin один раз. Возвращает True/False."""
    global _initialized
    if _initialized:
        return True
    try:
        import firebase_admin
        from firebase_admin import credentials
    except Exception:
        logger.warning("firebase-admin не установлен — push отключён.")
        return False

    try:
        if firebase_admin._apps:  # уже инициализировано где-то ещё
            _initialized = True
            return True
        cred_file = (getattr(settings, "FIREBASE_CREDENTIALS_FILE", "") or "").strip()
        if cred_file:
            firebase_admin.initialize_app(credentials.Certificate(cred_file))
        elif os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            firebase_admin.initialize_app()  # ADC из переменной окружения
        else:
            logger.warning("Firebase не настроен (нет FIREBASE_CREDENTIALS_FILE).")
            return False
        _initialized = True
        return True
    except Exception:
        logger.exception("Не удалось инициализировать firebase-admin.")
        return False


def _maybe_deactivate(token, err):
    """Выключаем токен, если FCM сообщил, что он недействителен."""
    msg = str(err or "").lower()
    if any(s in msg for s in ("not-registered", "unregistered", "invalid-argument", "invalid registration")):
        try:
            from .models import DeviceToken
            DeviceToken.objects.filter(token=token).update(is_active=False)
        except Exception:
            pass


def send_to_customer(customer, title, body, data=None):
    """Отправить push всем активным устройствам клиента. Не бросает наружу."""
    if not getattr(settings, "FCM_ENABLED", True):
        return
    if customer is None:
        return
    if not _ensure_init():
        return

    from .models import DeviceToken
    from firebase_admin import messaging

    tokens = list(
        DeviceToken.objects.filter(customer=customer, is_active=True)
        .values_list("token", flat=True)
    )
    if not tokens:
        return

    payload = {k: str(v) for k, v in (data or {}).items()}
    message = messaging.MulticastMessage(
        tokens=tokens,
        notification=messaging.Notification(title=title, body=body),
        data=payload,
    )
    try:
        resp = messaging.send_each_for_multicast(message)
    except Exception:
        logger.exception("FCM: отправка не удалась.")
        return

    if getattr(resp, "failure_count", 0):
        for idx, r in enumerate(resp.responses):
            if not r.success:
                _maybe_deactivate(tokens[idx], getattr(r, "exception", None))


def notify_order_status(order):
    """Push клиенту при смене статуса заказа (new / delivery)."""
    status = getattr(order, "status", "") or ""
    tpl = _STATUS_MESSAGES.get(status)
    if not tpl:
        return
    num = getattr(order, "public_order_number", "") or str(getattr(order, "id", ""))
    title, body_tpl = tpl
    send_to_customer(
        getattr(order, "customer", None),
        title,
        body_tpl.format(num=num),
        data={"order_number": num, "status": status},
    )
