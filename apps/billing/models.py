from decimal import Decimal
from django.db import models
from django.utils import timezone
from apps.customers.models import Customer
from apps.quotes.models import Quotation
from apps.logistics.models import LogisticsJob


def generate_invoice_number():
    year = timezone.now().year
    last_inv = Invoice.objects.filter(invoice_number__startswith=f"INV-{year}-").order_by('-id').first()
    if last_inv:
        try:
            last_seq = int(last_inv.invoice_number.split('-')[-1].split('/')[0])
            new_seq = last_seq + 1
        except (ValueError, IndexError):
            new_seq = 1
    else:
        new_seq = 1
    return f"INV-{year}-{new_seq:04d}"


def generate_receipt_number():
    year = timezone.now().year
    last_rcp = PaymentReceipt.objects.filter(receipt_number__startswith=f"RCP-{year}-").order_by('-id').first()
    if last_rcp:
        try:
            last_seq = int(last_rcp.receipt_number.split('-')[-1])
            new_seq = last_seq + 1
        except (ValueError, IndexError):
            new_seq = 1
    else:
        new_seq = 1
    return f"RCP-{year}-{new_seq:04d}"


class Invoice(models.Model):
    class InvoiceType(models.TextChoices):
        FULL = 'FULL', 'Standard Full Invoice'
        PARTIAL_DEPOSIT = 'PARTIAL_DEPOSIT', 'Mobilization / Deposit Invoice'
        PARTIAL_BALANCE = 'PARTIAL_BALANCE', 'Final Balance on POD Invoice'
        ADD_ON = 'ADD_ON', 'Supplementary / Add-on Invoice (Demurrage/Tolls)'

    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        ISSUED = 'ISSUED', 'Issued / Sent'
        PARTIALLY_PAID = 'PARTIALLY_PAID', 'Partially Paid'
        PAID = 'PAID', 'Fully Paid'
        OVERDUE = 'OVERDUE', 'Overdue'
        CANCELLED = 'CANCELLED', 'Cancelled'

    invoice_number = models.CharField(
        max_length=50,
        unique=True,
        default=generate_invoice_number,
        db_index=True
    )
    job = models.ForeignKey(
        LogisticsJob,
        on_delete=models.CASCADE,
        related_name='invoices',
        null=True,
        blank=True
    )
    quote = models.ForeignKey(
        Quotation,
        on_delete=models.SET_NULL,
        related_name='invoices',
        null=True,
        blank=True
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name='invoices'
    )
    invoice_type = models.CharField(
        max_length=20,
        choices=InvoiceType.choices,
        default=InvoiceType.FULL,
        db_index=True
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True
    )

    issue_date = models.DateField(default=timezone.localdate, db_index=True)
    due_date = models.DateField(db_index=True)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0.00'))
    vat_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    balance_due = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    invoice_pdf = models.FileField(upload_to='invoices/%Y/%m/', null=True, blank=True)
    notes = models.TextField(blank=True, default="Payment strictly according to agreed terms. Direct EFT into Menard Trading CC bank account.")
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def po_number(self):
        """Returns the associated Purchase Order reference number if available."""
        if self.job and self.job.quote and self.job.quote.purchase_order and self.job.quote.purchase_order.po_number:
            return self.job.quote.purchase_order.po_number
        if self.quote and self.quote.purchase_order and self.quote.purchase_order.po_number:
            return self.quote.purchase_order.po_number
        return ""

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Invoice'
        verbose_name_plural = 'Invoices'

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        if is_new:
            try:
                from apps.accounts.models import CompanySettings
                settings_vat = CompanySettings.get_settings().vat_rate
                if self.quote and self.quote.vat_rate is not None:
                    if self.vat_rate == Decimal('0.00') or self.vat_rate is None:
                        self.vat_rate = self.quote.vat_rate
                elif settings_vat is not None:
                    if self.vat_rate == Decimal('0.00') or self.vat_rate is None:
                        self.vat_rate = settings_vat
            except Exception:
                pass
        super().save(*args, **kwargs)

    def recalculate_totals(self):
        if self.vat_rate is None:
            try:
                from apps.accounts.models import CompanySettings
                if self.quote and self.quote.vat_rate is not None:
                    self.vat_rate = self.quote.vat_rate
                else:
                    settings_vat = CompanySettings.get_settings().vat_rate
                    self.vat_rate = settings_vat if settings_vat is not None else Decimal('0.00')
            except Exception:
                self.vat_rate = Decimal('0.00')

        if self.line_items.exists():
            items_total = sum((item.total_price for item in self.line_items.all()), Decimal('0.00'))
            self.subtotal = items_total
            self.vat_amount = (self.subtotal * (self.vat_rate / Decimal('100.00'))).quantize(Decimal('0.01'))
            self.total_amount = self.subtotal + self.vat_amount
        elif self.total_amount == Decimal('0.00') and self.subtotal > Decimal('0.00'):
            self.vat_amount = (self.subtotal * (self.vat_rate / Decimal('100.00'))).quantize(Decimal('0.01'))
            self.total_amount = self.subtotal + self.vat_amount
        elif self.subtotal == Decimal('0.00') and self.total_amount > Decimal('0.00'):
            if self.vat_rate > Decimal('0.00'):
                self.subtotal = (self.total_amount / (Decimal('1.00') + self.vat_rate / Decimal('100.00'))).quantize(Decimal('0.01'))
                self.vat_amount = self.total_amount - self.subtotal
            else:
                self.subtotal = self.total_amount
                self.vat_amount = Decimal('0.00')

        total_paid = sum((rcp.amount_paid for rcp in self.receipts.all()), Decimal('0.00'))
        self.amount_paid = total_paid
        self.balance_due = max(Decimal('0.00'), self.total_amount - self.amount_paid)
        if self.amount_paid >= self.total_amount and self.total_amount > Decimal('0.00'):
            self.status = self.Status.PAID
        elif self.amount_paid > Decimal('0.00'):
            self.status = self.Status.PARTIALLY_PAID
        elif self.due_date and self.due_date < timezone.localdate() and self.status not in [self.Status.DRAFT, self.Status.CANCELLED]:
            self.status = self.Status.OVERDUE
        elif self.status not in [self.Status.DRAFT, self.Status.CANCELLED]:
            self.status = self.Status.ISSUED
        self.save(update_fields=['vat_rate', 'subtotal', 'vat_amount', 'total_amount', 'amount_paid', 'balance_due', 'status'])

        # Sync linked LogisticsJob status if applicable
        if self.job:
            job = self.job
            if job.remaining_uninvoiced == Decimal('0.00') and all(i.status == Invoice.Status.PAID for i in job.invoices.all()):
                if job.status != LogisticsJob.Status.CLOSED:
                    job.status = LogisticsJob.Status.CLOSED
                    job.save(update_fields=['status'])
            elif job.status == LogisticsJob.Status.CLOSED and any(i.status != Invoice.Status.PAID for i in job.invoices.all()):
                job.status = LogisticsJob.Status.DELIVERED if job.pod_document else LogisticsJob.Status.DISPATCHED
                job.save(update_fields=['status'])


