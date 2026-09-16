import json
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from django.conf import settings
from apps.customers.models import Customer
from apps.billing.models import Invoice, PaymentReceipt
from apps.quotes.models import Quotation
from apps.accounts.models import SystemPermission, Role, UserProfile, CompanySettings
from menard_core.brevo_email import send_departmental_email


class EmailUXAndSenderTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username='billing_officer', password='password123')
        
        # Grant permissions
        for codename in ['invoices.view', 'invoices.create', 'invoices.download', 'receipts.view', 'receipts.download', 'quotations.view', 'quotations.send']:
            perm = SystemPermission.objects.filter(codename=codename).first()
            if not perm:
                SystemPermission.objects.create(codename=codename, name=codename, module='billing')
        role, _ = Role.objects.get_or_create(name='Billing Manager')
        role.permissions.set(SystemPermission.objects.all())
        profile, _ = UserProfile.objects.get_or_create(user=self.user)
        profile.roles.add(role)
        self.client.force_login(self.user)

        self.customer = Customer.objects.create(
            company_name='Atlas Freight Ltd',
            contact_name='David Miller',
            email='david@atlasfreight.co.za',
            payment_terms='30_DAYS_NET',
        )

        self.invoice = Invoice.objects.create(
            invoice_number='INV-2026-9001',
            customer=self.customer,
            subtotal=Decimal('10000.00'),
            vat_rate=Decimal('0.00'),
            vat_amount=Decimal('0.00'),
            total_amount=Decimal('10000.00'),
            balance_due=Decimal('10000.00'),
            due_date='2026-10-31',
        )

        self.receipt = PaymentReceipt.objects.create(
            receipt_number='RCP-2026-9001',
            invoice=self.invoice,
            customer=self.customer,
            amount_paid=Decimal('10000.00'),
            payment_method='EFT_BANK_TRANSFER',
            payment_date='2026-10-15',
            transaction_reference='FNB-998877',
        )

        self.quote = Quotation.objects.create(
            quote_number='QT-2026-9001',
            customer=self.customer,
            subtotal=Decimal('10000.00'),
            total_amount=Decimal('10000.00'),
        )

    def test_billing_sender_standardization_in_settings(self):
        """Verify that billing/invoicing emails use Menard Trading CC <billing@menardtrading.com>."""
        invoicing_cfg = settings.EMAIL_CONFIGS.get('invoicing', {})
        billing_cfg = settings.EMAIL_CONFIGS.get('billing', {})
        
        self.assertEqual(invoicing_cfg.get('DEFAULT_FROM_EMAIL'), 'Menard Trading CC <billing@menardtrading.com>')
        self.assertEqual(billing_cfg.get('DEFAULT_FROM_EMAIL'), 'Menard Trading CC <billing@menardtrading.com>')
        self.assertEqual(settings.EMAIL_BRANDING.get('billing_email'), 'billing@menardtrading.com')

    def test_company_settings_branding_dict_uses_billing_email(self):
        """Verify that CompanySettings defaults and branding dictionary return billing@menardtrading.com."""
        company_settings = CompanySettings.get_settings()
        branding = company_settings.as_branding_dict()
        self.assertIn('billing@menardtrading.com', branding['accounts_email'])
        self.assertIn('billing@menardtrading.com', branding['billing_email'])

    @patch('apps.billing.views.send_departmental_email', return_value=(True, 'Delivered'))
    def test_send_invoice_email_with_custom_recipient_and_composer_data(self, mock_send):
        """Verify SendInvoiceEmailView dispatches to custom recipient with clean business feedback."""
        url = reverse('send_invoice_email', args=[self.invoice.pk])
        response = self.client.post(url, {
            'recipient_email': 'custom.finance@atlasfreight.co.za',
            'subject': 'Custom Subject: Tax Invoice INV-2026-9001',
            'message': 'Please process payment on receipt.',
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse('invoice_preview', args=[self.invoice.pk]))
        
        # Verify clean business success message (no Brevo wording)
        messages = list(response.context['messages'])
        self.assertEqual(len(messages), 1)
        self.assertIn('Invoice sent successfully', messages[0].message)
        self.assertIn('custom.finance@atlasfreight.co.za', messages[0].message)
        self.assertNotIn('Brevo', messages[0].message)
        self.assertNotIn('SMTP', messages[0].message)

        # Verify mock call parameters
        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args.kwargs
        self.assertEqual(call_kwargs['department'], 'invoicing')
        self.assertEqual(call_kwargs['recipient_list'], ['custom.finance@atlasfreight.co.za'])
        self.assertEqual(call_kwargs['subject'], 'Custom Subject: Tax Invoice INV-2026-9001')
        self.assertEqual(call_kwargs['context']['custom_message'], 'Please process payment on receipt.')

    @patch('apps.billing.views.send_departmental_email', return_value=(True, 'Delivered'))
    def test_send_receipt_email_with_custom_recipient_and_composer_data(self, mock_send):
        """Verify SendReceiptEmailView dispatches receipt with clean business feedback."""
        url = reverse('send_receipt_email', args=[self.receipt.pk])
        response = self.client.post(url, {
            'recipient_email': 'remittance@atlasfreight.co.za',
            'subject': 'Payment Receipt RCP-2026-9001 Settlement Confirmation',
            'message': 'We have received your EFT settlement in full.',
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse('receipt_preview', args=[self.receipt.pk]))

        # Verify clean business success message
        messages = list(response.context['messages'])
        self.assertEqual(len(messages), 1)
        self.assertIn('Receipt sent successfully', messages[0].message)
        self.assertIn('remittance@atlasfreight.co.za', messages[0].message)
        self.assertNotIn('Brevo', messages[0].message)

        mock_send.assert_called_once()
        call_kwargs = mock_send.call_args.kwargs
        self.assertEqual(call_kwargs['department'], 'invoicing')
        self.assertEqual(call_kwargs['recipient_list'], ['remittance@atlasfreight.co.za'])

    @patch('apps.quotes.views.send_departmental_email', return_value=(True, 'Delivered'))
    def test_send_quote_email_with_custom_recipient(self, mock_send):
        """Verify SendQuoteEmailView dispatches quote with clean business feedback."""
        url = reverse('send_quote_email', args=[self.quote.pk])
        response = self.client.post(url, {
            'recipient_email': 'procurement@atlasfreight.co.za',
            'subject': 'Quotation QT-2026-9001 – Menard Trading CC',
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        messages = list(response.context['messages'])
        self.assertEqual(len(messages), 1)
        self.assertIn('Quotation sent successfully', messages[0].message)
        self.assertIn('procurement@atlasfreight.co.za', messages[0].message)
        self.assertNotIn('Brevo', messages[0].message)

    @patch('apps.billing.views.generate_invoice_pdf', return_value=b'corrupt_non_pdf')
    def test_send_invoice_fails_gracefully_on_invalid_pdf(self, mock_pdf):
        """Verify that corrupted/empty PDFs display a clean business-friendly error without stack traces."""
        url = reverse('send_invoice_email', args=[self.invoice.pk])
        response = self.client.post(url, {
            'recipient_email': 'test@example.com',
        }, follow=True)

        self.assertEqual(response.status_code, 200)
        messages = list(response.context['messages'])
        self.assertEqual(len(messages), 1)
        self.assertIn('Unable to send invoice', messages[0].message)
        self.assertIn('The invoice document could not be prepared', messages[0].message)
        self.assertNotIn('Traceback', messages[0].message)
        self.assertNotIn('Exception', messages[0].message)
