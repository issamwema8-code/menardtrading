from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
import datetime

from apps.customers.models import Customer
from apps.quotes.models import Quotation, QuoteLineItem
from apps.logistics.models import LogisticsJob
from apps.billing.models import Invoice, InvoiceLineItem, PaymentReceipt
from apps.billing.pdf_services import (
    generate_invoice_pdf,
    generate_quotation_pdf,
    generate_receipt_pdf,
    render_html_to_pdf_bytes
)

User = get_user_model()


class DocumentDesignSystemTestCase(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_superuser(
            username='admin_doc_test',
            email='admin@menardtrading.com',
            password='Password123!'
        )
        self.client.force_login(self.user)

        # Create sample customer
        self.customer = Customer.objects.create(
            company_name='Kalahari Trans-Logistics Ltd',
            contact_name='Johannes Amupolo',
            email='accounts@kalaharitrans.na',
            phone='+264 81 987 6543',
            vat_number='NA-99887766-V01',
            physical_address='Plot 44, Northern Industrial Area, Windhoek, Namibia',
            billing_address='PO Box 1029, Windhoek, Namibia',
            payment_terms='50% Deposit / 50% on POD'
        )

        # Create sample quotation
        self.quote = Quotation.objects.create(
            customer=self.customer,
            quote_number='QT-2026-TEST01',
            subtotal=Decimal('20000.00'),
            vat_rate=Decimal('15.00'),
            vat_amount=Decimal('3000.00'),
            total_amount=Decimal('23000.00'),
            valid_until=timezone.now().date() + datetime.timedelta(days=14),
            status='SENT',
            notes='Special escort required through B1 highway route.'
        )
        QuoteLineItem.objects.create(
            quote=self.quote,
            item_type='FREIGHT',
            description='Heavy Machinery Transport Windhoek to Walvis Bay Port',
            quantity=Decimal('1.00'),
            unit_price=Decimal('16000.00'),
            total_price=Decimal('16000.00')
        )
        QuoteLineItem.objects.create(
            quote=self.quote,
            item_type='FUEL_SURCHARGE',
            description='Cross-country fuel surcharge & port transit permit',
            quantity=Decimal('1.00'),
            unit_price=Decimal('4000.00'),
            total_price=Decimal('4000.00')
        )

        # Create sample job
        self.job = LogisticsJob.objects.create(
            job_number='JOB-2026-TEST01',
            quote=self.quote,
            customer=self.customer,
            status='DISPATCHED',
            vehicle_registration='N 8899 WB',
            driver_name='Kavango Shikongo',
            driver_phone='+264 81 223 3445'
        )

        # Create sample invoice
        self.invoice = Invoice.objects.create(
            customer=self.customer,
            job=self.job,
            invoice_number='INV-2026-TEST01',
            invoice_type='PARTIAL_DEPOSIT',
            subtotal=Decimal('10000.00'),
            vat_amount=Decimal('1500.00'),
            total_amount=Decimal('11500.00'),
            amount_paid=Decimal('5000.00'),
            balance_due=Decimal('6500.00'),
            status='PARTIALLY_PAID',
            due_date=timezone.now().date() + datetime.timedelta(days=7),
            notes='50% mobilization deposit before dispatch.'
        )
        InvoiceLineItem.objects.create(
            invoice=self.invoice,
            description='50% Initial Mobilization Deposit for Heavy Freight (Job #JOB-2026-TEST01)',
            quantity=Decimal('1.00'),
            unit_price=Decimal('10000.00'),
            total_price=Decimal('10000.00')
        )

        # Create sample payment receipt
        self.receipt = PaymentReceipt.objects.create(
            customer=self.customer,
            invoice=self.invoice,
            receipt_number='RCP-2026-TEST01',
            amount_paid=Decimal('5000.00'),
            payment_method='EFT_BANK_TRANSFER',
            transaction_reference='FNB-EFT-9928172',
            payment_date=timezone.now()
        )

    def test_invoice_preview_view(self):
        """Test the canonical Invoice preview view."""
        url = reverse('invoice_preview', kwargs={'pk': self.invoice.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'INV-2026-TEST01')
        self.assertContains(response, 'Kalahari Trans-Logistics Ltd')
        self.assertContains(response, 'MENARD TRADING CC')
        self.assertContains(response, 'ALWAYS ON TIME')
        self.assertContains(response, 'Print')
        self.assertContains(response, 'Download PDF')


    def test_invoice_pdf_download(self):
        """Test downloading the canonical Invoice PDF."""
        url = reverse('invoice_pdf_download', kwargs={'pk': self.invoice.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF-'))
        self.assertGreater(len(response.content), 1000)

    def test_quotation_preview_view(self):
        """Test the canonical Quotation preview view."""
        url = reverse('quote_preview', kwargs={'pk': self.quote.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'QT-2026-TEST01')
        self.assertContains(response, 'Heavy Machinery Transport')
        self.assertContains(response, 'MENARD TRADING CC')

    def test_quotation_pdf_download(self):
        """Test downloading the canonical Quotation PDF."""
        url = reverse('quote_pdf_download', kwargs={'pk': self.quote.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF-'))
        self.assertGreater(len(response.content), 1000)

    def test_receipt_preview_view(self):
        """Test the canonical Payment Receipt preview view."""
        url = reverse('receipt_preview', kwargs={'pk': self.receipt.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'RCP-2026-TEST01')
        self.assertContains(response, 'Payment Received')
        self.assertContains(response, 'OFFICIAL RECEIPT')
        self.assertContains(response, 'FNB-EFT-9928172')

    def test_receipt_pdf_download(self):
        """Test downloading the canonical Payment Receipt PDF."""
        url = reverse('receipt_pdf_download', kwargs={'pk': self.receipt.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF-'))
        self.assertGreater(len(response.content), 1000)

    def test_public_quote_portal(self):
        """Test the public client quote approval portal uses the shared canonical application shell."""
        # Test as unauthenticated customer
        anon_client = Client()
        url = reverse('quote_portal', kwargs={'token': self.quote.approval_token})
        response = anon_client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'QT-2026-TEST01')
        self.assertContains(response, 'Approve Quotation')
        self.assertContains(response, 'MENARD TRADING CC')
        self.assertContains(response, 'ALWAYS ON TIME')
        self.assertContains(response, 'Customer Quotation Portal')
        self.assertContains(response, 'Print')
        self.assertContains(response, 'Download PDF')

    def test_direct_pdf_render_services(self):
        """Test direct PDF rendering functions in pdf_services.py."""
        invoice_pdf = generate_invoice_pdf(self.invoice)
        self.assertTrue(invoice_pdf.startswith(b'%PDF-'))

        quote_pdf = generate_quotation_pdf(self.quote)
        self.assertTrue(quote_pdf.startswith(b'%PDF-'))

        receipt_pdf = generate_receipt_pdf(self.receipt)
        self.assertTrue(receipt_pdf.startswith(b'%PDF-'))
