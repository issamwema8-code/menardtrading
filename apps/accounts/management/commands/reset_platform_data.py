import os
import shutil
from django.core.management.base import BaseCommand
from django.conf import settings
from django.db import transaction

from apps.orders.models import PurchaseOrder, OrderCommunication, InboundEmailMessage, EmailQueueMessage
from apps.quotes.models import Quotation, QuoteLineItem
from apps.logistics.models import LogisticsJob
from apps.billing.models import Invoice, InvoiceLineItem, PaymentReceipt
from apps.accounting.models import SupplierBill, SupplierBillPayment, AccountingTransaction
from apps.accounts.models import AdminNotification, AuditLog
from apps.customers.models import Customer


class Command(BaseCommand):
    help = "Cleanses all test transactions, purchase orders, quotes, invoices, emails, and financial ledgers from the platform for live production rollout."

    def add_arguments(self, parser):
        parser.add_argument(
            '--wipe-customers',
            action='store_true',
            help='Also deletes all Customer directory records (default: False, keeps customer accounts).'
        )
        parser.add_argument(
            '--clean-media',
            action='store_true',
            help='Deletes generated PDFs and uploads in media/ directory.'
        )
        parser.add_argument(
            '--yes',
            action='store_true',
            help='Confirm data wipe without interactive confirmation prompt.'
        )

    def handle(self, *args, **options):
        wipe_customers = options.get('wipe_customers', False)
        clean_media = options.get('clean_media', False)
        confirmed = options.get('yes', False)

        self.stdout.write(self.style.WARNING("=" * 80))
        self.stdout.write(self.style.WARNING(" MENARD TRADING CC — PLATFORM DATA RESET & PURGE"))
        self.stdout.write(self.style.WARNING("=" * 80))
        self.stdout.write("This command will delete all transactional data:")
        self.stdout.write("  - All Purchase Orders & Communications")
        self.stdout.write("  - All Quotations & Line Items")
        self.stdout.write("  - All Logistics Jobs & Trips")
        self.stdout.write("  - All Invoices, Line Items & Payment Receipts")
        self.stdout.write("  - All Supplier Bills & Accounting Transactions")
        self.stdout.write("  - All Inbound Email Messages & Outbound Queues")
        self.stdout.write("  - All Admin Notifications & Audit Logs")
        if wipe_customers:
            self.stdout.write(self.style.ERROR("  - ALL Customer Directory Records (WIPE ENABLED)"))
        else:
            self.stdout.write("  - (Customer records and User accounts will be PRESERVED)")

        if not confirmed:
            answer = input("\nAre you sure you want to proceed? Type 'RESET' to confirm: ")
            if answer.strip() != 'RESET':
                self.stdout.write(self.style.ERROR("Operation cancelled. No data was deleted."))
                return

        with transaction.atomic():
            # 1. Billing & Financials
            receipts_c = PaymentReceipt.objects.count()
            PaymentReceipt.objects.all().delete()

            inv_items_c = InvoiceLineItem.objects.count()
            InvoiceLineItem.objects.all().delete()

            inv_c = Invoice.objects.count()
            Invoice.objects.all().delete()

            bill_payments_c = SupplierBillPayment.objects.count()
            SupplierBillPayment.objects.all().delete()

            bills_c = SupplierBill.objects.count()
            SupplierBill.objects.all().delete()

            tx_c = AccountingTransaction.objects.count()
            AccountingTransaction.objects.all().delete()

            # 2. Logistics & Fleet
            jobs_c = LogisticsJob.objects.count()
            LogisticsJob.objects.all().delete()

            # 3. Quotes
            quote_items_c = QuoteLineItem.objects.count()
            QuoteLineItem.objects.all().delete()

            quotes_c = Quotation.objects.count()
            Quotation.objects.all().delete()

            # 4. Orders & Communications
            order_comms_c = OrderCommunication.objects.count()
            OrderCommunication.objects.all().delete()

            orders_c = PurchaseOrder.objects.count()
            PurchaseOrder.objects.all().delete()

            # 5. Email Queues & Inbound Logs
            email_queue_c = EmailQueueMessage.objects.count()
            EmailQueueMessage.objects.all().delete()

            inbound_emails_c = InboundEmailMessage.objects.count()
            InboundEmailMessage.objects.all().delete()

            # 6. Notifications & Audit Logs
            notifs_c = AdminNotification.objects.count()
            AdminNotification.objects.all().delete()

            audit_c = AuditLog.objects.count()
            AuditLog.objects.all().delete()

            # 7. Customers (optional)
            if wipe_customers:
                cust_c = Customer.objects.count()
                Customer.objects.all().delete()
            else:
                cust_c = 0

        # 8. Clean media uploads if requested
        if clean_media:
            media_root = getattr(settings, 'MEDIA_ROOT', None)
            if media_root and os.path.exists(media_root):
                for folder in ['invoices', 'receipts', 'quotations', 'purchase_orders', 'statements', 'bills']:
                    folder_path = os.path.join(media_root, folder)
                    if os.path.exists(folder_path):
                        shutil.rmtree(folder_path, ignore_errors=True)
                        os.makedirs(folder_path, exist_ok=True)
                self.stdout.write(self.style.SUCCESS("  ✓ Purged generated PDFs in media/ directory."))

        self.stdout.write(self.style.SUCCESS("\n" + "=" * 80))
        self.stdout.write(self.style.SUCCESS(" PLATFORM DATA RESET COMPLETE"))
        self.stdout.write(self.style.SUCCESS("=" * 80))
        self.stdout.write(f"  - Deleted {orders_c} Purchase Orders & {order_comms_c} Communications")
        self.stdout.write(f"  - Deleted {quotes_c} Quotations & {quote_items_c} Line Items")
        self.stdout.write(f"  - Deleted {jobs_c} Logistics Jobs")
        self.stdout.write(f"  - Deleted {inv_c} Invoices & {receipts_c} Payment Receipts")
        self.stdout.write(f"  - Deleted {bills_c} Supplier Bills & {tx_c} Ledger Transactions")
        self.stdout.write(f"  - Deleted {inbound_emails_c} Inbound Email Records & {email_queue_c} Queued Emails")
        self.stdout.write(f"  - Deleted {notifs_c} Notifications & {audit_c} Audit Logs")
        if wipe_customers:
            self.stdout.write(f"  - Deleted {cust_c} Customer records")
        self.stdout.write(self.style.SUCCESS("\nThe system is now 100% clean and ready for live production orders!\n"))
