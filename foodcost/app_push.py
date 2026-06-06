"""
🔔 Push: регистрация токена устройства (FCM) за клиентом.

Под-шаг 3a — хранение токенов. Сама отправка push (FCM) — в push_fcm.py
(под-шаг 3b), он подключается после настройки Firebase.

Эндпоинты (нужен Bearer-токен):
  POST /api/app/push/register    { "device_token": "...", "platform": "ios|android|web" }
  POST /api/app/push/unregister  { "device_token": "..." }
"""

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import DeviceToken
from .public_api import api_success, api_error, _parse_json_body
from .app_auth import _authenticate


def _extract_token(payload):
    return str(payload.get("device_token") or payload.get("token") or "").strip()


@csrf_exempt
@require_POST
def push_register(request):
    customer, _row, err = _authenticate(request)
    if err:
        return err
    payload, perr = _parse_json_body(request)
    if perr:
        return perr

    token = _extract_token(payload)
    if not token:
        return api_error("INVALID_REQUEST", "Нет device_token", status=400)
    platform = str(payload.get("platform") or "")[:20]

    # update_or_create по токену: один токен = одно устройство; при смене
    # аккаунта на том же устройстве токен перепривяжется к новому клиенту.
    DeviceToken.objects.update_or_create(
        token=token[:255],
        defaults={"customer": customer, "platform": platform, "is_active": True},
    )
    return api_success({"ok": True})


@csrf_exempt
@require_POST
def push_unregister(request):
    customer, _row, err = _authenticate(request)
    if err:
        return err
    payload, perr = _parse_json_body(request)
    if perr:
        return perr

    token = _extract_token(payload)
    if not token:
        return api_error("INVALID_REQUEST", "Нет device_token", status=400)

    DeviceToken.objects.filter(
        customer=customer, token=token[:255]
    ).update(is_active=False)
    return api_success({"ok": True})
