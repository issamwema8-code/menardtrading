from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.billing.models import Invoice
from apps.customers.models import Customer
from apps.orders.models import PurchaseOrder
from apps.quotes.models import QuoteLineItem, Quotation


class DocumentReferenceWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='reference-admin',
            email='admin@example.com',
            password='test-password',
        )
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(
            company_name='Into Afrique Logistics',
            contact_name='Piet',
            email='info@example.com',
            phone='+264 81 000 0000',
            physical_address='Henties Bay, Namibia',
        )
        self.po = PurchaseOrder.objects.create(
            po_number='PO-2026-0045',
            customer=self.customer,
            raw_email_sender=self.customer.email,
        )
        self.quote = Quotation.objects.create(
            purchase_order=self.po,
            customer=self.customer,
        )
        QuoteLineItem.objects.create(
            quote=self.quote,
            description='Transport service',
            quantity=Decimal('1'),
            unit_price=Decimal('1000'),
        )

    def test_po_reference_flows_into_new_quote_and_invoice(self):
        self.assertEqual(self.quote.reference_number, 'PO-2026-0045')
        invoice = Invoice.objects.create(
            quote=self.quote,
            customer=self.customer,
            due_date='2026-10-16',
        )
        self.assertEqual(invoice.reference_number, 'PO-2026-0045')
        self.assertEqual(invoice.po_number, 'PO-2026-0045')

    def test_manual_invoice_reference_is_not_overwritten(self):
        invoice = Invoice.objects.create(
            quote=self.quote,
            customer=self.customer,
            reference_number='PO-2026-0045',
            due_date='2026-10-16',
        )
        invoice.reference_number = 'PO-2026-0045-CORRECTED'
        invoice.save()
        self.po.po_number = 'PO-2026-9999'
        self.po.save(update_fields=['po_number'])
        invoice.refresh_from_db()
        self.assertEqual(invoice.reference_number, 'PO-2026-0045-CORRECTED')

    def test_authorized_user_can_edit_existing_invoice_reference(self):
        invoice = Invoice.objects.create(
            customer=self.customer,
            due_date='2026-10-16',
        )
        response = self.client.post(
            reverse('edit_invoice_reference', args=[invoice.pk]),
            {'reference_number': 'PO-2026-0045'},
        )
        self.assertRedirects(response, reverse('invoice_preview', args=[invoice.pk]))
        invoice.refresh_from_db()
        self.assertEqual(invoice.reference_number, 'PO-2026-0045')
