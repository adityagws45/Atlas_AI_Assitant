from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Run APScheduler worker (Milestone 7)."

    def handle(self, *args, **options):
        raise NotImplementedError("Scheduler is implemented in Milestone 7.")
