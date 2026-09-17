import json
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth.models import User
from django.conf import settings
from apps.customers.models import Customer
from apps.billing.models import Invoice, PaymentReceipt
from apps.quotes.models import Quotation
from apps.logistics.models import LogisticsJob
from apps.accounts.models import SystemPermission, Role, UserProfile, CompanySettings
from menard_core.brevo_email import send_departmental_email, resolve_department_key, clean_email


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

        self.job = LogisticsJob.objects.create(
            job_number='JOB-2026-9001',
            quote=self.quote,
            customer=self.customer,
        )

    def test_accounts_sender_standardization_in_settings(self):
        """Verify that accounts, invoicing, billing, and quotes emails use Menard Trading CC <accounts@menardtrading.com>."""
        accounts_cfg = settings.EMAIL_CONFIGS.get('accounts', {})
        invoicing_cfg = settings.EMAIL_CONFIGS.get('invoicing', {})
        billing_cfg = settings.EMAIL_CONFIGS.get('billing', {})
        quotes_cfg = settings.EMAIL_CONFIGS.get('quotes', {})
        
        self.assertEqual(accounts_cfg.get('DEFAULT_FROM_EMAIL'), 'Menard Trading CC <accounts@menardtrading.com>')
        self.assertEqual(invoicing_cfg.get('DEFAULT_FROM_EMAIL'), 'Menard Trading CC <accounts@menardtrading.com>')
        self.assertEqual(billing_cfg.get('DEFAULT_FROM_EMAIL'), 'Menard Trading CC <accounts@menardtrading.com>')
        self.assertEqual(quotes_cfg.get('DEFAULT_FROM_EMAIL'), 'Menard Trading CC <accounts@menardtrading.com>')
        self.assertEqual(settings.EMAIL_BRANDING.get('accounts_email'), 'accounts@menardtrading.com')
        self.assertEqual(settings.EMAIL_BRANDING.get('billing_email'), 'accounts@menardtrading.com')

    def test_support_channel_independence_in_settings(self):
        """Verify that support channel uses support@menardtrading.com and does NOT inherit billing/accounts sender."""
        support_cfg = settings.EMAIL_CONFIGS.get('support', {})
        self.assertEqual(support_cfg.get('DEFAULT_FROM_EMAIL'), 'Menard Trading CC <support@menardtrading.com>')
        self.assertNotIn('billing@menardtrading.com', support_cfg.get('DEFAULT_FROM_EMAIL', ''))
        self.assertNotIn('accounts@menardtrading.com', support_cfg.get('DEFAULT_FROM_EMAIL', ''))
        self.assertEqual(settings.EMAIL_BRANDING.get('support_email'), 'support@menardtrading.com')

    def test_info_channel_independence_in_settings(self):
        """Verify that info channel uses info@menardtrading.com and does NOT inherit billing/accounts sender."""
        info_cfg = settings.EMAIL_CONFIGS.get('info', {})
        self.assertEqual(info_cfg.get('DEFAULT_FROM_EMAIL'), 'Menard Trading CC <info@menardtrading.com>')
        self.assertNotIn('billing@menardtrading.com', info_cfg.get('DEFAULT_FROM_EMAIL', ''))
        self.assertNotIn('accounts@menardtrading.com', info_cfg.get('DEFAULT_FROM_EMAIL', ''))

    def test_company_settings_branding_dict_defaults(self):
        """Verify that CompanySettings defaults and branding dictionary return proper channel addresses."""
        company_settings = CompanySettings.get_settings()
        branding = company_settings.as_branding_dict()
        self.assertEqual(branding['accounts_email'], 'accounts@menardtrading.com')
        self.assertEqual(branding['billing_email'], 'accounts@menardtrading.com')
        self.assertEqual(branding['support_email'], 'support@menardtrading.com')
        self.assertEqual(branding['quotes_email'], 'accounts@menardtrading.com')
        self.assertEqual(branding['orders_email'], 'orders@menardtrading.com')

    def test_department_purpose_resolver(self):
        """Verify resolve_department_key maps commercial/financial purposes to accounts and support to support."""
        self.assertEqual(resolve_department_key('accounts'), 'accounts')
        self.assertEqual(resolve_department_key('invoicing'), 'accounts')
        self.assertEqual(resolve_department_key('billing'), 'accounts')
        self.assertEqual(resolve_department_key('receipt'), 'accounts')
        self.assertEqual(resolve_department_key('financial'), 'accounts')
        self.assertEqual(resolve_department_key('support'), 'support')
        self.assertEqual(resolve_department_key('help'), 'support')
        self.assertEqual(resolve_department_key('info'), 'info')
        self.assertEqual(resolve_department_key('otp'), 'info')
        self.assertEqual(resolve_department_key('orders'), 'orders')
        self.assertEqual(resolve_department_key('operations'), 'operations')
        self.assertEqual(resolve_department_key('logistics'), 'operations')

    @override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
    def test_departmental_email_dispatch_headers(self):
        """Verify each department dispatches with exact From and Reply-To headers and attachments."""
        from django.core import mail
        cases = [
            ('accounts', 'emails/invoice_sent.html', {'invoice': self.invoice},
             'Menard Trading CC <accounts@menardtrading.com>', ['accounts@menardtrading.com'],
             [('INV-2026-9001.pdf', b'%PDF-1.7\nvalid', 'application/pdf')]),
            ('support', 'emails/po_received_noreply.html', {'po': None, 'customer_name': 'David'},
             'Menard Trading CC <support@menardtrading.com>', ['support@menardtrading.com'], None),
            ('info', 'emails/two_factor_otp.html', {'user_name': 'David', 'otp_code': '123456'},
             'Menard Trading CC <info@menardtrading.com>', ['info@menardtrading.com'], None),
            ('orders', 'emails/po_received.html', {'po': None, 'customer_name': 'David'},
             'Menard Trading Orders <orders@menardtrading.com>', ['orders@menardtrading.com'], None),
            ('operations', 'emails/pod_received.html', {'job': self.job},
             'Menard Trading Logistics <logistics@menardtrading.com>', ['logistics@menardtrading.com'], None),
            ('no-reply', 'emails/po_received_noreply.html', {'po': None, 'customer_name': 'David'},
             'Menard Trading No-Reply <no-reply@menardtrading.com>', ['orders@menardtrading.com'], None),
        ]

        for dept, tpl, ctx, expected_from, expected_reply_to, attachments in cases:
            mail.outbox.clear()
            success, msg = send_departmental_email(
                department=dept,
                recipient_list=['client@example.com'],
                subject=f'Test {dept}',
                template_name=tpl,
                context=ctx,
                attachments=attachments,
            )
            self.assertTrue(success, f"Failed for {dept}: {msg}")
            self.assertEqual(len(mail.outbox), 1, f"No email sent for {dept}")
            sent_msg = mail.outbox[0]
            self.assertEqual(sent_msg.from_email, expected_from, f"Mismatch From for {dept}")
            self.assertEqual(sent_msg.reply_to, expected_reply_to, f"Mismatch Reply-To for {dept}")
            if attachments:
                self.assertEqual(len(sent_msg.attachments), len(attachments))
                self.assertEqual(sent_msg.attachments[0][0], attachments[0][0])

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

    def test_webmail_menu_item_present_in_dashboard(self):
        """Verify Webmail link is rendered in user menu with correct URL, target, and descriptions."""
        response = self.client.get(reverse('dashboard_overview'))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode('utf-8')
        self.assertIn('https://cosmos-ai-labs.com:8090/snappymail/', content)
        self.assertIn('target="_blank"', content)
        self.assertIn('rel="noopener noreferrer"', content)
        self.assertIn('Webmail', content)
        self.assertIn('Access your Menard Trading email account', content)


