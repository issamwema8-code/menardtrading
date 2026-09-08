from decimal import Decimal
from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class SystemPermission(models.Model):
    """
    Standardized, machine-readable granular permission definitions.
    Follows module.action format, e.g.:
      - orders.view, orders.create, orders.upload, orders.email, orders.delete
      - quotations.view, quotations.create, quotations.send, quotations.approve
      - logistics.view, logistics.update_status, logistics.upload_pod
      - invoices.view, invoices.create, invoices.record_payment, invoices.send
      - receipts.view, receipts.download
      - customers.view, customers.create, customers.update, customers.delete
      - users.view, users.create, users.update, users.delete, users.assign_roles
      - roles.view, roles.create, roles.update, roles.delete, roles.assign_permissions
      - audit.view
      - settings.view, settings.update
    """
    codename = models.CharField(max_length=100, unique=True, db_index=True)
    name = models.CharField(max_length=150)
    module = models.CharField(max_length=50, db_index=True)
    description = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['module', 'codename']
        verbose_name = 'System Permission'
        verbose_name_plural = 'System Permissions'

    def __str__(self):
        return f"{self.module} | {self.name} ({self.codename})"


class Role(models.Model):
    """
    Dynamic, business-driven user roles configured by Superusers.
    A role bundles one or more SystemPermissions.
    """
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    is_system = models.BooleanField(default=False, help_text="System-defined default role")
    permissions = models.ManyToManyField(
        SystemPermission,
        related_name='roles',
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Role'
        verbose_name_plural = 'Roles'

    def __str__(self):
        return self.name

    @property
    def permission_count(self):
        return self.permissions.count()

    @property
    def user_count(self):
        return self.user_profiles.count()


import secrets
from django.contrib.auth.hashers import make_password, check_password

WEAK_2FA_PINS = {
    '0000', '1111', '2222', '3333', '4444', '5555', '6666', '7777', '8888', '9999',
    '1234', '4321', '0123', '3210', '1212', '6969', '1313', '2424', '2026', '2025'
}


class UserProfile(models.Model):
    """
    Extends standard Django User with multi-role assignment, RBAC helpers,
    and secure Two-Factor Authentication (2FA) credentials.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='profile'
    )
    phone = models.CharField(max_length=40, blank=True, default='')
    roles = models.ManyToManyField(
        Role,
        related_name='user_profiles',
        blank=True
    )

    # Two-Factor Authentication: 4-digit PIN credentials (Hashed, never plaintext)
    two_factor_pin_hash = models.CharField(max_length=255, blank=True, null=True)
    pin_failed_attempts = models.PositiveIntegerField(default=0)
    pin_locked_until = models.DateTimeField(null=True, blank=True)

    # Two-Factor Authentication: 6-digit Email OTP credentials (Hashed, never plaintext)
    email_otp_hash = models.CharField(max_length=255, blank=True, null=True)
    email_otp_created_at = models.DateTimeField(null=True, blank=True)
    email_otp_expires_at = models.DateTimeField(null=True, blank=True)
    email_otp_failed_attempts = models.PositiveIntegerField(default=0)
    email_otp_resend_cooldown_until = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'User Profile'
        verbose_name_plural = 'User Profiles'

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.user.email})"

    @property
    def is_pin_configured(self) -> bool:
        """Returns True if the user has a valid 4-digit PIN configured."""
        return bool(self.two_factor_pin_hash)

    @property
    def is_pin_locked(self) -> bool:
        """Returns True if PIN verification is currently locked due to too many failed attempts."""
        if self.pin_locked_until:
            return timezone.now() < self.pin_locked_until
        return False

    @property
    def is_otp_cooldown_active(self) -> bool:
        """Returns True if resend cooldown timer is currently active."""
        if self.email_otp_resend_cooldown_until:
            return timezone.now() < self.email_otp_resend_cooldown_until
        return False

    def get_otp_cooldown_remaining_seconds(self) -> int:
        """Returns seconds remaining before next OTP resend is allowed."""
        if self.email_otp_resend_cooldown_until:
            remaining = int((self.email_otp_resend_cooldown_until - timezone.now()).total_seconds())
            return max(0, remaining)
        return 0

    def set_pin(self, raw_pin: str) -> tuple[bool, str]:
        """
        Validates, checks security criteria, and hashes the 4-digit PIN.
        """
        raw_pin = str(raw_pin).strip()
        if not raw_pin.isdigit() or len(raw_pin) != 4:
            return False, "PIN must consist of exactly 4 numeric digits."
        
        if raw_pin in WEAK_2FA_PINS:
            return False, "The chosen PIN is too common. Please choose a more secure 4-digit combination."

        self.two_factor_pin_hash = make_password(raw_pin)
        self.pin_failed_attempts = 0
        self.pin_locked_until = None
        self.save(update_fields=['two_factor_pin_hash', 'pin_failed_attempts', 'pin_locked_until', 'updated_at'])
        return True, "2FA PIN configured successfully."

    def check_pin(self, raw_pin: str) -> tuple[bool, str]:
        """
        Validates raw 4-digit PIN against stored hash with rate-limiting and temporary lockout.
        """
        if not self.is_pin_configured:
            return False, "No 2FA PIN configured for this account."

        if self.is_pin_locked:
            remaining_mins = max(1, int((self.pin_locked_until - timezone.now()).total_seconds() // 60))
            return False, f"Too many failed PIN attempts. Locked for {remaining_mins} minutes. Please use email verification."

        raw_pin = str(raw_pin).strip()
        if not raw_pin.isdigit() or len(raw_pin) != 4:
            self._record_pin_failure()
            return False, "Incorrect PIN"

        if check_password(raw_pin, self.two_factor_pin_hash):
            self.pin_failed_attempts = 0
            self.pin_locked_until = None
            self.save(update_fields=['pin_failed_attempts', 'pin_locked_until', 'updated_at'])
            return True, "PIN verified successfully."
        else:
            self._record_pin_failure()
            return False, "Incorrect PIN"

    def _record_pin_failure(self):
        self.pin_failed_attempts += 1
        if self.pin_failed_attempts >= 5:
            self.pin_locked_until = timezone.now() + timezone.timedelta(minutes=15)
        self.save(update_fields=['pin_failed_attempts', 'pin_locked_until', 'updated_at'])

    def clear_pin(self):
        """Removes the configured 2FA PIN."""
        self.two_factor_pin_hash = None
        self.pin_failed_attempts = 0
        self.pin_locked_until = None
        self.save(update_fields=['two_factor_pin_hash', 'pin_failed_attempts', 'pin_locked_until', 'updated_at'])

    def generate_email_otp(self) -> str:
        """
        Generates a single-use cryptographically secure 6-digit OTP,
        hashes it for storage, and enforces a 10-minute validity window.
        """
        raw_otp = f"{secrets.randbelow(900000) + 100000:06d}"
        now = timezone.now()
        
        self.email_otp_hash = make_password(raw_otp)
        self.email_otp_created_at = now
        self.email_otp_expires_at = now + timezone.timedelta(minutes=10)
        self.email_otp_resend_cooldown_until = now + timezone.timedelta(minutes=1)
        self.email_otp_failed_attempts = 0
        self.save(update_fields=[
            'email_otp_hash', 'email_otp_created_at', 'email_otp_expires_at',
            'email_otp_resend_cooldown_until', 'email_otp_failed_attempts', 'updated_at'
        ])
        return raw_otp

    def verify_email_otp(self, raw_otp: str) -> tuple[bool, str]:
        """
        Validates raw 6-digit OTP against stored hash with expiration and attempt limiting.
        Single-use: automatically invalidated upon successful match.
        """
        if not self.email_otp_hash or not self.email_otp_expires_at:
            return False, "No active verification code found. Please request a new one."

        if timezone.now() > self.email_otp_expires_at:
            return False, "Your verification code has expired. Please request a new one."

        if self.email_otp_failed_attempts >= 5:
            return False, "Too many failed attempts. Please request a new verification code."

        raw_otp = str(raw_otp).strip()
        if not raw_otp.isdigit() or len(raw_otp) != 6:
            self._record_otp_failure()
            return False, "Incorrect code"

        if check_password(raw_otp, self.email_otp_hash):
            # Invalidate single-use code immediately
            self.email_otp_hash = None
            self.email_otp_expires_at = None
            self.email_otp_failed_attempts = 0
            self.save(update_fields=['email_otp_hash', 'email_otp_expires_at', 'email_otp_failed_attempts', 'updated_at'])
            return True, "Verification code accepted."
        else:
            self._record_otp_failure()
            return False, "Incorrect code"

    def _record_otp_failure(self):
        self.email_otp_failed_attempts += 1
        self.save(update_fields=['email_otp_failed_attempts', 'updated_at'])

    def effective_permissions(self):
        """
        Returns a set of all active permission codenames granted to this user.
        Superusers inherently receive all active system permissions.
        """
        if self.user.is_superuser:
            return set(SystemPermission.objects.values_list('codename', flat=True))
        
        if not self.user.is_active:
            return set()

        return set(
            SystemPermission.objects.filter(
                roles__in=self.roles.filter(is_active=True)
            ).values_list('codename', flat=True).distinct()
        )

    def has_perm(self, codename: str) -> bool:
        """
        Checks if the user has a specific granular permission.
        """
        if self.user.is_superuser:
            return True
        if not self.user.is_active:
            return False
        return codename in self.effective_permissions()


class AuditLog(models.Model):
    """
    Comprehensive, immutable security & operational audit trail.
    """
    class Result(models.TextChoices):
        SUCCESS = 'SUCCESS', 'Success'
        FAILURE = 'FAILURE', 'Failure'
        DENIED = 'DENIED', 'Access Denied'

    user = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs'
    )
    user_email = models.CharField(max_length=255, blank=True, default='')
    action = models.CharField(max_length=100, db_index=True)
    resource_type = models.CharField(max_length=100, blank=True, default='', db_index=True)
    resource_id = models.CharField(max_length=100, blank=True, default='')
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True, default='')
    details = models.JSONField(default=dict, blank=True)
    result = models.CharField(
        max_length=20,
        choices=Result.choices,
        default=Result.SUCCESS,
        db_index=True
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Audit Log'
        verbose_name_plural = 'Audit Logs'

    def __str__(self):
        user_str = self.user.email if self.user else (self.user_email or 'Anonymous')
        return f"[{self.created_at.strftime('%Y-%m-%d %H:%M:%S')}] {user_str} -> {self.action} ({self.result})"


class CompanySettings(models.Model):
    """
    Centralized company & business profile configuration.
    Single source of truth for invoices, quotations, receipts, emails, and PDFs.
    """
    company_name = models.CharField(max_length=200, default="MENARD TRADING CC", help_text="Official business/company name")
    tagline = models.CharField(max_length=200, default="ALWAYS ON TIME", blank=True, help_text="Corporate tagline")
    
    # Address & Location
    postal_address = models.CharField(max_length=255, default="P O BOX 497-19001,", blank=True, help_text="Postal address")
    physical_address = models.TextField(blank=True, default="", help_text="Street address / Logistics depot location")
    city = models.CharField(max_length=100, default="RUNDU", blank=True, help_text="City / Town")
    country = models.CharField(max_length=100, default="NAMIBIA", blank=True, help_text="Country")
    
    # Legal & Tax Registration (Empty by default unless explicitly configured)
    vat_number = models.CharField(max_length=100, blank=True, default="", help_text="Official VAT Registration Number")
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('15.00'), help_text="Default VAT Rate Percentage (e.g. 15.00, 0, 10)")
    company_reg_number = models.CharField(max_length=100, blank=True, default="", help_text="Company / Close Corporation Registration Number")
    
    # Contact & Communication
    phone = models.CharField(max_length=50, blank=True, default="+264 81 445 5188", help_text="Primary telephone number")
    mobile = models.CharField(max_length=50, blank=True, default="+264 81 445 5188", help_text="Direct / Mobile number")
    email = models.EmailField(blank=True, default="support@menardtrading.com", help_text="General company email")
    orders_email = models.EmailField(blank=True, default="orders@menardtrading.com", help_text="Orders & POs mailbox")
    quotes_email = models.EmailField(blank=True, default="quotes@menardtrading.com", help_text="Quotations mailbox")
    accounts_email = models.EmailField(blank=True, default="accounts@menardtrading.com", help_text="Accounts & Billing mailbox")
    website = models.URLField(blank=True, default="https://menardtrading.com", help_text="Official company website URL")
    
    # Logo & Assets
    logo_image = models.ImageField(upload_to="branding/", blank=True, null=True, help_text="Custom company logo image")
    login_bg_image_1 = models.ImageField(upload_to="branding/backgrounds/", blank=True, null=True, help_text="Login & Portal Background Image 1")
    login_bg_image_2 = models.ImageField(upload_to="branding/backgrounds/", blank=True, null=True, help_text="Login & Portal Background Image 2")
    login_bg_image_3 = models.ImageField(upload_to="branding/backgrounds/", blank=True, null=True, help_text="Login & Portal Background Image 3")
    
    # Banking Details for Invoices, Statements & Receipts
    bank_name = models.CharField(max_length=100, blank=True, default="", help_text="Bank name (e.g. First National Bank)")
    account_name = models.CharField(max_length=150, blank=True, default="", help_text="Bank account holder name")
    account_number = models.CharField(max_length=60, blank=True, default="", help_text="Bank account number")
    account_type = models.CharField(max_length=60, blank=True, default="", help_text="Account type (e.g. Business Cheque Account)")
    branch_code = models.CharField(max_length=30, blank=True, default="", help_text="Bank branch code")
    branch_name = models.CharField(max_length=100, blank=True, default="", help_text="Branch name")
    swift_code = models.CharField(max_length=30, blank=True, default="", help_text="SWIFT / BIC code")
    
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')

    class Meta:
        verbose_name = "Company Settings"
        verbose_name_plural = "Company Settings"

    def __str__(self):
        return f"{self.company_name} ({self.get_formatted_address()})"

    @classmethod
    def get_settings(cls):
        """
        Retrieves the singleton CompanySettings instance or initializes with default values.
        """
        obj = cls.objects.first()
        if not obj:
            obj = cls.objects.create(
                company_name="MENARD TRADING CC",
                tagline="ALWAYS ON TIME",
                postal_address="P O BOX 497-19001,",
                city="RUNDU",
                country="NAMIBIA",
                physical_address="",
                vat_number="",
                vat_rate=Decimal('15.00'),
                company_reg_number="",
                phone="+264 81 445 5188",
                mobile="+264 81 445 5188",
                email="support@menardtrading.com",
                orders_email="orders@menardtrading.com",
                quotes_email="quotes@menardtrading.com",
                accounts_email="accounts@menardtrading.com",
                website="https://menardtrading.com",
            )
        return obj

    def get_formatted_address(self) -> str:
        """
        Returns a single-line formatted address combining postal, physical, and city/country.
        """
        parts = []
        if self.physical_address:
            addr = self.physical_address.strip().rstrip(',')
            if addr:
                parts.append(addr)
        if self.postal_address:
            addr = self.postal_address.strip().rstrip(',')
            if addr:
                parts.append(addr)
        
        location_parts = []
        if self.city:
            c = self.city.strip().rstrip(',')
            if c:
                location_parts.append(c)
        if self.country:
            co = self.country.strip().rstrip(',')
            if co:
                location_parts.append(co)
        
        if location_parts:
            location_str = " - ".join(location_parts)
            if location_str not in " ".join(parts):
                parts.append(location_str)
        
        return ", ".join(parts) if parts else "P O BOX 497-19001, RUNDU - NAMIBIA"


    def as_branding_dict(self) -> dict:
        """
        Transforms CompanySettings into the standardized dictionary used across templates, emails, and PDFs.
        """
        from django.conf import settings
        base_url = getattr(settings, 'BASE_URL', 'https://menardtrading.com')
        
        emblem_b64 = getattr(settings, 'EMAIL_BRANDING', {}).get('emblem_base64', '')
        logo_png_url = getattr(settings, 'EMAIL_BRANDING', {}).get('logo_png_url', f"{base_url}/static/images/menard_emblem.png")
        logo_url = getattr(settings, 'EMAIL_BRANDING', {}).get('logo_url', f"{base_url}/static/images/menard_emblem.png")
        
        if self.logo_image:
            try:
                logo_url = f"{base_url}{self.logo_image.url}"
                logo_png_url = logo_url
                with open(self.logo_image.path, 'rb') as f:
                    import base64
                    emblem_b64 = f"data:image/png;base64,{base64.b64encode(f.read()).decode('utf-8')}"
            except Exception:
                pass

        vat_rate_val = self.vat_rate if self.vat_rate is not None else Decimal('15.00')
        pct_str = f"{vat_rate_val:.2f}%" if (vat_rate_val % 1 != 0) else f"{int(vat_rate_val)}%"

        return {
            'brand_name': self.company_name or 'MENARD TRADING CC',
            'company_name': self.company_name or 'MENARD TRADING CC',
            'tagline': self.tagline or 'ALWAYS ON TIME',
            'logo_url': logo_url,
            'logo_png_url': logo_png_url,
            'logo_svg': f"{base_url}/static/images/menard_emblem.svg",
            'emblem_base64': emblem_b64,
            'postal_address': self.postal_address or '',
            'physical_address': self.physical_address or '',
            'city': self.city or '',
            'country': self.country or '',
            'company_address': self.get_formatted_address(),
            'vat_number': self.vat_number or '',
            'vat_rate': vat_rate_val,
            'vat_percentage': pct_str,
            'company_reg_number': self.company_reg_number or '',
            'support_email': self.email or 'support@menardtrading.com',
            'orders_email': self.orders_email or self.email or 'orders@menardtrading.com',
            'quotes_email': self.quotes_email or self.email or 'quotes@menardtrading.com',
            'accounts_email': self.accounts_email or self.email or 'accounts@menardtrading.com',
            'phone': self.phone or self.mobile or '',
            'telephone': self.phone or '',
            'mobile': self.mobile or '',
            'website_url': self.website or 'https://menardtrading.com',
            'login_bg_image_1': self.login_bg_image_1.url if self.login_bg_image_1 else f"{settings.STATIC_URL}images/login_bg_3.jpg",
            'login_bg_image_2': self.login_bg_image_2.url if self.login_bg_image_2 else f"{settings.STATIC_URL}images/login_bg_3.jpg",
            'login_bg_image_3': self.login_bg_image_3.url if self.login_bg_image_3 else f"{settings.STATIC_URL}images/login_bg_3.jpg",
            'has_custom_login_bg_1': bool(self.login_bg_image_1),
            'has_custom_login_bg_2': bool(self.login_bg_image_2),
            'has_custom_login_bg_3': bool(self.login_bg_image_3),
            'bank_details': {
                'bank_name': self.bank_name or '',
                'account_name': self.account_name or self.company_name or '',
                'account_number': self.account_number or '',
                'account_type': self.account_type or '',
                'branch_code': self.branch_code or '',
                'branch_name': self.branch_name or '',
                'swift_code': self.swift_code or '',
            }
        }


class AdminNotification(models.Model):
    class NotificationType(models.TextChoices):
        NEW_ORDER = 'NEW_ORDER', 'New Purchase Order'
        EMAIL_FAILED = 'EMAIL_FAILED', 'Email Delivery Failed'
        QUOTE_APPROVED = 'QUOTE_APPROVED', 'Quote Approved'
        PAYMENT_RECEIVED = 'PAYMENT_RECEIVED', 'Payment Received'
        SYSTEM = 'SYSTEM', 'System Alert'

    title = models.CharField(max_length=200)
    message = models.TextField()
    notification_type = models.CharField(
        max_length=30,
        choices=NotificationType.choices,
        default=NotificationType.NEW_ORDER,
        db_index=True
    )
    link_url = models.CharField(max_length=300, blank=True, default='')
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Admin Notification'
        verbose_name_plural = 'Admin Notifications'

    def __str__(self):
        return f"{self.get_notification_type_display()}: {self.title}"
