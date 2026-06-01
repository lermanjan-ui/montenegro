"""
Ночное автосписание продаж со склада.

Запускается планировщиком (Render Cron Job) раз в сутки. По умолчанию
пересчитывает автосписание за ВЧЕРА по ВСЕМ странам и всем складам.

Использует тот же движок, что и страница ручного пересчёта
(foodcost.views_autowriteoff.recompute_sales_for_date) — то есть результат
идентичен ручному «Пересчитать за дату», и пересчёт идемпотентен
(повторный запуск безопасно заменяет прежние движения-продажи за дату).

Примеры:
    python manage.py autowriteoff_yesterday
    python manage.py autowriteoff_yesterday --date 2026-05-31
    python manage.py autowriteoff_yesterday --country uzbekistan
    python manage.py autowriteoff_yesterday --date 2026-05-31 --country uzbekistan
"""

from datetime import datetime, timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from foodcost.models import Country
from foodcost.views_autowriteoff import recompute_sales_for_date


class Command(BaseCommand):
    help = "Пересчитать автосписание продаж со склада (по умолчанию — за вчера, по всем странам)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            dest="date",
            default=None,
            help="Дата в формате ГГГГ-ММ-ДД. По умолчанию — вчера.",
        )
        parser.add_argument(
            "--country",
            dest="country",
            default=None,
            help="Slug страны. По умолчанию — все страны.",
        )

    def handle(self, *args, **options):
        # ----- дата -----
        raw_date = options.get("date")
        if raw_date:
            try:
                date_obj = datetime.strptime(raw_date, "%Y-%m-%d").date()
            except ValueError:
                self.stderr.write(self.style.ERROR(f"Неверная дата: {raw_date}. Формат ГГГГ-ММ-ДД."))
                return
        else:
            date_obj = timezone.now().date() - timedelta(days=1)

        # ----- страны -----
        countries = Country.objects.all()
        slug = options.get("country")
        if slug:
            countries = countries.filter(slug=slug)
            if not countries.exists():
                self.stderr.write(self.style.ERROR(f"Страна не найдена: {slug}"))
                return

        self.stdout.write(
            self.style.NOTICE(f"Автосписание продаж за {date_obj.strftime('%d.%m.%Y')}")
        )

        grand_orders = 0
        grand_moves = 0
        for country in countries:
            try:
                summary = recompute_sales_for_date(country, date_obj, location_id=None, user=None)
            except Exception as exc:  # не валим весь крон из-за одной страны
                self.stderr.write(self.style.ERROR(f"  {country.slug}: ОШИБКА — {exc}"))
                continue

            grand_orders += summary["orders"]
            grand_moves += summary["movements"]
            self.stdout.write(
                f"  {country.slug}: заказов {summary['orders']}, "
                f"движений {summary['movements']}, "
                f"удалено прежних {summary['deleted']}, "
                f"себестоимость {summary['total_cost']:.0f}"
                + (f", пропущено без склада {summary['skipped_no_location']}" if summary["skipped_no_location"] else "")
            )

        self.stdout.write(
            self.style.SUCCESS(
                f"Готово. Итого: заказов {grand_orders}, движений {grand_moves}."
            )
        )
