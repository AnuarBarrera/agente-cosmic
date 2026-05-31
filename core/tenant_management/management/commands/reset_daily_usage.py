from django.core.management.base import BaseCommand
from django.utils import timezone

class Command(BaseCommand):
    help = 'Resets the daily usage counters for all tenants. Note: The current implementation calculates usage on the fly, so this command is a placeholder for future cleanup tasks.'

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS('Starting daily usage reset process...'))
        # The current implementation calculates daily and monthly usage on the fly
        # based on timestamps in the UsageRecord table. Therefore, no explicit reset is needed.
        # This command can be used in the future to archive old records or perform other
        # maintenance tasks.
        self.stdout.write(self.style.SUCCESS('Daily usage is calculated dynamically. No reset needed.'))
        self.stdout.write(self.style.SUCCESS('Process finished successfully.'))
