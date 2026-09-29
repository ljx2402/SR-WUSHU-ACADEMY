import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.academy.models import TrainingClass
from apps.academy.services import generate_sessions


class Command(BaseCommand):
    help = "Create training sessions from class timetables for the next N days (default 14). Safe to re-run."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=14)
        parser.add_argument("--start", type=datetime.date.fromisoformat)

    def handle(self, *args, days, start, **options):
        start = start or timezone.localdate()
        end = start + datetime.timedelta(days=days - 1)
        total = 0
        for training_class in TrainingClass.objects.filter(is_active=True):
            total += len(generate_sessions(training_class, start, end))
        self.stdout.write(self.style.SUCCESS(f"Created {total} session(s) for {start} – {end}."))
