from django.core.management.base import BaseCommand

from apps.academy.models import Program
from apps.finance.models import ChargeItem, FeeType

PROGRAMS = [
    ("wushu-taolu", "Wushu Taolu"),
    ("sanda", "Sanda"),
    ("taiji", "Taiji"),
    ("changquan", "Changquan"),
    ("nanquan", "Nanquan"),
    ("weapons", "Weapons"),
]

# Price-list placeholders; set the real prices in the admin (Finance → Charge items).
CHARGE_ITEMS = [
    ("Registration fee", FeeType.REGISTRATION),
    ("Uniform (T-shirt)", FeeType.UNIFORM),
    ("Training uniform (full set)", FeeType.UNIFORM),
    ("Jian (straight sword)", FeeType.WEAPON),
    ("Dao (broadsword)", FeeType.WEAPON),
    ("Gun (staff)", FeeType.WEAPON),
    ("Qiang (spear)", FeeType.WEAPON),
    ("Nandao (southern broadsword)", FeeType.WEAPON),
]


class Command(BaseCommand):
    help = "Create the academy's programs and a starter price list (safe to re-run)."

    def handle(self, *args, **options):
        for code, name in PROGRAMS:
            Program.objects.get_or_create(code=code, defaults={"name": name})
        for name, fee_type in CHARGE_ITEMS:
            ChargeItem.objects.get_or_create(name=name, defaults={"fee_type": fee_type, "default_amount": 0})
        self.stdout.write(self.style.SUCCESS("Programs and price list ready."))
