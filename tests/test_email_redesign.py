from django.test import TestCase
from django.template.loader import render_to_string
from django.conf import settings
from decimal import Decimal
from django.utils import timezone
from apps.customers.models import Customer
from apps.orders.models import PurchaseOrder
from apps.quotes.models import Quotation, QuoteLineItem
from apps.logistics.models import LogisticsJob
from apps.billing.models import Invoice, InvoiceLineItem, PaymentReceipt
from django.contrib.auth.models import User


class EmailTemplateRedesignTests(TestCase):
    """
    Validates complete 3-part brand lockup, professional customer-facing language,
    and absence of robotic/technical jargon across all email templates.
    """

    def setUp(self):
        self.customer = Customer.objects.create(
            company_name="Tasec Trading Enterprises CC",
            contact_name="Sarah Jacobs",
            email="procurement@tasec.com",
            phone="+264 61 987 654",
            vat_number="4099887766",
            physical_address="Walvis Bay Port Logistics Yard",
            billing_address="PO Box 1024, Walvis Bay, Namibia"
        )

        self.po = PurchaseOrder.objects.create(
            customer=self.customer,
            po_number="PO-2026-901",
            pickup_location="Walvis Bay Port",
            delivery_location="Windhoek Distribution Center",
            cargo_description="34T Palletized Commercial Goods",
            weight_tons=Decimal("34.00"),
            status=PurchaseOrder.Status.RECEIVED
        )

        self.quote = Quotation.objects.create(
            customer=self.customer,
            purchase_order=self.po,
            quote_number="QT-2026-0901",
            subtotal=Decimal("45000.00"),
            vat_rate=Decimal("15.00"),
            vat_amount=Decimal("6750.00"),
            total_amount=Decimal("51750.00"),
            status=Quotation.Status.SENT
        )

        self.job = LogisticsJob.objects.create(
            quote=self.quote,
            customer=self.customer,
            job_number="JOB-2026-0901",
            vehicle_registration="N 89201 W",
            driver_name="Lucas Shilongo",
            status=LogisticsJob.Status.POD_RECEIVED
        )

        self.invoice = Invoice.objects.create(
            job=self.job,
            customer=self.customer,
            invoice_number="INV-2026-0901",
            invoice_type=Invoice.InvoiceType.FULL,
            subtotal=Decimal("45000.00"),
            vat_amount=Decimal("6750.00"),
            total_amount=Decimal("51750.00"),
            amount_paid=Decimal("0.00"),
            balance_due=Decimal("51750.00"),
            due_date=timezone.now().date(),
            status=Invoice.Status.ISSUED
        )

        self.receipt = PaymentReceipt.objects.create(
            invoice=self.invoice,
            customer=self.customer,
            amount_paid=Decimal("51750.00"),
            payment_method=PaymentReceipt.PaymentMethod.EFT,
            transaction_reference="EFT-FNB-998822",
            payment_date=timezone.now().date()
        )

        self.user = User.objects.create_user(
            username="testuser",
            email="sarah.jacobs@tasec.com",
            first_name="Sarah",
            last_name="Jacobs"
        )

        self.branding = getattr(settings, 'EMAIL_BRANDING', {})
        self.context_base = {
            'branding': self.branding,
            'base_url': 'https://menardtrading.com'
        }

        self.forbidden_phrases = [
            "Automated Transmission Confirmation",
            "Automated Transmission",
            "System Generated",
            "System Notification",
            "Live Operations Dashboard",
            "Backend",
            "API",
            "Webhook",
            "Database",
            "Internal System",
            "Processing Engine",
            "Document Processing",
            "System-generated confirmation"
        ]

    def _assert_branding_present(self, html: str):
        self.assertIn("MENARD TRADING CC", html)
        self.assertIn("ALWAYS ON TIME", html)
        # Verify emblem / logo is present
        self.assertTrue(
            ("menard_emblem.svg" in html) or ("data:image/svg+xml;base64" in html) or ("alt=\"Menard Trading CC\"" in html)
        )

    def _assert_no_robotic_jargon(self, html: str):
        for phrase in self.forbidden_phrases:
            self.assertNotIn(phrase.lower(), html.lower())

    def test_po_received_noreply_template(self):
        ctx = {
            **self.context_base,
            'po': self.po,
            'customer_name': self.customer.contact_name
        }
        html = render_to_string('emails/po_received_noreply.html', ctx)
        self._assert_branding_present(html)
        self._assert_no_robotic_jargon(html)
        self.assertIn("Purchase Order Received", html)
        self.assertIn("#PO-2026-901", html)
        self.assertIn("Walvis Bay Port", html)
        self.assertIn("Windhoek Distribution Center", html)
        self.assertIn("Quotation in preparation", html)

    def test_po_received_template(self):
        ctx = {
            **self.context_base,
            'po': self.po,
            'customer_name': self.customer.contact_name
        }
        html = render_to_string('emails/po_received.html', ctx)
        self._assert_branding_present(html)
        self._assert_no_robotic_jargon(html)
        self.assertIn("Purchase Order Received", html)
        self.assertIn("#PO-2026-901", html)

    def test_po_operator_reply_template(self):
        ctx = {
            **self.context_base,
            'po': self.po,
            'recipient_name': self.customer.contact_name,
            'message_body': "Kindly provide the updated customs clearance forms for Walvis Bay."
        }
        html = render_to_string('emails/po_operator_reply.html', ctx)
        self._assert_branding_present(html)
        self._assert_no_robotic_jargon(html)
        self.assertIn("Update on Purchase Order #PO-2026-901", html)
        self.assertIn("customs clearance forms", html)

    def test_po_failed_noreply_template(self):
        ctx = {
            **self.context_base,
            'subject': "Freight Request",
            'error_message': "The attached file could not be read."
        }
        html = render_to_string('emails/po_failed_noreply.html', ctx)
        self._assert_branding_present(html)
        self._assert_no_robotic_jargon(html)
        self.assertIn("Regarding Your Purchase Order", html)
        self.assertIn("Freight Request", html)

    def test_quote_sent_template(self):
        ctx = {
            **self.context_base,
            'quote': self.quote,
            'approval_url': 'https://menardtrading.com/portal/quotes/xyz123/'
        }
        html = render_to_string('emails/quote_sent.html', ctx)
        self._assert_branding_present(html)
        self._assert_no_robotic_jargon(html)
        self.assertIn("Quotation #QT-2026-0901", html)
        self.assertIn("N$ 51750.00", html)
        self.assertIn("Review &amp; Approve Quotation", html)

    def test_invoice_sent_template(self):
        ctx = {
            **self.context_base,
            'invoice': self.invoice
        }
        html = render_to_string('emails/invoice_sent.html', ctx)
        self._assert_branding_present(html)
        self._assert_no_robotic_jargon(html)
        self.assertIn("Tax Invoice #INV-2026-0901", html)
        self.assertIn("N$ 51750.00", html)
        self.assertIn("Banking Details for EFT Settlement", html)
        self.assertIn("First National Bank", html)

    def test_receipt_sent_template(self):
        ctx = {
            **self.context_base,
            'receipt': self.receipt
        }
        html = render_to_string('emails/receipt_sent.html', ctx)
        self._assert_branding_present(html)
        self._assert_no_robotic_jargon(html)
        self.assertIn("Payment Receipt #", html)
        self.assertIn("N$ 51750.00", html)
        self.assertIn("EFT-FNB-998822", html)

    def test_pod_received_template(self):
        ctx = {
            **self.context_base,
            'job': self.job
        }
        html = render_to_string('emails/pod_received.html', ctx)
        self._assert_branding_present(html)
        self._assert_no_robotic_jargon(html)
        self.assertIn("Proof of Delivery", html)
        self.assertIn("#JOB-2026-0901", html)
        self.assertIn("Lucas Shilongo", html)
        self.assertIn("N 89201 W", html)

    def test_two_factor_otp_template(self):
        ctx = {
            **self.context_base,
            'user_name': self.user.first_name,
            'otp_code': '849201'
        }
        html = render_to_string('emails/two_factor_otp.html', ctx)
        self._assert_branding_present(html)
        self._assert_no_robotic_jargon(html)
        self.assertIn("849201", html)
        self.assertIn("Your Verification Code", html)

    def test_password_reset_template(self):
        ctx = {
            **self.context_base,
            'user': self.user,
            'reset_url': 'https://menardtrading.com/accounts/reset-password/uid/token/'
        }
        html = render_to_string('emails/password_reset.html', ctx)
        self._assert_branding_present(html)
        self._assert_no_robotic_jargon(html)
        self.assertIn("Password Reset Request", html)
        self.assertIn("Reset My Password", html)
