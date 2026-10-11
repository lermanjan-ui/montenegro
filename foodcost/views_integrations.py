"""
Экран настроек интеграций арендатора (ERP → Настройки → Интеграции).

Позволяет завести Telegram / Payme / Click креды КОНКРЕТНОГО арендатора через
интерфейс, без правки БД. Eskiz (SMS) сюда НЕ входит — это общий сервис платформы.

Секреты (ключи/токены): пустое поле в форме = НЕ менять (оставить что было),
чтобы не затирать уже заданный секрет при обычном сохранении.
"""
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render

from .models import UserProfile
from .models_integrations import TenantIntegrationSettings
from .views import get_country, require_section_access, user_can_edit

# Обычные поля — затираются значением из формы.
_PLAIN_FIELDS = [
    "payme_merchant_id", "payme_checkout_url", "payme_account_field",
    "payme_success_url", "payme_fail_url",
    "click_service_id", "click_merchant_id", "click_merchant_user_id",
    "click_success_url", "click_fail_url",
    "telegram_chat_id",
]
# Секреты — пустое поле в форме НЕ меняет сохранённое значение.
_SECRET_FIELDS = ["payme_secret_key", "click_secret_key", "telegram_bot_token"]


@login_required(login_url="/login/")
def integration_settings_page(request, country_slug):
    country = get_country(country_slug, request.user)

    access_error = require_section_access(request.user, UserProfile.SECTION_SETTINGS)
    if access_error:
        return access_error

    s, _ = TenantIntegrationSettings.objects.get_or_create(country=country)

    if request.method == "POST":
        if not user_can_edit(request.user):
            return HttpResponseForbidden("Недостаточно прав для изменения интеграций")
        for f in _PLAIN_FIELDS:
            setattr(s, f, (request.POST.get(f) or "").strip())
        for f in _SECRET_FIELDS:
            val = (request.POST.get(f) or "").strip()
            if val:  # пусто — не трогаем уже сохранённый секрет
                setattr(s, f, val)
        s.save()
        return redirect(request.path + "?saved=1")

    payme_cb = request.build_absolute_uri(f"/api/payments/payme/{country.slug}/callback/")
    click_cb = request.build_absolute_uri(f"/api/payments/click/{country.slug}/callback/")

    return render(request, "foodcost/integration_settings.html", {
        "country": country,
        "s": s,
        "saved": request.GET.get("saved") == "1",
        "payme_callback_url": payme_cb,
        "click_callback_url": click_cb,
        "secret_set": {f: bool(getattr(s, f)) for f in _SECRET_FIELDS},
    })
