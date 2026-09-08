from decimal import Decimal
from django.db import models
from django.utils import timezone
from apps.customers.models import Customer
from apps.quotes.models import Quotation


def generate_job_number():
    year = timezone.now().year
    last_job = LogisticsJob.objects.filter(job_number__startswith=f"JOB-{year}-").order_by('-id').first()
    if last_job:
        try:
            last_seq = int(last_job.job_number.split('-')[-1])
            new_seq = last_seq + 1
        except (ValueError, IndexError):
            new_seq = 1
    else:
        new_seq = 1
    return f"JOB-{year}-{new_seq:04d}"


class LogisticsJob(models.Model):
    class Status(models.TextChoices):
        BOOKED = 'BOOKED', 'Booked / Scheduled'
        DISPATCHED = 'DISPATCHED', 'Dispatched / En Route to Pickup'
        IN_TRANSIT = 'IN_TRANSIT', 'Loaded & In Transit'
        DELIVERED = 'DELIVERED', 'Delivered at Bay'
        POD_RECEIVED = 'POD_RECEIVED', 'POD Signed & Verified'
        CLOSED = 'CLOSED', 'Job Closed'

    job_number = models.CharField(
        max_length=50,
        unique=True,
        default=generate_job_number,
        db_index=True
    )
    quote = models.ForeignKey(
        Quotation,
        on_delete=models.CASCADE,
        related_name='jobs'
    )
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name='logistics_jobs'
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.BOOKED,
        db_index=True
    )
    
    # Fleet & Driver info
    vehicle_registration = models.CharField(max_length=50, blank=True, help_text="e.g. CA 123-456")
    trailer_number = models.CharField(max_length=50, blank=True)
    driver_name = models.CharField(max_length=150, blank=True)
    driver_phone = models.CharField(max_length=50, blank=True)

    # Route & Cargo info
    pickup_address = models.TextField()
    delivery_address = models.TextField()
    cargo_summary = models.TextField(blank=True)
    weight_tons = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Dates
    scheduled_date = models.DateField(default=timezone.now)
    dispatch_date = models.DateTimeField(null=True, blank=True)
    delivery_date = models.DateTimeField(null=True, blank=True)

    # Proof of Delivery (POD)
    pod_document = models.FileField(upload_to='pods/%Y/%m/', null=True, blank=True)
    pod_uploaded_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Logistics Job'
        verbose_name_plural = 'Logistics Jobs'

    def __str__(self):
        return f"{self.job_number} - {self.customer.company_name} ({self.get_status_display()})"

    @property
    def quote_total(self):
        return self.quote.total_amount if self.quote else Decimal('0.00')

    @property
    def total_invoiced(self):
        return sum((inv.total_amount for inv in self.invoices.exclude(status='CANCELLED')), Decimal('0.00'))

    @property
    def total_paid(self):
        return sum((inv.amount_paid for inv in self.invoices.exclude(status='CANCELLED')), Decimal('0.00'))

    @property
    def remaining_uninvoiced(self):
        rem = self.quote_total - self.total_invoiced
        return max(Decimal('0.00'), rem)
