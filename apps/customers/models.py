from django.db import models
from django.utils.text import slugify


class PaymentTermOption(models.Model):
    """
    Dynamic configurable payment terms that can be expanded on the fly by users.
    """
    name = models.CharField(max_length=150, unique=True, help_text="Human-readable term name, e.g. 50% Deposit / 50% on Delivery")
    code = models.CharField(max_length=150, unique=True, db_index=True, help_text="Machine identifier, e.g. 50_DEPOSIT_50_POD")
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    display_order = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['display_order', 'name']
        verbose_name = 'Payment Term Option'
        verbose_name_plural = 'Payment Term Options'

    def __str__(self):
        return self.name

    @classmethod
    def get_all_terms(cls):
        """
        Returns all active terms. Seeds default terms if none exist.
        """
        if not cls.objects.exists():
            default_terms = [
                ('50_DEPOSIT_50_POD', '50% Deposit / 50% on Delivery', 1),
                ('100_UPFRONT', '100% Upfront', 2),
                ('30_DEPOSIT_70_POD', '30% Deposit / 70% on Delivery', 3),
                ('30_DAYS_NET', '30 Days Net', 4),
                ('14_DAYS_NET', '14 Days Net', 5),
                ('7_DAYS_NET', '7 Days Net', 6),
                ('COD', 'Cash on Delivery (COD)', 7),
                ('CUSTOM', 'Custom Terms', 8),
            ]
            for code, name, order in default_terms:
                cls.objects.get_or_create(code=code, defaults={'name': name, 'display_order': order})
        return list(cls.objects.filter(is_active=True).order_by('display_order', 'name'))

    @classmethod
    def add_custom_term(cls, name: str, code: str = None, description: str = ''):
        name = name.strip()
        if not name:
            raise ValueError("Payment term name cannot be empty.")
        if not code:
            code = slugify(name).replace('-', '_').upper()
            if not code:
                code = f"TERM_{cls.objects.count() + 1}"
        
        base_code = code
        counter = 1
        while cls.objects.filter(code=code).exclude(name__iexact=name).exists():
            code = f"{base_code}_{counter}"
            counter += 1

        term, created = cls.objects.get_or_create(
            name__iexact=name,
            defaults={
                'name': name,
                'code': code,
                'description': description,
                'display_order': 100 + cls.objects.count()
            }
        )
        return term


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
        max_length=150,
        default=PaymentTerms.DEPOSIT_50_POD_50,
        blank=True
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

    def get_payment_terms_display(self):
        # 1. Match against built-in choices
        for val, label in self.PaymentTerms.choices:
            if self.payment_terms == val:
                return label
        # 2. Match against dynamic PaymentTermOption
        term = PaymentTermOption.objects.filter(code=self.payment_terms).first()
        if term:
            return term.name
        # 3. Fallback to readable format
        return self.payment_terms.replace('_', ' ').title() if self.payment_terms else 'Standard'

    def get_address_lines(self):
        """
        Returns an ordered list of address lines (PO Box, postal address, physical address)
        avoiding duplicates or placeholder N/A values.
        """
        lines = []
        if self.billing_address and self.billing_address.strip() and self.billing_address.strip() != 'N/A':
            lines.append(self.billing_address.strip())
        if self.physical_address and self.physical_address.strip() and self.physical_address.strip() != 'N/A':
            cleaned_phys = self.physical_address.strip()
            if not any(cleaned_phys.lower() == l.lower() for l in lines) and cleaned_phys not in (self.billing_address or ''):
                lines.append(cleaned_phys)
        return lines

    @property
    def display_address(self):
        lines = self.get_address_lines()
        return ", ".join(lines) if lines else ""

