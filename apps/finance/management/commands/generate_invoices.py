from django.core.management.base import BaseCommand

from apps.finance.services import generate_draft_invoices, issue_invoice


class Command(BaseCommand):
    help = ("Create one DRAFT invoice per family for all unpaid, not-yet-invoiced charges. "
            "Drafts are reviewed and issued by finance; pass --issue to issue them straight away.")

    def add_arguments(self, parser):
        parser.add_argument("--issue", action="store_true", help="Issue the drafts immediately.")

    def handle(self, *args, issue, **options):
        drafts = generate_draft_invoices()
        for draft in drafts:
            if issue:
                issue_invoice(draft)
        state = "issued" if issue else "draft"
        self.stdout.write(self.style.SUCCESS(f"Created {len(drafts)} {state} family invoice(s)."))
