"""Ежедневный отчёт по заказам в Telegram.

Запуск (Render Cron Job в 00:30 по Ташкенту):
    python manage.py daily_order_report
Расписание cron (UTC):  30 19 * * *   (= 00:30 Asia/Tashkent, UTC+5)

По умолчанию считает за ВЧЕРАШНИЙ день (тот, что завершился к 00:30).
Можно явно: --date YYYY-MM-DD  или  --today.

Отправляет ботом TELEGRAM_BOT_TOKEN в чат TELEGRAM_CHAT_ID (из настроек/ENV).
HTTP — через urllib (без сторонних зависимостей).
"""

import datetime as dt
import urllib.request
import urllib.parse
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from foodcost.models import Order


def _fmt_money(value):
    """Сумма без копеек, разряды через пробел: 1234567 -> '1 234 567'."""
    try:
        n = int(round(Decimal(value or 0)))
    except Exception:
        n = 0
    return f"{n:,}".replace(",", " ")


def _classify_source(name):
    n = (name or "").strip().lower()
    if "яндекс" in n or "yandex" in n:
        return "yandex"
    if "uzum" in n or "узум" in n:
        return "uzum"
    if "приложение" in n or n == "app":
        return "app"
    if "сайт" in n or "site" in n or "website" in n:
        return "site"
    return "other"


def _send_telegram(text):
    token = (getattr(settings, "TELEGRAM_BOT_TOKEN", "") or "").strip()
    chat_id = (getattr(settings, "TELEGRAM_CHAT_ID", "") or "").strip()
    if not token or not chat_id:
        return False, "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID не заданы"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }).encode("utf-8")
    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


class Command(BaseCommand):
    help = "Ежедневный отчёт по заказам в Telegram (итоги дня)."

    def add_arguments(self, parser):
        parser.add_argument("--date", help="Дата отчёта YYYY-MM-DD (по умолчанию вчера).")
        parser.add_argument("--today", action="store_true", help="Считать за сегодня.")
        parser.add_argument(
            "--print", action="store_true",
            help="Только вывести текст, не отправлять в Telegram.",
        )

    def handle(self, *args, **opts):
        # ---- целевой день (календарный, по Asia/Tashkent) ----
        if opts.get("date"):
            target = dt.date.fromisoformat(opts["date"])
        elif opts.get("today"):
            target = timezone.localdate()
        else:
            target = timezone.localdate() - dt.timedelta(days=1)

        tz = timezone.get_current_timezone()
        start = timezone.make_aware(dt.datetime.combine(target, dt.time.min), tz)
        end = start + dt.timedelta(days=1)

        qs = Order.objects.filter(created_at__gte=start, created_at__lt=end)

        cancelled_q = Q(status=Order.STATUS_CANCELLED) | Q(is_cancelled=True)
        cancelled_qs = qs.filter(cancelled_q)
        active_qs = qs.exclude(cancelled_q)

        total = qs.count()
        cancelled_count = cancelled_qs.count()

        # ---- источники (по всем заказам дня) ----
        buckets = {"yandex": 0, "uzum": 0, "site": 0, "app": 0, "other": 0}
        for src_name in qs.values_list("source__name", flat=True):
            buckets[_classify_source(src_name)] += 1

        # ---- суммы ----
        sum_active = sum((o.total_amount or 0) for o in active_qs)
        sum_cancelled = sum((o.total_amount or 0) for o in cancelled_qs)

        # ---- оплаты (без отказов) ----
        cash = Decimal("0")
        other_pay = Decimal("0")
        for o in active_qs.select_related("payment_method"):
            amt = o.total_amount or 0
            if o.payment_method and o.payment_method.is_cash:
                cash += amt
            else:
                other_pay += amt

        # ---- текст ----
        lines = [
            f"📊 <b>Итоги за {target.strftime('%d.%m.%Y')}</b>",
            "",
            f"Заказов всего: <b>{total}</b>",
            "Из них:",
            f"  Яндекс: {buckets['yandex']}",
            f"  Uzum: {buckets['uzum']}",
            f"  Сайт: {buckets['site']}",
            f"  Приложение: {buckets['app']}",
        ]
        if buckets["other"]:
            lines.append(f"  Прочее: {buckets['other']}")
        lines += [
            f"Отказов из них: {cancelled_count}",
            "",
            f"Сумма заказов за день (без отказов): <b>{_fmt_money(sum_active)}</b> сум",
            f"Сумма отказов: {_fmt_money(sum_cancelled)} сум",
            "",
            f"Наличные в кассе: <b>{_fmt_money(cash)}</b> сум",
            f"Остальные оплаты: <b>{_fmt_money(other_pay)}</b> сум",
        ]
        text = "\n".join(lines)

        if opts.get("print"):
            self.stdout.write(text)
            return

        ok, info = _send_telegram(text)
        if ok:
            self.stdout.write(self.style.SUCCESS(f"Отчёт за {target} отправлен в Telegram."))
        else:
            self.stdout.write(self.style.ERROR(f"Не отправлено: {info}"))
            self.stdout.write(text)
