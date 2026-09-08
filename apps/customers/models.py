from django.db import models


class Customer(models.Model):
    class PaymentTerms(models.TextChoices):
        UPFRONT_100 = '100_UPFRONT', '100% Upfront'
        DEPOSIT_50_POD_50 = '50_DEPOSIT_50_POD', '50% Deposit / 50% on POD'
        DEPOSIT_30_POD_70 = '30_DEPOSIT_70_POD', '30% Deposit / 70% on POD'
        NET_30_DAYS = '30_DAYS_NET', '30 Days Net'
        CUSTOM = 'CUSTOM', 'Custom Terms'

    company_name = models.CharField(max_length=255, db_index=True)
    trading_name = models.CharField(max_length=255, blank=True)
    contact_name = models.CharField(max_length=255)
    email = models.EmailField(db_index=True)
    phone = models.CharField(max_length=50)
    vat_number = models.CharField(max_length=50, blank=True)
    registration_number = models.CharField(max_length=100, blank=True)
    physical_address = models.TextField()
    billing_address = models.TextField(blank=True)
    payment_terms = models.CharField(
        max_length=30,
        choices=PaymentTerms.choices,
        default=PaymentTerms.DEPOSIT_50_POD_50
    )
    credit_limit = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['company_name']
        verbose_name = 'Customer'
        verbose_name_plural = 'Customers'

    def __str__(self):
        return f"{self.company_name} ({self.contact_name})"
