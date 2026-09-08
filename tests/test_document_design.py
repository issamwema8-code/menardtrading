from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
import datetime

from apps.customers.models import Customer
from apps.orders.models import PurchaseOrder, OrderCommunication
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

    def test_create_custom_multi_item_quotation(self):
        """Test creating a new standalone quotation with multiple line items."""
        url = reverse('create_quotation')
        data = {
            'customer': self.customer.id,
            'descriptions[]': ['Aluminium Double Doors (Custom)', 'Tinted Glass Windows 1200x900', 'Delivery & Installation'],
            'quantities[]': ['2.00', '6.00', '1.00'],
            'unit_prices[]': ['4500.00', '1200.00', '2500.00'],
            'validity_days': '21',
            'notes': 'Fabrication timeline 5 working days upon deposit.'
        }
        response = self.client.post(url, data, follow=True)
        self.assertEqual(response.status_code, 200)

        # Retrieve created quote
        quote = Quotation.objects.filter(customer=self.customer, notes__icontains='Fabrication timeline').latest('created_at')
        self.assertIsNone(quote.purchase_order)
        self.assertEqual(quote.line_items.count(), 3)
        # Expected subtotal: (2 * 4500) + (6 * 1200) + (1 * 2500) = 9000 + 7200 + 2500 = 18700
        self.assertEqual(quote.subtotal, Decimal('18700.00'))
        # VAT 15% of 18700 = 2805.00, Total = 21505.00
        self.assertEqual(quote.vat_amount, Decimal('2805.00'))
        self.assertEqual(quote.total_amount, Decimal('21505.00'))

    def test_create_direct_multi_item_invoice(self):
        """Test creating a direct standalone invoice with multiple line items without requiring a quote."""
        url = reverse('create_invoice')
        data = {
            'customer': self.customer.id,
            'invoice_type': 'FULL',
            'payment_terms': 'Immediate EFT / Cash',
            'descriptions[]': ['Heavy Duty Steel Security Gate', 'Padlocks & Anchor Bolts'],
            'quantities[]': ['1.00', '4.00'],
            'unit_prices[]': ['8500.00', '350.00'],
            'notes': 'Direct walk-in counter sale.'
        }
        response = self.client.post(url, data, follow=True)
        self.assertEqual(response.status_code, 200)

        # Retrieve created invoice
        invoice = Invoice.objects.filter(customer=self.customer, notes__icontains='Direct walk-in').latest('created_at')
        self.assertIsNone(invoice.job)
        self.assertIsNone(invoice.quote)
        self.assertEqual(invoice.line_items.count(), 2)
        # Expected subtotal: (1 * 8500) + (4 * 350) = 8500 + 1400 = 9900.00
        self.assertEqual(invoice.subtotal, Decimal('9900.00'))
        # VAT 15% of 9900 = 1485.00, Total = 11385.00
        self.assertEqual(invoice.vat_amount, Decimal('1485.00'))
        self.assertEqual(invoice.total_amount, Decimal('11385.00'))
        self.assertEqual(invoice.balance_due, Decimal('11385.00'))
        self.assertEqual(invoice.status, 'ISSUED')

    def test_quotation_data_api(self):
        """Test the JSON data endpoint for a quotation."""
        url = reverse('quote_data_api', kwargs={'pk': self.quote.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['quote_number'], 'QT-2026-TEST01')
        self.assertEqual(data['customer_id'], self.customer.id)
        self.assertEqual(len(data['items']), 2)

    def test_update_quotation(self):
        """Test editing a quotation: modifying customer, notes, status, and replacing line items."""
        # Create a new draft quote
        new_quote = Quotation.objects.create(
            customer=self.customer,
            quote_number='QT-EDIT-001',
            status='DRAFT'
        )
        QuoteLineItem.objects.create(
            quote=new_quote,
            description='Original Item',
            quantity=Decimal('1.00'),
            unit_price=Decimal('1000.00'),
            total_price=Decimal('1000.00')
        )
        new_quote.recalculate_totals()

        url = reverse('update_quotation', kwargs={'pk': new_quote.id})
        data = {
            'customer_id': self.customer.id,
            'notes': 'Updated quotation terms with special discounts.',
            'status': 'SENT',
            'validity_days': '30',
            'descriptions[]': ['Custom Front Door', 'Double Glazed Window 1500x1200'],
            'quantities[]': ['2.00', '4.00'],
            'unit_prices[]': ['3500.00', '1500.00']
        }
        response = self.client.post(url, data, follow=True)
        self.assertEqual(response.status_code, 200)

        new_quote.refresh_from_db()
        self.assertEqual(new_quote.status, 'SENT')
        self.assertIn('special discounts', new_quote.notes)
        self.assertEqual(new_quote.line_items.count(), 2)
        # Expected subtotal: (2 * 3500) + (4 * 1500) = 7000 + 6000 = 13000
        self.assertEqual(new_quote.subtotal, Decimal('13000.00'))
        # VAT 15% of 13000 = 1950.00, Total = 14950.00
        self.assertEqual(new_quote.vat_amount, Decimal('1950.00'))
        self.assertEqual(new_quote.total_amount, Decimal('14950.00'))

    def test_delete_quotation(self):
        """Test deleting a standalone quotation without linked jobs."""
        standalone_quote = Quotation.objects.create(
            customer=self.customer,
            quote_number='QT-DEL-001',
            status='DRAFT'
        )
        QuoteLineItem.objects.create(
            quote=standalone_quote,
            description='Temporary Item to delete',
            quantity=Decimal('1.00'),
            unit_price=Decimal('500.00'),
            total_price=Decimal('500.00')
        )
        quote_id = standalone_quote.id

        url = reverse('delete_quotation', kwargs={'pk': quote_id})
        response = self.client.post(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Quotation.objects.filter(id=quote_id).exists())
        self.assertFalse(QuoteLineItem.objects.filter(quote_id=quote_id).exists())

    def test_delete_quotation_with_active_job_protected(self):
        """Test that deleting a quote with an active logistics job is blocked."""
        url = reverse('delete_quotation', kwargs={'pk': self.quote.id})
        response = self.client.post(url, follow=True)
        self.assertEqual(response.status_code, 200)
        # Quote must still exist because self.job is linked
        self.assertTrue(Quotation.objects.filter(id=self.quote.id).exists())

    def test_purchase_order_data_api(self):
        """Test the JSON data endpoint for a purchase order."""
        po = PurchaseOrder.objects.create(
            po_number='PO-API-001',
            customer=self.customer,
            cargo_description='Mining Drill Bits & Lubricants',
            weight_tons=Decimal('14.50'),
            volume_cbm=Decimal('22.00'),
            quantity_pallets=10,
            pickup_location='Swakopmund Yard',
            delivery_location='Rundu Logistics Depot'
        )
        url = reverse('po_data_api', kwargs={'pk': po.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data['success'])
        self.assertEqual(data['po_number'], 'PO-API-001')
        self.assertEqual(data['weight_tons'], '14.50')
        self.assertEqual(data['quantity_pallets'], 10)

    def test_merge_purchase_orders(self):
        """Test consolidating two separate customer POs into a single master order."""
        # Create Primary PO
        po1 = PurchaseOrder.objects.create(
            po_number='PO-MERGE-001',
            customer=self.customer,
            cargo_description='10x Steel Structural Beams',
            weight_tons=Decimal('12.00'),
            volume_cbm=Decimal('15.00'),
            quantity_pallets=4,
            pickup_location='Windhoek Industrial Hub',
            delivery_location='Walvis Bay Port',
            special_instructions='Crane required for offloading.'
        )
        quote1 = Quotation.objects.create(
            purchase_order=po1,
            customer=self.customer,
            quote_number='QT-MERGE-001',
            status='DRAFT'
        )
        QuoteLineItem.objects.create(
            quote=quote1,
            description='Steel Beams Freight transit',
            quantity=Decimal('12.00'),
            unit_price=Decimal('1000.00'),
            total_price=Decimal('12000.00')
        )
        quote1.recalculate_totals()

        # Create Secondary PO
        po2 = PurchaseOrder.objects.create(
            po_number='PO-MERGE-002',
            customer=self.customer,
            cargo_description='5x Anchor Bolts & Fittings',
            weight_tons=Decimal('3.50'),
            volume_cbm=Decimal('5.00'),
            quantity_pallets=2,
            pickup_location='Windhoek Industrial Hub',
            delivery_location='Walvis Bay Port',
            special_instructions='Handle with care.'
        )
        quote2 = Quotation.objects.create(
            purchase_order=po2,
            customer=self.customer,
            quote_number='QT-MERGE-002',
            status='DRAFT'
        )
        QuoteLineItem.objects.create(
            quote=quote2,
            description='Anchor Bolts Freight add-on',
            quantity=Decimal('3.50'),
            unit_price=Decimal('800.00'),
            total_price=Decimal('2800.00')
        )
        quote2.recalculate_totals()

        # Add communication on secondary PO
        OrderCommunication.objects.create(
            purchase_order=po2,
            sender_department='orders',
            recipient_email=self.customer.email,
            subject='PO Confirmation',
            message_body='Received second batch PO'
        )

        url = reverse('merge_purchase_orders')
        data = {
            'primary_po_id': po1.id,
            'secondary_po_id': po2.id,
            'combined_po_number': 'PO-MERGE-001 / PO-MERGE-002',
            'weight_tons': '15.50',
            'volume_cbm': '20.00',
            'quantity_pallets': '6',
        }
        response = self.client.post(url, data, follow=True)
        self.assertEqual(response.status_code, 200)

        po1.refresh_from_db()
        po2.refresh_from_db()

        # Verify Master PO is updated
        self.assertEqual(po1.po_number, 'PO-MERGE-001 / PO-MERGE-002')
        self.assertEqual(po1.weight_tons, Decimal('15.50'))
        self.assertEqual(po1.volume_cbm, Decimal('20.00'))
        self.assertEqual(po1.quantity_pallets, 6)
        self.assertIn('Steel Structural Beams', po1.cargo_description)
        self.assertIn('Anchor Bolts & Fittings', po1.cargo_description)

        # Verify Secondary PO is cancelled/archived
        self.assertEqual(po2.status, PurchaseOrder.Status.CANCELLED)
        self.assertIn('Merged into PO', po2.special_instructions)

        # Verify communication transfer
        self.assertEqual(po1.communications.count(), 1)

        # Verify Quotation line item transfer: primary quote now has both items
        quote1.refresh_from_db()
        self.assertEqual(quote1.line_items.count(), 2)
        # Expected subtotal: 12000 + 2800 = 14800.00
        self.assertEqual(quote1.subtotal, Decimal('14800.00'))
        self.assertFalse(Quotation.objects.filter(id=quote2.id).exists())



