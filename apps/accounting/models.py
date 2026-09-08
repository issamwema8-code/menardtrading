from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


def generate_expense_number():
    year = timezone.now().year
    last_exp = Expense.objects.filter(expense_number__startswith=f"EXP-{year}-").order_by('-id').first()
    if last_exp:
        try:
            last_seq = int(last_exp.expense_number.split('-')[-1])
            new_seq = last_seq + 1
        except (ValueError, IndexError):
            new_seq = 1
    else:
        new_seq = 1
    return f"EXP-{year}-{new_seq:04d}"


def generate_bill_number():
    year = timezone.now().year
    last_bill = SupplierBill.objects.filter(bill_number__startswith=f"BILL-{year}-").order_by('-id').first()
    if last_bill:
        try:
            last_seq = int(last_bill.bill_number.split('-')[-1])
            new_seq = last_seq + 1
        except (ValueError, IndexError):
            new_seq = 1
    else:
        new_seq = 1
    return f"BILL-{year}-{new_seq:04d}"


def generate_bill_payment_number():
    year = timezone.now().year
    last_pay = SupplierBillPayment.objects.filter(payment_number__startswith=f"BPAY-{year}-").order_by('-id').first()
    if last_pay:
        try:
            last_seq = int(last_pay.payment_number.split('-')[-1])
            new_seq = last_seq + 1
        except (ValueError, IndexError):
            new_seq = 1
    else:
        new_seq = 1
    return f"BPAY-{year}-{new_seq:04d}"


class ExpenseCategory(models.Model):
    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Expense Category'
        verbose_name_plural = 'Expense Categories'

    def __str__(self):
        return self.name


