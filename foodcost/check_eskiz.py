"""
Диагностика интеграции с Eskiz прямо с сервера.

    python manage.py check_eskiz                 # токен + баланс
    python manage.py check_eskiz +998901234567   # + тестовая SMS на номер
    python manage.py check_eskiz +998... --text "Bu Eskiz dan test"

Показывает: настроен ли клиент, валиден ли токен (логин/refresh), баланс/лимит
и — при указании номера — реальный ответ Eskiz на отправку (status/id/ошибка).
"""

from django.core.management.base import BaseCommand

from foodcost import sms_eskiz


class Command(BaseCommand):
    help = "Проверка интеграции Eskiz: токен, баланс, тестовая отправка SMS."

    def add_arguments(self, parser):
        parser.add_argument("phone", nargs="?", default="", help="Номер для тестовой SMS (+998...)")
        parser.add_argument("--text", default="Bu Eskiz dan test", help="Текст тестовой SMS")

    def handle(self, *args, **opts):
        self.stdout.write(f"configured: {sms_eskiz.is_configured()}")
        if not sms_eskiz.is_configured():
            self.stdout.write(self.style.ERROR(
                "ESKIZ_EMAIL / ESKIZ_PASSWORD не заданы — клиент не настроен."
            ))
            return

        # 1) токен / аккаунт
        try:
            info = sms_eskiz.account_info()
            self.stdout.write(self.style.SUCCESS("token: OK (логин/валидность подтверждены)"))
            self.stdout.write(f"account: {info}")
        except Exception as e:  # noqa: BLE001
            self.stdout.write(self.style.ERROR(f"token/login ERROR: {e}"))
            return

        # 2) баланс / лимит
        try:
            bal = sms_eskiz.get_balance()
            self.stdout.write(f"balance/limit: {bal}")
        except Exception as e:  # noqa: BLE001
            self.stdout.write(self.style.WARNING(f"balance ERROR: {e}"))

        # 3) тестовая отправка (если указан номер)
        phone = (opts.get("phone") or "").strip()
        if not phone:
            self.stdout.write("Тестовая отправка пропущена (укажите номер аргументом).")
            return

        res = sms_eskiz.send_sms_result(phone, opts["text"])
        self.stdout.write(f"send result: {res}")
        if res.get("ok"):
            self.stdout.write(self.style.SUCCESS(
                f"SMS поставлена в очередь (status={res.get('status')}, id={res.get('message_id')})"
            ))
        else:
            self.stdout.write(self.style.ERROR(f"отправка не удалась: {res.get('error')}"))
