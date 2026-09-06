from decimal import Decimal

from django.core.management.base import BaseCommand

from userapp.constants import DEPARTMENTS
from userapp.models import Fee

DEFAULT_AMOUNTS = {
    100: Decimal("25000"),
    200: Decimal("22000"),
    300: Decimal("20000"),
    400: Decimal("20000"),
    500: Decimal("20000"),
}


class Command(BaseCommand):
    help = "Seed default fees for every department and level (existing fees are left untouched)."

    def handle(self, *args, **options):
        created = 0
        for code in DEPARTMENTS:
            for level, amount in DEFAULT_AMOUNTS.items():
                fee, was_created = Fee.objects.get_or_create(
                    department=code,
                    level=level,
                    defaults={"amount": amount},
                )
                if was_created:
                    created += 1
        self.stdout.write(self.style.SUCCESS(f"Seeded {created} fee(s). Total fees: {Fee.objects.count()}"))
