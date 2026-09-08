from django.db import models
from django.core.serializers.json import DjangoJSONEncoder
from apps.customers.models import Customer


class PurchaseOrder(models.Model):
    class Status(models.TextChoices):
        RECEIVED = 'RECEIVED', 'PO Received'
        PARSING = 'PARSING', 'Parsing Document'
        PARSED = 'PARSED', 'Parsed & Ready for Quote'
        QUOTED = 'QUOTED', 'Quote Generated'
        JOB_CREATED = 'JOB_CREATED', 'Job Created'
        COMPLETED = 'COMPLETED', 'Completed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    class Source(models.TextChoices):
        EMAIL = 'EMAIL', 'Email'
        MANUAL_UPLOAD = 'MANUAL_UPLOAD', 'Manual Upload'

    po_number = models.CharField(max_length=100, unique=True, db_index=True)
    customer = models.ForeignKey(
        Customer,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='purchase_orders'
    )
    source = models.CharField(
        max_length=20,
        choices=Source.choices,
        default=Source.EMAIL,
        db_index=True
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
        db_index=True
    )
    po_file = models.FileField(upload_to='purchase_orders/%Y/%m/', null=True, blank=True)
    
    # Inbound email metadata
    raw_email_sender = models.EmailField(blank=True)
    raw_email_subject = models.CharField(max_length=500, blank=True)
    raw_email_body = models.TextField(blank=True)

    # Extracted Logistics & Cargo Data
    pickup_location = models.TextField(blank=True)
    delivery_location = models.TextField(blank=True)
    cargo_description = models.TextField(blank=True)
    weight_tons = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    volume_cbm = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    quantity_pallets = models.PositiveIntegerField(null=True, blank=True)
    required_pickup_date = models.DateField(null=True, blank=True)
    required_delivery_date = models.DateField(null=True, blank=True)
    special_instructions = models.TextField(blank=True)
    
    # Raw parser JSON payload
    extracted_json = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)

    # Automated Communication Tracking (Strictly 1 No-Reply Acknowledgment)
    acknowledgment_sent = models.BooleanField(
        default=False,
        help_text="Indicates whether the automated no-reply receipt confirmation was dispatched."
    )
    acknowledgment_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the automated no-reply receipt confirmation was sent."
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Purchase Order'
        verbose_name_plural = 'Purchase Orders'

    def __str__(self):
        customer_name = self.customer.company_name if self.customer else self.raw_email_sender
        return f"PO #{self.po_number} - {customer_name} ({self.get_status_display()})"


class OrderCommunication(models.Model):
    """
    Tracks outgoing communication, clarifications, and replies sent to the customer regarding a PO.
    """
    purchase_order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name='communications')
    sender_department = models.CharField(max_length=50, default='orders')
    recipient_email = models.EmailField()
    subject = models.CharField(max_length=500)
    message_body = models.TextField()
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-sent_at']

    def __str__(self):
        return f"Reply to {self.recipient_email} for PO #{self.purchase_order.po_number}"

