"""Пересчёт авто-списания (склад) за все даты с заказами.

Перебирает все дни, где есть неотменённые заказы, и для каждого вызывает
recompute_sales_for_date — тот же идемпотентный пересчёт, что и кнопка на
странице авто-списания. Удаляет старые движения-продажи за день и создаёт
заново по актуальному правилу (заготовка раскрывается в продукты при нехватке).

Примеры:
  python manage.py recompute_sales_all
  python manage.py recompute_sales_all --country uzbekistan
  python manage.py recompute_sales_all --date-from 2026-05-01 --date-to 2026-06-08
  python manage.py recompute_sales_all --location 2
"""

from datetime import datetime
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db.models.functions import TruncDate

from foodcost.models import Country, Order
from foodcost.views_autowriteoff import recompute_sales_for_date


def _parse(value):
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


class Command(BaseCommand):
    help = "Пересчитать авто-списание склада за все даты с заказами"

    def add_arguments(self, parser):
        parser.add_argument("--country", default="uzbekistan",
                            help="slug страны (по умолчанию uzbekistan)")
        parser.add_argument("--date-from", default=None, help="ГГГГ-ММ-ДД")
        parser.add_argument("--date-to", default=None, help="ГГГГ-ММ-ДД")
        parser.add_argument("--location", type=int, default=None,
                            help="ID склада (по умолчанию все)")

    def handle(self, *args, **opts):
        try:
            country = Country.objects.get(slug=opts["country"])
        except Country.DoesNotExist:
            self.stderr.write(f"Страна '{opts['country']}' не найдена")
            return

        df = _parse(opts.get("date_from"))
        dt = _parse(opts.get("date_to"))
        loc_id = opts.get("location")

        qs = (
            Order.objects
            .filter(country=country, is_cancelled=False)
            .exclude(status=Order.STATUS_CANCELLED)
        )
        if loc_id:
            qs = qs.filter(location_id=loc_id)

        dates = sorted(
            d for d in set(
                qs.annotate(_d=TruncDate("order_date")).values_list("_d", flat=True)
            ) if d
        )
        if df:
            dates = [d for d in dates if d >= df]
        if dt:
            dates = [d for d in dates if d <= dt]

        if not dates:
            self.stdout.write("Нет дат с заказами под заданные условия.")
            return

        self.stdout.write(f"Пересчёт за {len(dates)} дн. ({dates[0]} … {dates[-1]})")

        tot_orders = tot_moves = tot_deleted = 0
        tot_cost = Decimal(0)
        for d in dates:
            s = recompute_sales_for_date(country, d, location_id=loc_id, user=None)
            tot_orders += s["orders"]
            tot_moves += s["movements"]
            tot_deleted += s["deleted"]
            tot_cost += s["total_cost"]
            self.stdout.write(
                f"  {d}: заказов {s['orders']}, движений {s['movements']}, "
                f"удалено старых {s['deleted']}"
            )

        self.stdout.write(self.style.SUCCESS(
            f"Готово. Дней: {len(dates)}, заказов: {tot_orders}, "
            f"создано движений: {tot_moves}, удалено старых: {tot_deleted}, "
            f"себестоимость списаний: {tot_cost:.2f}"
        ))
