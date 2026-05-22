from decimal import Decimal

from django.core.management.base import BaseCommand

from foodcost.models import Order


class Command(BaseCommand):
    help = "Recalculate orders financial fields"

    def handle(self, *args, **options):

        updated = 0

        orders = (
            Order.objects
            .select_related("source")
            .all()
        )

        for order in orders:

            food_total = (
                order.subtotal_amount
                - order.discount_amount
            )

            customer_delivery_amount = Decimal("0")

            if order.source and order.source.name.lower() == "сайт":
                if food_total > 0 and food_total < Decimal("150000"):
                    customer_delivery_amount = Decimal("15000")

            total_amount = food_total + customer_delivery_amount

            commission_amount = Decimal("0")

            if order.source:
                commission_amount = (
                    total_amount
                    * order.source.commission_percent
                    / Decimal("100")
                )

            net_revenue = total_amount - commission_amount

            order.customer_delivery_amount = customer_delivery_amount
            order.total_amount = total_amount
            order.commission_amount = commission_amount
            order.net_revenue = net_revenue

            order.save(
                update_fields=[
                    "customer_delivery_amount",
                    "total_amount",
                    "commission_amount",
                    "net_revenue",
                ]
            )

            updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Recalculated {updated} orders"
            )
        )