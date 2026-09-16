import logging
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from apps.billing.models import Invoice, PaymentReceipt
from apps.logistics.models import LogisticsJob

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Audits, fixes and recalculates invoice totals, payment receipts, balance due, and status across the entire system."

    def add_arguments(self, parser):
        parser.add_argument(
            '--invoice',
            type=str,
            help="Specific invoice number to inspect and fix (e.g. INV-2026-0003)."
        )
        parser.add_argument(
            '--paid',
            type=str,
            help="For a specific --invoice, adjust/ensure the linked receipt amount matches this value (e.g. --paid 2500)."
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help="Simulate the audit and recalculation without writing changes to the database."
        )

    def handle(self, *args, **options):
        invoice_num = options.get('invoice')
        paid_override = options.get('paid')
        dry_run = options.get('dry_run', False)

        self.stdout.write(self.style.SUCCESS("=" * 80))
        self.stdout.write(self.style.SUCCESS(" MENARD TRADING CC — INVOICE BALANCES & PAYMENT AUDIT ENGINE"))
        self.stdout.write(self.style.SUCCESS("=" * 80))

        if dry_run:
            self.stdout.write(self.style.WARNING(">> RUNNING IN DRY-RUN MODE (No database changes will be saved)\n"))

        if invoice_num:
            invoices = Invoice.objects.filter(invoice_number__iexact=invoice_num.strip())
            if not invoices.exists():
                self.stdout.write(self.style.ERROR(f"Invoice '{invoice_num}' not found."))
                return
        else:
            invoices = Invoice.objects.all().order_by('id')

        total_checked = 0
        total_fixed = 0

        for inv in invoices:
            total_checked += 1
            old_total = inv.total_amount
            old_paid = inv.amount_paid
            old_bal = inv.balance_due
            old_status = inv.status

            # If paid_override was passed for a specific invoice, adjust or create receipt
            if invoice_num and paid_override is not None:
                new_paid_val = Decimal(str(paid_override).strip())
                receipt = inv.receipts.order_by('id').first()
                if receipt:
                    if not dry_run:
                        receipt.amount_paid = new_paid_val
                        receipt.save()
                    self.stdout.write(self.style.WARNING(f"  -> Adjusted Receipt #{receipt.receipt_number} amount to N$ {new_paid_val:,.2f}"))
                elif new_paid_val > Decimal('0.00'):
                    if not dry_run:
                        PaymentReceipt.objects.create(
                            invoice=inv,
                            customer=inv.customer,
                            amount_paid=new_paid_val,
                            payment_method=PaymentReceipt.PaymentMethod.EFT,
                            transaction_reference=f"ADJ-{timezone.now().strftime('%Y%m%d%H%M')}",
                            notes="Auto-adjusted payment allocation."
                        )
                    self.stdout.write(self.style.WARNING(f"  -> Created Adjustment Receipt of N$ {new_paid_val:,.2f}"))

            # Calculate actual sum of all receipts
            actual_paid = sum((rcp.amount_paid for rcp in inv.receipts.all()), Decimal('0.00'))
            
            # Recalculate line items & totals
            if inv.line_items.exists():
                items_total = sum((item.total_price for item in inv.line_items.all()), Decimal('0.00'))
                inv.subtotal = items_total
                inv.vat_amount = (inv.subtotal * (inv.vat_rate / Decimal('100.00'))).quantize(Decimal('0.01'))
                inv.total_amount = inv.subtotal + inv.vat_amount
            elif inv.total_amount == Decimal('0.00') and inv.subtotal > Decimal('0.00'):
                inv.vat_amount = (inv.subtotal * (inv.vat_rate / Decimal('100.00'))).quantize(Decimal('0.01'))
                inv.total_amount = inv.subtotal + inv.vat_amount
            elif inv.subtotal == Decimal('0.00') and inv.total_amount > Decimal('0.00'):
                if inv.vat_rate > Decimal('0.00'):
                    inv.subtotal = (inv.total_amount / (Decimal('1.00') + inv.vat_rate / Decimal('100.00'))).quantize(Decimal('0.01'))
                    inv.vat_amount = inv.total_amount - inv.subtotal
                else:
                    inv.subtotal = inv.total_amount
                    inv.vat_amount = Decimal('0.00')

            inv.amount_paid = actual_paid
            inv.balance_due = max(Decimal('0.00'), inv.total_amount - inv.amount_paid)

            # Determine correct status
            if inv.amount_paid >= inv.total_amount and inv.total_amount > Decimal('0.00'):
                inv.status = Invoice.Status.PAID
            elif inv.amount_paid > Decimal('0.00'):
                inv.status = Invoice.Status.PARTIALLY_PAID
            elif inv.due_date and inv.due_date < timezone.localdate() and inv.status not in [Invoice.Status.DRAFT, Invoice.Status.CANCELLED]:
                inv.status = Invoice.Status.OVERDUE
            elif inv.status not in [Invoice.Status.DRAFT, Invoice.Status.CANCELLED]:
                inv.status = Invoice.Status.ISSUED

            discrepancy = (
                old_total != inv.total_amount or
                old_paid != inv.amount_paid or
                old_bal != inv.balance_due or
                old_status != inv.status
            )

            if discrepancy:
                total_fixed += 1
                self.stdout.write(self.style.WARNING(
                    f"\n[!] INVOICE MISMATCH DETECTED: {inv.invoice_number} ({inv.customer.company_name})"
                ))
                self.stdout.write(f"    - Total Amount : N$ {old_total:,.2f}  ==>  N$ {inv.total_amount:,.2f}")
                self.stdout.write(f"    - Amount Paid  : N$ {old_paid:,.2f}  ==>  N$ {inv.amount_paid:,.2f}")
                self.stdout.write(f"    - Balance Due  : N$ {old_bal:,.2f}  ==>  N$ {inv.balance_due:,.2f}")
                self.stdout.write(f"    - Status       : {old_status}  ==>  {inv.status}")

                if not dry_run:
                    inv.save(update_fields=['subtotal', 'vat_rate', 'vat_amount', 'total_amount', 'amount_paid', 'balance_due', 'status'])
                    self.stdout.write(self.style.SUCCESS(f"    [FIXED] Saved corrected values to database."))
            else:
                self.stdout.write(f"[OK] {inv.invoice_number}: Total=N$ {inv.total_amount:,.2f} | Paid=N$ {inv.amount_paid:,.2f} | Bal=N$ {inv.balance_due:,.2f} | Status={inv.status}")

            # Job status sync
            if inv.job and not dry_run:
                job = inv.job
                if job.remaining_uninvoiced == Decimal('0.00') and all(i.status == Invoice.Status.PAID for i in job.invoices.all()):
                    if job.status != LogisticsJob.Status.CLOSED:
                        job.status = LogisticsJob.Status.CLOSED
                        job.save(update_fields=['status'])
                elif job.status == LogisticsJob.Status.CLOSED and any(i.status != Invoice.Status.PAID for i in job.invoices.all()):
                    job.status = LogisticsJob.Status.DELIVERED if job.pod_document else LogisticsJob.Status.DISPATCHED
                    job.save(update_fields=['status'])

        self.stdout.write(self.style.SUCCESS("\n" + "=" * 80))
        self.stdout.write(self.style.SUCCESS(
            f" AUDIT COMPLETE: {total_checked} invoice(s) checked, {total_fixed} invoice(s) corrected."
        ))
        self.stdout.write(self.style.SUCCESS("=" * 80 + "\n"))
