import uuid
from decimal import Decimal
from django.db import models
from django.utils import timezone
from apps.customers.models import Customer
from apps.orders.models import PurchaseOrder


def generate_quote_number():
    year = timezone.now().year
    last_quote = Quotation.objects.filter(quote_number__startswith=f"QT-{year}-").order_by('-id').first()
    if last_quote:
        try:
            last_seq = int(last_quote.quote_number.split('-')[-1])
            new_seq = last_seq + 1
        except (ValueError, IndexError):
            new_seq = 1
    else:
        new_seq = 1
    return f"QT-{year}-{new_seq:04d}"


class Quotation(models.Model):
    class Status(models.TextChoices):
        DRAFT = 'DRAFT', 'Draft'
        SENT = 'SENT', 'Sent to Customer'
        APPROVED = 'APPROVED', 'Approved by Customer'
        REJECTED = 'REJECTED', 'Rejected'
        EXPIRED = 'EXPIRED', 'Expired'

    quote_number = models.CharField(
        max_length=50,
        unique=True,
        default=generate_quote_number,
        db_index=True
    )
    purchase_order = models.OneToOneField(
        PurchaseOrder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='quotation'
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name='quotations'
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True
    )
    approval_token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('15.00'))
    vat_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    valid_until = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="Rates include comprehensive transit goods insurance. Payment terms as per agreement.")
    
    quote_pdf = models.FileField(upload_to='quotes/%Y/%m/', null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Quotation'
        verbose_name_plural = 'Quotations'

    def __str__(self):
        return f"{self.quote_number} - {self.customer.company_name} (R{self.total_amount})"

    def save(self, *args, **kwargs):
        is_new = self._state.adding
        if is_new:
            try:
                from apps.accounts.models import CompanySettings
                settings_vat = CompanySettings.get_settings().vat_rate
                if settings_vat is not None:
                    # If vat_rate is still default 15.00, apply configured company settings rate
                    if self.vat_rate == Decimal('15.00') or self.vat_rate is None:
                        self.vat_rate = settings_vat
            except Exception:
                pass
        super().save(*args, **kwargs)

    def recalculate_totals(self):
        if self.vat_rate is None:
            try:
                from apps.accounts.models import CompanySettings
                settings_vat = CompanySettings.get_settings().vat_rate
                self.vat_rate = settings_vat if settings_vat is not None else Decimal('15.00')
            except Exception:
                self.vat_rate = Decimal('15.00')

        items_total = sum((item.total_price for item in self.line_items.all()), Decimal('0.00'))
        self.subtotal = items_total
        self.vat_amount = (self.subtotal * (self.vat_rate / Decimal('100.00'))).quantize(Decimal('0.01'))
        self.total_amount = self.subtotal + self.vat_amount
        self.save(update_fields=['vat_rate', 'subtotal', 'vat_amount', 'total_amount'])


class QuoteLineItem(models.Model):
    class ItemType(models.TextChoices):
        FREIGHT = 'FREIGHT', 'Freight Transport'
        FUEL_SURCHARGE = 'FUEL_SURCHARGE', 'Fuel Surcharge'
        TOLL_FEES = 'TOLL_FEES', 'Tolls / Route Surcharges'
        BORDER_CLEARANCE = 'BORDER_CLEARANCE', 'Border Clearance / Customs'
        HANDLING = 'HANDLING', 'Loading / Offloading Handling'
        DEMURRAGE = 'DEMURRAGE', 'Demurrage / Detention'
        OTHER = 'OTHER', 'Other Logistics Service'

    quote = models.ForeignKey(Quotation, on_delete=models.CASCADE, related_name='line_items')
    item_type = models.CharField(max_length=30, choices=ItemType.choices, default=ItemType.FREIGHT)
    description = models.CharField(max_length=500)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('1.00'))
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    total_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))

    class Meta:
        ordering = ['id']

    def save(self, *args, **kwargs):
        self.total_price = (Decimal(str(self.quantity)) * Decimal(str(self.unit_price))).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)
        if self.quote:
            self.quote.recalculate_totals()

    def delete(self, *args, **kwargs):
        quote = self.quote
        super().delete(*args, **kwargs)
        if quote:
            quote.recalculate_totals()
