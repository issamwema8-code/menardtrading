import time
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.orders.imap_service import sync_orders_mailbox


class Command(BaseCommand):
    help = 'Syncs inbound purchase orders from orders@menardtrading.com IMAP mailbox (one-off or continuous 24/7 background daemon)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--daemon',
            action='store_true',
            help='Runs continuously as a 24/7 background daemon polling at specified intervals'
        )
        parser.add_argument(
            '--interval',
            type=int,
            default=30,
            help='Polling interval in seconds when in daemon mode (default: 30s)'
        )

    def handle(self, *args, **options):
        is_daemon = options.get('daemon')
        interval = options.get('interval', 30)

        if not is_daemon:
            self.run_sync()
            return

        self.stdout.write(self.style.SUCCESS(f"Starting Menard Trading 24/7 Automated Mailbox Poller (Polling every {interval}s)..."))
        while True:
            try:
                self.run_sync()
            except KeyboardInterrupt:
                self.stdout.write(self.style.NOTICE("\nStopping mailbox poller daemon."))
                break
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error in poller iteration: {e}"))
            time.sleep(interval)

    def run_sync(self):
        result = sync_orders_mailbox()
        now_str = timezone.now().strftime('%Y-%m-%d %H:%M:%S')
        if result.get('status') == 'success':
            count = result.get('synced_count', 0)
            if count > 0:
                self.stdout.write(self.style.SUCCESS(f"[{now_str}] Successfully ingested {count} new Purchase Order(s):"))
                for po_num in result.get('orders', []):
                    self.stdout.write(self.style.SUCCESS(f"  -> Processed, Quoted & Auto-replied: {po_num}"))
        else:
            err = result.get('error', 'Unknown IMAP connection error')
            # Only print error if it's not an empty inbox
            if 'NO' in str(err) or 'error' in str(err).lower():
                self.stdout.write(self.style.WARNING(f"[{now_str}] Mailbox check: {err}"))
