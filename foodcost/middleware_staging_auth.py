# -*- coding: utf-8 -*-
"""HTTP Basic Auth для закрытия staging от посторонних.

Активен ТОЛЬКО когда задана переменная окружения STAGING_BASIC_AUTH="логин:пароль".
Если переменной нет (как на боевом) — middleware пропускает все запросы без изменений,
поэтому файл безопасно держать в любой ветке.
"""
import base64
import os

from django.http import HttpResponse


class StagingBasicAuthMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        self.cred = os.environ.get("STAGING_BASIC_AUTH", "").strip()

    def __call__(self, request):
        if self.cred:
            header = request.META.get("HTTP_AUTHORIZATION", "")
            ok = False
            if header.startswith("Basic "):
                try:
                    decoded = base64.b64decode(header[6:]).decode("utf-8")
                    ok = decoded == self.cred
                except Exception:
                    ok = False
            if not ok:
                resp = HttpResponse("Staging — authorization required", status=401)
                resp["WWW-Authenticate"] = 'Basic realm="staging"'
                return resp
        return self.get_response(request)