class InvoiceLineItem(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name='line_items')
    description = models.CharField(max_length=500)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('1.00'))
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        ordering = ['id']

    def save(self, *args, **kwargs):
        self.total_price = (Decimal(str(self.quantity)) * Decimal(str(self.unit_price))).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)
        if self.invoice:
            self.invoice.recalculate_totals()

    def delete(self, *args, **kwargs):
        inv = self.invoice
        super().delete(*args, **kwargs)
        if inv:
            inv.recalculate_totals()


class PaymentReceipt(models.Model):
    class PaymentMethod(models.TextChoices):
        EFT = 'EFT_BANK_TRANSFER', 'EFT / Bank Transfer'
        CREDIT_CARD = 'CREDIT_CARD', 'Credit / Debit Card'
        DIRECT_DEPOSIT = 'DIRECT_DEPOSIT', 'Direct Bank Deposit'
        CASH = 'CASH', 'Cash'

    receipt_number = models.CharField(
        max_length=50,
        unique=True,
        default=generate_receipt_number,
        db_index=True
    )
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name='receipts'
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name='payment_receipts'
    )
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2)
    payment_method = models.CharField(
        max_length=30,
        choices=PaymentMethod.choices,
        default=PaymentMethod.EFT
    )
    payment_date = models.DateField(default=timezone.localdate, db_index=True)
    transaction_reference = models.CharField(max_length=100, help_text="e.g. Bank Statement Reference or POP Code")
    receipt_pdf = models.FileField(upload_to='receipts/%Y/%m/', null=True, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Payment Receipt'
        verbose_name_plural = 'Payment Receipts'

    def __str__(self):
        from menard_core.formatters import format_money
        return f"{self.receipt_number} - {format_money(self.amount_paid, 'R')} for {self.invoice.invoice_number}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.invoice:
            self.invoice.recalculate_totals()

    def delete(self, *args, **kwargs):
        inv = self.invoice
        super().delete(*args, **kwargs)
        if inv:
            inv.recalculate_totals()
