"""
Сид модулей и трёх базовых тарифов. Идемпотентно (get_or_create).
Модули берутся из разделов системы (UserProfile.SECTION_CHOICES).

    python manage.py seed_billing
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from foodcost.models import UserProfile
from foodcost.models_billing import Module, Plan

# Наборы модулей по тарифам (коды = разделы). Правится под бизнес позже.
START = ["orders", "all_orders", "customers", "dishes", "techcards",
         "settings", "employees", "shifts", "shift_handover"]
BUSINESS = START + ["products", "preparations", "packaging", "writeoffs",
                    "order_analytics", "suppliers", "purchases", "inventory",
                    "stock", "transfers", "schedule"]
# PRO = все модули (ниже)

# Модули, которые имеет смысл продавать и отдельно.
ADDON_CODES = {"order_analytics", "writeoff_analytics", "finance", "site"}


class Command(BaseCommand):
    help = "Создать модули (из разделов) и 3 базовых тарифа."

    @transaction.atomic
    def handle(self, *args, **o):
        # 1) модули из разделов
        code_to_module = {}
        for i, (code, name) in enumerate(UserProfile.SECTION_CHOICES):
            m, _ = Module.objects.get_or_create(
                code=code,
                defaults={"name": name, "sort_order": i,
                          "is_addon": code in ADDON_CODES},
            )
            code_to_module[code] = m
        all_codes = list(code_to_module.keys())

        # 2) тарифы
        plans = [
            ("start", "Старт", 0, START),
            ("business", "Бизнес", 0, BUSINESS),
            ("pro", "Про", 0, all_codes),
        ]
        for order, (code, name, price, codes) in enumerate(plans):
            plan, _ = Plan.objects.get_or_create(
                code=code, defaults={"name": name, "monthly_price": price, "sort_order": order})
            mods = [code_to_module[c] for c in codes if c in code_to_module]
            plan.modules.set(mods)

        self.stdout.write(self.style.SUCCESS(
            f"Готово: модулей {len(all_codes)}, тарифов 3 (Старт/Бизнес/Про)."))
