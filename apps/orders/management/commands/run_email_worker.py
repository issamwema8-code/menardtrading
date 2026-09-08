import time
import signal
import sys
from django.core.management.base import BaseCommand
from apps.orders.email_worker import run_worker_cycle, process_outbound_queue, process_inbound_queue


class Command(BaseCommand):
    help = "Runs the autonomous background email worker for inbound processing and outbound delivery queue."

    def add_arguments(self, parser):
        parser.add_argument(
            '--daemon',
            action='store_true',
            help='Runs worker continuously as a daemon service with interval sleep'
        )
        parser.add_argument(
            '--interval',
            type=int,
            default=5,
            help='Sleep interval in seconds between cycles in daemon mode (default: 5s)'
        )
        parser.add_argument(
            '--once',
            action='store_true',
            help='Runs one single cycle and exits (ideal for cron jobs)'
        )

    def handle(self, *args, **options):
        is_daemon = options['daemon']
        interval = options['interval']

        self.stdout.write(self.style.SUCCESS("=" * 70))
        self.stdout.write(self.style.SUCCESS(" MENARD TRADING CC — AUTONOMOUS EMAIL QUEUE & INBOUND WORKER"))
        self.stdout.write(self.style.SUCCESS("=" * 70))
        self.stdout.write(f"Mode: {'Continuous Daemon' if is_daemon else 'One-shot Execution'}")
        if is_daemon:
            self.stdout.write(f"Poll Interval: {interval} seconds (Press Ctrl+C to terminate)")

        stop_requested = False

        def handle_signal(sig, frame):
            nonlocal stop_requested
            self.stdout.write(self.style.WARNING("\nTermination signal received. Shutting down gracefully..."))
            stop_requested = True

        signal.signal(signal.SIGINT, handle_signal)
        signal.signal(signal.SIGTERM, handle_signal)

        while not stop_requested:
            try:
                res = run_worker_cycle()
                inbound_count = res['inbound']['processed']
                outbound_sent = res['outbound']['sent']
                outbound_retrying = res['outbound']['retrying']
                outbound_failed = res['outbound']['failed']

                if inbound_count > 0 or outbound_sent > 0 or outbound_retrying > 0 or outbound_failed > 0:
                    self.stdout.write(
                        f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                        f"Inbound: {inbound_count} processed | "
                        f"Outbound: {outbound_sent} sent, {outbound_retrying} retrying, {outbound_failed} failed"
                    )

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error during worker cycle: {e}"))

            if not is_daemon:
                break

            # Sleep in 1-second chunks for responsive SIGINT
            for _ in range(interval):
                if stop_requested:
                    break
                time.sleep(1)

        self.stdout.write(self.style.SUCCESS("Worker stopped cleanly."))
