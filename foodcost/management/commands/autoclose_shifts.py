"""Автозакрытие смен по графику.

Закрывает смены в статусе «Идёт» (in_progress), у которых истекло плановое
время работы, если сотрудник не закрыл смену сам. Плановая длительность
берётся из графика: planned_hours (приоритет) → плановое время окончания
end_time → default_shift_hours сотрудника.

Запускать по расписанию (Render Cron), например каждые 15 минут:
    python manage.py autoclose_shifts
"""
from datetime import datetime, timedelta, time as _time
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.utils import timezone

from foodcost.models import EmployeeShift


class Command(BaseCommand):
    help = "Автозакрытие смен (in_progress), у которых истекло плановое время по графику"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true",
                            help="Только показать, что было бы закрыто, без сохранения")

    def handle(self, *args, **options):
        dry = options.get("dry_run")
        now = timezone.localtime()
        tz = timezone.get_current_timezone()

        qs = EmployeeShift.objects.filter(
            status=EmployeeShift.STATUS_IN_PROGRESS
        ).select_related("employee")

        closed = 0
        for sh in qs:
            start_t = sh.start_time or _time(0, 0)
            start_dt = datetime.combine(sh.shift_date, start_t)
            if timezone.is_naive(start_dt):
                start_dt = timezone.make_aware(start_dt, tz)

            planned = sh.planned_hours or (sh.employee.default_shift_hours if sh.employee else None) or Decimal(0)

            if planned and planned > 0:
                end_dt = start_dt + timedelta(hours=float(planned))
            elif sh.end_time:
                end_dt = datetime.combine(sh.shift_date, sh.end_time)
                if timezone.is_naive(end_dt):
                    end_dt = timezone.make_aware(end_dt, tz)
                if end_dt <= start_dt:
                    end_dt += timedelta(days=1)  # ночная смена через полночь
            else:
                end_dt = start_dt + timedelta(hours=12)

            if now < end_dt:
                continue  # плановое время ещё не вышло

            # длительность смены = от старта до планового конца
            hours = Decimal(str(round((end_dt - start_dt).total_seconds() / 3600.0, 2)))
            if sh.break_minutes:
                hours = hours - Decimal(sh.break_minutes) / Decimal(60)
            if hours < 0:
                hours = Decimal(0)

            label = f"{sh.employee.name if sh.employee else '—'} / {sh.shift_date}"
            if dry:
                self.stdout.write(f"[dry] закрыл бы: {label} → {hours} ч (план до {end_dt:%d.%m %H:%M})")
                closed += 1
                continue

            sh.end_time = end_dt.timetz().replace(second=0, microsecond=0)
            sh.hours = hours
            sh.actual_hours = hours
            sh.status = EmployeeShift.STATUS_DONE
            sh.comment = (sh.comment + " " if sh.comment else "") + "[авто-закрытие по графику]"
            sh.save(update_fields=["end_time", "hours", "actual_hours", "status", "comment"])
            closed += 1
            self.stdout.write(f"закрыта смена: {label} → {hours} ч")

        self.stdout.write(self.style.SUCCESS(f"Готово. Закрыто смен: {closed}"))
