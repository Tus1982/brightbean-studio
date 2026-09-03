"""Management command to run the publishing engine worker.

Usage:
    python manage.py run_publisher
    python manage.py run_publisher --interval 15
"""

import signal
import time

from django.core.management.base import BaseCommand

from apps.publisher.engine import PublishEngine


class Command(BaseCommand):
    help = "Run the publishing engine worker that polls for scheduled posts."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=int,
            default=15,
            help="Poll interval in seconds (default: 15).",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single poll cycle and exit.",
        )
        parser.add_argument(
            "--reconcile-every",
            type=int,
            default=600,
            help=(
                "Seconds between reconciliation sweeps of posts waiting on a creator "
                "(TikTok drafts). 0 disables it. Default: 600."
            ),
        )

    def handle(self, *args, **options):
        interval = options["interval"]
        run_once = options["once"]
        reconcile_every = options["reconcile_every"]
        engine = PublishEngine()
        # Sweep once on boot, then on the cadence: a post parked in
        # "In TikTok drafts" is waiting on a person, and the only thing that can
        # settle it is asking the platform. This lives here as well as in the
        # background queue because the publisher is the process that is always
        # running — if the generic queue worker is down, a video the creator has
        # already published would otherwise sit amber forever.
        next_reconcile = 0.0

        self.stdout.write(self.style.SUCCESS(f"Publishing engine started (poll interval: {interval}s)"))

        # Handle graceful shutdown
        running = True

        def signal_handler(signum, frame):
            nonlocal running
            self.stdout.write(self.style.WARNING("\nShutting down publisher..."))
            running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        while running:
            try:
                published = engine.poll_and_publish()
                if published:
                    self.stdout.write(self.style.SUCCESS(f"Published {published} post(s)"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"Publisher error: {e}"))

            if reconcile_every and time.monotonic() >= next_reconcile:
                next_reconcile = time.monotonic() + reconcile_every
                try:
                    moved = engine.reconcile_awaiting_creator()
                    if moved:
                        self.stdout.write(self.style.SUCCESS(f"Settled {moved} post(s) waiting on a creator"))
                except Exception as e:
                    self.stderr.write(self.style.ERROR(f"Reconcile error: {e}"))

            if run_once:
                break

            time.sleep(interval)

        self.stdout.write(self.style.SUCCESS("Publisher stopped."))
