from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone
from django.urls import reverse
import io

from apps.customers.models import Customer
from apps.billing.models import Invoice, InvoiceLineItem, PaymentReceipt
from apps.billing.pdf_services import generate_receipt_pdf

User = get_user_model()


class InvoiceBalanceFixTestCase(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            company_name="TASEC TRADING ENTERPRISES CC",
            contact_name="COMFORT",
            email="chikunimenard@gmail.com",
            phone="0814968285",
            vat_number="1119990",
            physical_address="990 donkeruoek Rundu Namibia",
            payment_terms="30_DAYS_NET"
        )
        self.user = User.objects.create_superuser(
            username="adminuser",
            email="admin@menardtrading.com",
            password="adminpassword123"
        )
        self.client = Client()
        self.client.force_login(self.user)

    def test_3000_invoice_with_2500_payment_is_partially_paid_with_500_balance(self):
        """
        Verify that an invoice billed for 3,000 with 2,500 paid calculates:
        - amount_paid: 2,500.00
        - balance_due: 500.00
        - status: PARTIALLY_PAID (NOT PAID)
        """
        inv = Invoice.objects.create(
            invoice_number="INV-2026-0003",
            customer=self.customer,
            invoice_type=Invoice.InvoiceType.FULL,
            due_date=timezone.localdate() + timezone.timedelta(days=14),
            vat_rate=Decimal('0.00'),
            notes="Standard Full Invoice",
            status=Invoice.Status.ISSUED
        )
        InvoiceLineItem.objects.create(
            invoice=inv,
            description="Freight Consignment Service",
            quantity=Decimal('1.00'),
            unit_price=Decimal('3000.00')
        )
        inv.recalculate_totals()
        inv.refresh_from_db()

        self.assertEqual(inv.total_amount, Decimal('3000.00'))
        self.assertEqual(inv.amount_paid, Decimal('0.00'))
        self.assertEqual(inv.balance_due, Decimal('3000.00'))
        self.assertEqual(inv.status, Invoice.Status.ISSUED)

        # Record payment of 2,500.00
        receipt = PaymentReceipt.objects.create(
            receipt_number="RCP-2026-0001",
            invoice=inv,
            customer=self.customer,
            amount_paid=Decimal('2500.00'),
            payment_method=PaymentReceipt.PaymentMethod.EFT,
            transaction_reference="1244"
        )

        inv.refresh_from_db()
        self.assertEqual(inv.total_amount, Decimal('3000.00'))
        self.assertEqual(inv.amount_paid, Decimal('2500.00'))
        self.assertEqual(inv.balance_due, Decimal('500.00'))
        self.assertEqual(inv.status, Invoice.Status.PARTIALLY_PAID)

        # Verify receipt generation and remaining balance in PDF context
        pdf_bytes = generate_receipt_pdf(receipt)
        self.assertIsNotNone(pdf_bytes)

    def test_management_command_fixes_corrupted_invoice_balances(self):
        """
        Simulate an invoice where status was incorrectly forced to PAID and balance_due to 0,
        and verify management command audits and fixes it.
        """
        inv = Invoice.objects.create(
            invoice_number="INV-2026-0003",
            customer=self.customer,
            invoice_type=Invoice.InvoiceType.FULL,
            due_date=timezone.localdate() + timezone.timedelta(days=14),
            vat_rate=Decimal('0.00')
        )
        InvoiceLineItem.objects.create(
            invoice=inv,
            description="Freight Consignment Service",
            quantity=Decimal('1.00'),
            unit_price=Decimal('3000.00')
        )
        PaymentReceipt.objects.create(
            receipt_number="RCP-2026-0001",
            invoice=inv,
            customer=self.customer,
            amount_paid=Decimal('2500.00'),
            payment_method=PaymentReceipt.PaymentMethod.EFT,
            transaction_reference="1244"
        )

        # Intentionally corrupt invoice fields (as if corrupted by legacy update)
        Invoice.objects.filter(pk=inv.pk).update(
            amount_paid=Decimal('3000.00'),
            balance_due=Decimal('0.00'),
            status=Invoice.Status.PAID
        )

        corrupted_inv = Invoice.objects.get(pk=inv.pk)
        self.assertEqual(corrupted_inv.status, Invoice.Status.PAID)
        self.assertEqual(corrupted_inv.balance_due, Decimal('0.00'))

        # Run fix_invoice_balances command
        out = io.StringIO()
        call_command('fix_invoice_balances', stdout=out)

        fixed_inv = Invoice.objects.get(pk=inv.pk)
        self.assertEqual(fixed_inv.amount_paid, Decimal('2500.00'))
        self.assertEqual(fixed_inv.balance_due, Decimal('500.00'))
        self.assertEqual(fixed_inv.status, Invoice.Status.PARTIALLY_PAID)
        self.assertIn("INVOICE MISMATCH DETECTED: INV-2026-0003", out.getvalue())

    def test_sync_balances_web_action(self):
        """
        Test the web view POST endpoint that audits and recalculates all invoices.
        """
        inv = Invoice.objects.create(
            invoice_number="INV-2026-0004",
            customer=self.customer,
            invoice_type=Invoice.InvoiceType.FULL,
            due_date=timezone.localdate() + timezone.timedelta(days=14),
            vat_rate=Decimal('0.00')
        )
        InvoiceLineItem.objects.create(
            invoice=inv,
            description="Transport Durban to JHB",
            quantity=Decimal('1.00'),
            unit_price=Decimal('3000.00')
        )
        PaymentReceipt.objects.create(
            receipt_number="RCP-2026-0002",
            invoice=inv,
            customer=self.customer,
            amount_paid=Decimal('2000.00'),
            payment_method=PaymentReceipt.PaymentMethod.EFT,
            transaction_reference="EFT-2000"
        )

        url = reverse('recalculate_invoices_action')
        response = self.client.post(url, follow=True)
        self.assertEqual(response.status_code, 200)

        inv.refresh_from_db()
        self.assertEqual(inv.total_amount, Decimal('3000.00'))
        self.assertEqual(inv.amount_paid, Decimal('2000.00'))
        self.assertEqual(inv.balance_due, Decimal('1000.00'))
        self.assertEqual(inv.status, Invoice.Status.PARTIALLY_PAID)
