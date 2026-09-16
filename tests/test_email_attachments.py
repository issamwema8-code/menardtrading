from decimal import Decimal

from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone

from menard_core.brevo_email import send_departmental_email
from apps.orders.models import DocumentEmailDelivery, PurchaseOrder
from apps.billing.models import Invoice, PaymentReceipt
from apps.customers.models import Customer
from apps.quotes.models import Quotation


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class DocumentEmailAttachmentTests(TestCase):
    def _invoice_context(self):
        customer = Customer.objects.create(
            company_name='Attachment Client',
            contact_name='Accounts',
            email='client@example.com',
            phone='000',
            physical_address='Namibia',
        )
        invoice = Invoice.objects.create(
            invoice_number='INV-2026-0003',
            customer=customer,
            due_date=timezone.localdate(),
        )
        return {'invoice': invoice}

    def test_invoice_email_contains_valid_pdf_attachment(self):
        success, message = send_departmental_email(
            department='invoicing',
            recipient_list=['client@example.com'],
            subject='Tax Invoice #INV-2026-0003',
            template_name='emails/invoice_sent.html',
            context=self._invoice_context(),
            attachments=[('INV-2026-0003.pdf', b'%PDF-1.7\nvalid', 'application/pdf')],
        )
        self.assertTrue(success, message)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual([(a[0], a[1], a[2]) for a in mail.outbox[0].attachments], [
            ('INV-2026-0003.pdf', b'%PDF-1.7\nvalid', 'application/pdf')
        ])
        delivery = DocumentEmailDelivery.objects.get(document_id='INV-2026-0003')
        self.assertEqual(delivery.status, DocumentEmailDelivery.Status.SENT)
        self.assertEqual(delivery.attachment_manifest[0]['size'], len(b'%PDF-1.7\nvalid'))

    def test_claimed_attachment_without_bytes_is_not_sent(self):
        success, message = send_departmental_email(
                department='invoicing',
                recipient_list=['client@example.com'],
                subject='Tax Invoice #INV-2026-0003',
                template_name='emails/invoice_sent.html',
                context=self._invoice_context(),
                attachments=None,
            )
        self.assertFalse(success)
        self.assertIn('attachment', message.lower())
        self.assertEqual(len(mail.outbox), 0)
        delivery = DocumentEmailDelivery.objects.get(document_id='INV-2026-0003')
        self.assertEqual(delivery.status, DocumentEmailDelivery.Status.ATTACHMENT_FAILED)

    def test_empty_pdf_is_not_sent(self):
        success, message = send_departmental_email(
                department='invoicing',
                recipient_list=['client@example.com'],
                subject='Tax Invoice #INV-2026-0003',
                template_name='emails/invoice_sent.html',
                context=self._invoice_context(),
                attachments=[('INV-2026-0003.pdf', b'', 'application/pdf')],
            )
        self.assertFalse(success)
        self.assertIn('empty', message.lower())
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_pdf_and_partial_multi_attachment_are_not_sent(self):
        success, message = send_departmental_email(
            department='invoicing',
            recipient_list=['client@example.com'],
            subject='Tax Invoice #INV-2026-0003',
            template_name='emails/invoice_sent.html',
            context=self._invoice_context(),
            attachments=[
                ('INV-2026-0003.pdf', b'not-a-pdf', 'application/pdf'),
                ('supporting.txt', b'valid', 'text/plain'),
            ],
        )
        self.assertFalse(success)
        self.assertIn('valid PDF', message)
        self.assertEqual(len(mail.outbox), 0)

    def test_quote_receipt_and_outgoing_po_emails_attach_documents(self):
        customer = Customer.objects.create(
            company_name='Provider Client', contact_name='Accounts',
            email='client@example.com', phone='000', physical_address='Namibia',
        )
        quote = Quotation.objects.create(customer=customer, quote_number='QT-2026-0003')
        receipt_invoice = Invoice.objects.create(customer=customer, due_date=timezone.localdate())
        receipt = PaymentReceipt.objects.create(
            receipt_number='RCP-2026-0003', invoice=receipt_invoice, customer=customer,
            amount_paid='10.00', transaction_reference='TEST-1',
        )
        po = PurchaseOrder.objects.create(
            po_number='PO-OUT-2026-0003', direction=PurchaseOrder.Direction.OUTGOING,
            supplier=customer, recipient_email='client@example.com', status=PurchaseOrder.Status.DRAFT,
            cargo_description='Transport service', quantity=Decimal('1'), unit_price=Decimal('10'),
        )
        cases = [
            ('quotes', 'emails/quote_sent.html', {'quote': quote, 'approval_url': 'https://example.com/approve'}, 'QT-2026-0003.pdf'),
            ('invoicing', 'emails/receipt_sent.html', {'receipt': receipt}, 'RCP-2026-0003.pdf'),
            ('orders', 'emails/outgoing_po_sent.html', {'po': po, 'recipient_name': 'Accounts'}, 'PO-OUT-2026-0003.pdf'),
        ]
        for department, template, context, filename in cases:
            mail.outbox.clear()
            success, message = send_departmental_email(
                department=department,
                recipient_list=['client@example.com'],
                subject=f'Document {filename}',
                template_name=template,
                context=context,
                attachments=[(filename, b'%PDF-1.7\nvalid', 'application/pdf')],
            )
            self.assertTrue(success, message)
            self.assertEqual(mail.outbox[0].attachments[0][0], filename)
