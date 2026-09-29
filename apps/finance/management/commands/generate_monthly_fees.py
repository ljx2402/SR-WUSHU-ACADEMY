from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.finance.services import generate_tuition_charges


class Command(BaseCommand):
    help = "Bill monthly / per-session class fees for a month (default: current month). Safe to re-run."

    def add_arguments(self, parser):
        parser.add_argument("--year", type=int)
        parser.add_argument("--month", type=int)

    def handle(self, *args, year, month, **options):
        today = timezone.localdate()
        year, month = year or today.year, month or today.month
        created = generate_tuition_charges(year, month)
        self.stdout.write(self.style.SUCCESS(f"Created {len(created)} charge(s) for {year}-{month:02d}."))
