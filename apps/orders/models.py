import uuid
from django.utils import timezone
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


class InboundEmailMessage(models.Model):
    """
    Persisted raw record of an incoming email from customers or inbound webhooks.
    Guarantees fast non-blocking persistence and strict idempotency via unique message_id.
    """
    class Status(models.TextChoices):
        RECEIVED = 'RECEIVED', 'Received'
        PROCESSING = 'PROCESSING', 'Processing'
        PROCESSED = 'PROCESSED', 'Processed'
        FAILED = 'FAILED', 'Processing Failed'

    message_id = models.CharField(
        max_length=255,
        unique=True,
        db_index=True,
        help_text="Unique RFC Message-ID, Brevo item token, or content hash for strict idempotency"
    )
    sender_email = models.EmailField(db_index=True)
    sender_name = models.CharField(max_length=200, blank=True, default='')
    recipient_email = models.EmailField(default='orders@menardtrading.com')
    reply_to = models.EmailField(blank=True, default='')
    subject = models.CharField(max_length=500, blank=True)
    body_text = models.TextField(blank=True)
    body_html = models.TextField(blank=True)
    raw_payload = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.RECEIVED,
        db_index=True
    )
    processing_error = models.TextField(blank=True)

    purchase_order = models.ForeignKey(
        'PurchaseOrder',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='inbound_messages'
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Inbound Email Message'
        verbose_name_plural = 'Inbound Email Messages'

    def __str__(self):
        return f"Inbound {self.message_id[:20]} from {self.sender_email} ({self.get_status_display()})"


class EmailQueueMessage(models.Model):
    """
    Persistent asynchronous queue for outbound departmental emails.
    Features exponential backoff retries, state tracking, and full diagnostic logs.
    """
    class Status(models.TextChoices):
        QUEUED = 'QUEUED', 'Queued'
        SENDING = 'SENDING', 'Sending'
        SENT = 'SENT', 'Sent'
        FAILED = 'FAILED', 'Delivery Failed'
        CANCELLED = 'CANCELLED', 'Cancelled'

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    department = models.CharField(max_length=50, default='no-reply')
    recipient_list = models.JSONField(default=list, help_text="List of recipient email addresses")
    reply_to = models.CharField(max_length=200, blank=True, default='')
    subject = models.CharField(max_length=500)
    template_name = models.CharField(max_length=200)
    context_data = models.JSONField(default=dict, blank=True, encoder=DjangoJSONEncoder)
    attachments_data = models.JSONField(default=list, blank=True)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True
    )
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=4)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_error = models.TextField(blank=True)

    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    purchase_order = models.ForeignKey(
        'PurchaseOrder',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='queued_emails'
    )
    inbound_email = models.ForeignKey(
        'InboundEmailMessage',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='triggered_emails'
    )

    class Meta:
        ordering = ['next_attempt_at', 'created_at']
        verbose_name = 'Queued Email Message'
        verbose_name_plural = 'Queued Email Messages'

    def __str__(self):
        recipients = ", ".join(self.recipient_list) if isinstance(self.recipient_list, list) else str(self.recipient_list)
        return f"Email [{self.get_status_display()}] to {recipients[:30]} ({self.subject[:30]})"