class Vendor(models.Model):
    name = models.CharField(max_length=255, db_index=True)
    contact_name = models.CharField(max_length=255, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    vat_number = models.CharField(max_length=50, blank=True)
    registration_number = models.CharField(max_length=100, blank=True)
    physical_address = models.TextField(blank=True)
    bank_name = models.CharField(max_length=100, blank=True)
    account_number = models.CharField(max_length=50, blank=True)
    branch_code = models.CharField(max_length=20, blank=True)
    payment_terms = models.CharField(max_length=50, default='30_DAYS_NET')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Vendor / Supplier'
        verbose_name_plural = 'Vendors / Suppliers'

    def __str__(self):
        return self.name


class Expense(models.Model):
    class PaymentMethod(models.TextChoices):
        EFT = 'EFT_BANK_TRANSFER', 'EFT / Bank Transfer'
        CREDIT_CARD = 'CREDIT_CARD', 'Credit / Debit Card'
        DIRECT_DEPOSIT = 'DIRECT_DEPOSIT', 'Direct Bank Deposit'
        CASH = 'CASH', 'Cash'
        PETTY_CASH = 'PETTY_CASH', 'Petty Cash'

    class Status(models.TextChoices):
        PAID = 'PAID', 'Paid'
        PENDING = 'PENDING', 'Pending Approval'
        DRAFT = 'DRAFT', 'Draft'
        VOID = 'VOID', 'Void / Cancelled'

    expense_number = models.CharField(
        max_length=50,
        unique=True,
        default=generate_expense_number,
        db_index=True
    )
    date = models.DateField(default=timezone.now, db_index=True)
    category = models.ForeignKey(
        ExpenseCategory,
        on_delete=models.PROTECT,
        related_name='expenses'
    )
    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='expenses'
    )
    payee = models.CharField(
        max_length=255,
        blank=True,
        help_text="Direct payee name if not a registered vendor"
    )
    description = models.TextField()
    
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('15.00'))
    vat_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    payment_method = models.CharField(
        max_length=30,
        choices=PaymentMethod.choices,
        default=PaymentMethod.EFT
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PAID,
        db_index=True
    )
    receipt_file = models.FileField(upload_to='expenses/%Y/%m/', null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='expenses_created'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-date', '-created_at']
        verbose_name = 'Expense'
        verbose_name_plural = 'Expenses'

    def __str__(self):
        payee_name = self.vendor.name if self.vendor else (self.payee or 'Payee')
        return f"{self.expense_number} - {self.category.name} (R{self.total_amount}) to {payee_name}"

    def save(self, *args, **kwargs):
        # Automatically calculate VAT and total if subtotal provided
        if self.subtotal is not None:
            self.vat_amount = (self.subtotal * (self.vat_rate / Decimal('100.00'))).quantize(Decimal('0.01'))
            self.total_amount = self.subtotal + self.vat_amount
        super().save(*args, **kwargs)


class SupplierBill(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        ISSUED = 'ISSUED', 'Received / Awaiting Payment'
        PARTIALLY_PAID = 'PARTIALLY_PAID', 'Partially Paid'
        PAID = 'PAID', 'Fully Paid'
        OVERDUE = 'OVERDUE', 'Overdue'
        VOID = 'VOID', 'Void'

    bill_number = models.CharField(
        max_length=50,
        unique=True,
        default=generate_bill_number,
        db_index=True
    )
    supplier_reference = models.CharField(
        max_length=100,
        blank=True,
        help_text="Supplier's original invoice/bill number"
    )
    vendor = models.ForeignKey(
        Vendor,
        on_delete=models.CASCADE,
        related_name='bills'
    )
    issue_date = models.DateField(default=timezone.now, db_index=True)
    due_date = models.DateField(db_index=True)

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('15.00'))
    vat_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    balance_due = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ISSUED,
        db_index=True
    )
    bill_pdf = models.FileField(upload_to='supplier_bills/%Y/%m/', null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bills_created'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-due_date', '-created_at']
        verbose_name = 'Supplier Bill (Accounts Payable)'
        verbose_name_plural = 'Supplier Bills (Accounts Payable)'

    def __str__(self):
        return f"{self.bill_number} - {self.vendor.name} (R{self.total_amount}) [{self.get_status_display()}]"

    def recalculate_totals(self):
        self.vat_amount = (self.subtotal * (self.vat_rate / Decimal('100.00'))).quantize(Decimal('0.01'))
        self.total_amount = self.subtotal + self.vat_amount
        total_paid = sum((p.amount_paid for p in self.payments.all()), Decimal('0.00'))
        self.amount_paid = total_paid
        self.balance_due = max(Decimal('0.00'), self.total_amount - total_paid)
        if self.status != self.Status.VOID:
            if self.amount_paid >= self.total_amount and self.total_amount > 0:
                self.status = self.Status.PAID
            elif self.amount_paid > 0:
                self.status = self.Status.PARTIALLY_PAID
            elif self.due_date < timezone.now().date():
                self.status = self.Status.OVERDUE
            else:
                self.status = self.Status.ISSUED
        self.save()


class SupplierBillPayment(models.Model):
    class PaymentMethod(models.TextChoices):
        EFT = 'EFT_BANK_TRANSFER', 'EFT / Bank Transfer'
        CREDIT_CARD = 'CREDIT_CARD', 'Credit / Debit Card'
        DIRECT_DEPOSIT = 'DIRECT_DEPOSIT', 'Direct Bank Deposit'
        CASH = 'CASH', 'Cash'

    payment_number = models.CharField(
        max_length=50,
        unique=True,
        default=generate_bill_payment_number,
        db_index=True
    )
    bill = models.ForeignKey(
        SupplierBill,
        on_delete=models.CASCADE,
        related_name='payments'
    )
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField(default=timezone.now, db_index=True)
    payment_method = models.CharField(
        max_length=30,
        choices=PaymentMethod.choices,
        default=PaymentMethod.EFT
    )
    transaction_reference = models.CharField(max_length=100, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='bill_payments_created'
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-payment_date', '-created_at']
        verbose_name = 'Bill Payment'
        verbose_name_plural = 'Bill Payments'

    def __str__(self):
        return f"{self.payment_number} - R{self.amount_paid} for {self.bill.bill_number}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if self.bill:
            self.bill.recalculate_totals()
