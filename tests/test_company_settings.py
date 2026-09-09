from decimal import Decimal
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model
from django.utils import timezone
import datetime

from apps.accounts.models import CompanySettings, Role, SystemPermission, AuditLog
from apps.customers.models import Customer
from apps.quotes.models import Quotation, QuoteLineItem
from apps.billing.models import Invoice, InvoiceLineItem, PaymentReceipt
from apps.billing.pdf_services import generate_invoice_pdf, generate_quotation_pdf, generate_receipt_pdf
from menard_core.context_processors import branding_context

User = get_user_model()


class CompanySettingsTestCase(TestCase):
    """
    Test suite verifying centralized dynamic business information,
    default initializations, RBAC permissions, and single source of truth across
    documents, templates, PDFs, and emails.
    """

    def setUp(self):
        self.client = Client()

        # Admin user
        self.admin_user = User.objects.create_superuser(
            username='admin_settings_tester',
            email='admin@menardtrading.com',
            password='Password123!'
        )

        # Standard user without settings permissions
        self.standard_user = User.objects.create_user(
            username='standard_operator',
            email='operator@menardtrading.com',
            password='Password123!'
        )

        # Customer record
        self.customer = Customer.objects.create(
            company_name='Namibia Mining Logistics Ltd',
            contact_name='Helena Shilongo',
            email='logistics@namibiamining.na',
            phone='+264 81 777 8888',
            vat_number='NA-44556677-V99',
            billing_address='PO Box 987, Swakopmund, Namibia',
            physical_address='Plot 12, Mining Way, Swakopmund, Namibia',
            payment_terms='30 Days Net'
        )

        # Quotation
        self.quote = Quotation.objects.create(
            customer=self.customer,
            quote_number='QT-2026-CS01',
            subtotal=Decimal('35000.00'),
            vat_rate=Decimal('15.00'),
            vat_amount=Decimal('5250.00'),
            total_amount=Decimal('40250.00'),
            status=Quotation.Status.SENT,
            valid_until=timezone.now().date() + datetime.timedelta(days=14)
        )

        # Invoice
        self.invoice = Invoice.objects.create(
            customer=self.customer,
            invoice_number='INV-2026-CS01',
            invoice_type=Invoice.InvoiceType.FULL,
            subtotal=Decimal('35000.00'),
            vat_amount=Decimal('5250.00'),
            total_amount=Decimal('40250.00'),
            amount_paid=Decimal('0.00'),
            balance_due=Decimal('40250.00'),
            due_date=timezone.now().date() + datetime.timedelta(days=30),
            status=Invoice.Status.ISSUED
        )

        # Payment Receipt
        self.receipt = PaymentReceipt.objects.create(
            customer=self.customer,
            invoice=self.invoice,
            receipt_number='RCP-2026-CS01',
            amount_paid=Decimal('40250.00'),
            payment_method=PaymentReceipt.PaymentMethod.EFT,
            transaction_reference='EFT-BANK-CONF-7711',
            payment_date=timezone.now().date()
        )

    def test_default_company_settings_initialization(self):
        """Verify initial default business values as required by specifications."""
        settings_obj = CompanySettings.get_settings()
        self.assertEqual(settings_obj.company_name, "MENARD TRADING CC")
        self.assertEqual(settings_obj.postal_address, "P O BOX 497-19001,")
        self.assertEqual(settings_obj.city, "RUNDU")
        self.assertEqual(settings_obj.country, "NAMIBIA")
        self.assertEqual(settings_obj.tagline, "ALWAYS ON TIME")
        self.assertIn("P O BOX 497-19001, RUNDU - NAMIBIA", settings_obj.get_formatted_address())
        
        # Verify defaults
        self.assertEqual(settings_obj.vat_number, "")
        self.assertEqual(settings_obj.vat_rate, Decimal('0.00'))
        self.assertEqual(settings_obj.company_reg_number, "")
        self.assertEqual(settings_obj.phone, "+264 81 445 5188")

    def test_rbac_protection_on_company_settings(self):
        """Unauthorized users cannot view or edit company settings."""
        url = reverse('administration-company-settings')

        # Anonymous redirect
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

        # Operator without settings perm gets 403
        self.client.force_login(self.standard_user)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 403)

        # Post attempt is forbidden
        response = self.client.post(url, {'company_name': 'Hacker Logistics CC'})
        self.assertEqual(response.status_code, 403)

    def test_admin_can_update_company_settings(self):
        """Authorized admin can update business details and changes take effect."""
        self.client.force_login(self.admin_user)
        url = reverse('administration-company-settings')

        # GET form
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Company &amp; Business Information')
        self.assertContains(response, 'MENARD TRADING CC')

        # POST update
        post_data = {
            'company_name': 'MENARD LOGISTICS GROUP CC',
            'tagline': 'FORWARDING EXCELLENCE',
            'postal_address': 'P O BOX 500-20002,',
            'physical_address': 'Plot 88, Trans-Caprivi Highway',
            'city': 'RUNDU',
            'country': 'NAMIBIA',
            'vat_number': 'VAT-NAM-990011',
            'company_reg_number': 'CC/2026/887766',
            'email': 'contact@menardgroup.com',
            'orders_email': 'dispatch@menardgroup.com',
            'quotes_email': 'quotes@menardgroup.com',
            'accounts_email': 'billing@menardgroup.com',
            'phone': '+264 66 255 1234',
            'mobile': '+264 81 555 9999',
            'website': 'https://menardgroup.com',
            'bank_name': 'Standard Bank Namibia',
            'account_name': 'Menard Logistics Group CC',
            'account_number': '089988776655',
            'account_type': 'Business Current Account',
            'branch_code': '082372',
            'branch_name': 'Rundu Branch',
            'swift_code': 'SBICNANX',
        }

        response = self.client.post(url, post_data)
        self.assertEqual(response.status_code, 302)

        # Verify DB updated
        settings_obj = CompanySettings.get_settings()
        self.assertEqual(settings_obj.company_name, 'MENARD LOGISTICS GROUP CC')
        self.assertEqual(settings_obj.tagline, 'FORWARDING EXCELLENCE')
        self.assertEqual(settings_obj.vat_number, 'VAT-NAM-990011')
        self.assertEqual(settings_obj.phone, '+264 66 255 1234')
        self.assertEqual(settings_obj.bank_name, 'Standard Bank Namibia')

        # Verify audit log was recorded
        audit = AuditLog.objects.filter(action='COMPANY_SETTINGS_UPDATED').first()
        self.assertIsNotNone(audit)
        self.assertEqual(audit.user, self.admin_user)

    def test_dynamic_reflection_in_documents_and_previews(self):
        """Updating CompanySettings immediately reflects in Quotations, Invoices, and Receipts."""
        # Update settings
        settings_obj = CompanySettings.get_settings()
        settings_obj.company_name = "MENARD EXPEDITED FREIGHT CC"
        settings_obj.tagline = "SWIFT RELIABLE TRANSIT"
        settings_obj.vat_number = "VAT-77889900"
        settings_obj.phone = "+264 66 111 2233"
        settings_obj.bank_name = "Nedbank Namibia"
        settings_obj.account_number = "1100998877"
        settings_obj.save()

        self.client.force_login(self.admin_user)

        # 1. Invoice Preview
        inv_url = reverse('invoice_preview', kwargs={'pk': self.invoice.id})
        response = self.client.get(inv_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'MENARD EXPEDITED FREIGHT CC')
        self.assertContains(response, 'SWIFT RELIABLE TRANSIT')
        self.assertContains(response, 'VAT-77889900')
        self.assertContains(response, 'Nedbank Namibia')
        # Customer details must come from customer record
        self.assertContains(response, 'Namibia Mining Logistics Ltd')
        self.assertContains(response, 'NA-44556677-V99')

        # 2. Quotation Preview
        quote_url = reverse('quote_preview', kwargs={'pk': self.quote.id})
        response = self.client.get(quote_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'MENARD EXPEDITED FREIGHT CC')
        self.assertContains(response, 'SWIFT RELIABLE TRANSIT')
        self.assertContains(response, 'Namibia Mining Logistics Ltd')

        # 3. Receipt Preview
        rcp_url = reverse('receipt_preview', kwargs={'pk': self.receipt.id})
        response = self.client.get(rcp_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'MENARD EXPEDITED FREIGHT CC')
        self.assertContains(response, 'SWIFT RELIABLE TRANSIT')
        self.assertContains(response, 'Namibia Mining Logistics Ltd')

        # 4. Direct PDF generation
        inv_pdf = generate_invoice_pdf(self.invoice)
        self.assertTrue(inv_pdf.startswith(b'%PDF-'))
        quote_pdf = generate_quotation_pdf(self.quote)
        self.assertTrue(quote_pdf.startswith(b'%PDF-'))
        rcp_pdf = generate_receipt_pdf(self.receipt)
        self.assertTrue(rcp_pdf.startswith(b'%PDF-'))

    def test_background_images_upload_and_branding_dict(self):
        """Verify custom background images can be uploaded, updated, and exposed via branding."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.client.force_login(self.admin_user)

        # 1x1 dummy PNG
        dummy_png = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
        bg1_file = SimpleUploadedFile('custom_bg1.png', dummy_png, content_type='image/png')
        bg2_file = SimpleUploadedFile('custom_bg2.png', dummy_png, content_type='image/png')

        post_data = {
            'company_name': 'MENARD TRADING CC',
            'tagline': 'ALWAYS ON TIME',
            'postal_address': 'P O BOX 497-19001,',
            'city': 'RUNDU',
            'country': 'NAMIBIA',
            'email': 'info@menardtrading.com',
            'orders_email': 'orders@menardtrading.com',
            'quotes_email': 'quotes@menardtrading.com',
            'accounts_email': 'accounts@menardtrading.com',
            'login_bg_image_1': bg1_file,
            'login_bg_image_2': bg2_file,
            'login_bg_image_3': SimpleUploadedFile('custom_bg3.jpg', b'fake_bg_image_bytes_3', content_type='image/jpeg'),
        }

        resp = self.client.post(reverse('administration-company-settings'), post_data, follow=True)
        self.assertEqual(resp.status_code, 200)

        settings_obj = CompanySettings.get_settings()
        self.assertTrue(settings_obj.login_bg_image_1)
        self.assertTrue(settings_obj.login_bg_image_2)
        self.assertTrue(settings_obj.login_bg_image_3)

        branding = settings_obj.as_branding_dict()
        self.assertTrue(branding['has_custom_login_bg_1'])
        self.assertTrue(branding['has_custom_login_bg_2'])
        self.assertTrue(branding['has_custom_login_bg_3'])
        self.assertIn('custom_bg1', branding['login_bg_image_1'])
        self.assertIn('custom_bg2', branding['login_bg_image_2'])
        self.assertIn('custom_bg3', branding['login_bg_image_3'])

        # Test removal / reset to default
        reset_post_data = {
            'company_name': 'MENARD TRADING CC',
            'tagline': 'ALWAYS ON TIME',
            'postal_address': 'P O BOX 497-19001,',
            'city': 'RUNDU',
            'country': 'NAMIBIA',
            'email': 'info@menardtrading.com',
            'orders_email': 'orders@menardtrading.com',
            'quotes_email': 'quotes@menardtrading.com',
            'accounts_email': 'accounts@menardtrading.com',
            'remove_login_bg_1': '1',
            'remove_login_bg_2': '1',
            'remove_login_bg_3': '1',
        }
        resp = self.client.post(reverse('administration-company-settings'), reset_post_data, follow=True)
        self.assertEqual(resp.status_code, 200)

        settings_obj = CompanySettings.get_settings()
        self.assertFalse(bool(settings_obj.login_bg_image_1))
        self.assertFalse(bool(settings_obj.login_bg_image_2))
        self.assertFalse(bool(settings_obj.login_bg_image_3))

        branding = settings_obj.as_branding_dict()
        self.assertFalse(branding['has_custom_login_bg_1'])
        self.assertFalse(branding['has_custom_login_bg_2'])
        self.assertFalse(branding['has_custom_login_bg_3'])
        self.assertIn('login_bg_3.jpg', branding['login_bg_image_1'])
        self.assertIn('login_bg_3.jpg', branding['login_bg_image_2'])
        self.assertIn('login_bg_3.jpg', branding['login_bg_image_3'])

    def test_dynamic_vat_rate_configuration_and_document_reflection(self):
        """
        Verify that changing VAT rate in CompanySettings dynamically updates
        newly generated Quotations, Invoices, Line Items, and PDF documents (e.g. 10%, 0% VAT exempt).
        """
        self.client.force_login(self.admin_user)

        # 1. Update company settings to 10% VAT
        post_data = {
            'company_name': 'MENARD TRADING CC',
            'tagline': 'ALWAYS ON TIME',
            'postal_address': 'P O BOX 497-19001,',
            'city': 'RUNDU',
            'country': 'NAMIBIA',
            'email': 'info@menardtrading.com',
            'orders_email': 'orders@menardtrading.com',
            'quotes_email': 'quotes@menardtrading.com',
            'accounts_email': 'accounts@menardtrading.com',
            'vat_rate': '10.00',
        }
        resp = self.client.post(reverse('administration-company-settings'), post_data, follow=True)
        self.assertEqual(resp.status_code, 200)

        settings_obj = CompanySettings.get_settings()
        self.assertEqual(settings_obj.vat_rate, Decimal('10.00'))
        self.assertEqual(settings_obj.as_branding_dict()['vat_rate'], Decimal('10.00'))

        # 2. Create Quotation and check 10% VAT calculation
        quote = Quotation.objects.create(
            customer=self.customer,
            valid_until=timezone.now().date() + datetime.timedelta(days=14),
        )
        self.assertEqual(quote.vat_rate, Decimal('10.00'))

        QuoteLineItem.objects.create(
            quote=quote,
            description='10-Ton Cargo Rundu to Windhoek',
            quantity=Decimal('1.00'),
            unit_price=Decimal('20000.00')
        )
        quote.refresh_from_db()
        self.assertEqual(quote.subtotal, Decimal('20000.00'))
        self.assertEqual(quote.vat_rate, Decimal('10.00'))
        self.assertEqual(quote.vat_amount, Decimal('2000.00'))
        self.assertEqual(quote.total_amount, Decimal('22000.00'))

        # 3. Create standalone Invoice and verify 10% calculation
        inv = Invoice.objects.create(
            customer=self.customer,
            due_date=timezone.now().date() + datetime.timedelta(days=30),
        )
        self.assertEqual(inv.vat_rate, Decimal('10.00'))

        InvoiceLineItem.objects.create(
            invoice=inv,
            description='Freight Haulage Service',
            quantity=Decimal('1.00'),
            unit_price=Decimal('50000.00')
        )
        inv.refresh_from_db()
        self.assertEqual(inv.subtotal, Decimal('50000.00'))
        self.assertEqual(inv.vat_rate, Decimal('10.00'))
        self.assertEqual(inv.vat_amount, Decimal('5000.00'))
        self.assertEqual(inv.total_amount, Decimal('55000.00'))
        self.assertEqual(inv.balance_due, Decimal('55000.00'))

        # 4. Generate PDFs and verify dynamic VAT percentage is rendered
        quote_pdf = generate_quotation_pdf(quote)
        self.assertIsNotNone(quote_pdf)

        inv_pdf = generate_invoice_pdf(inv)
        self.assertIsNotNone(inv_pdf)

        # 5. Test 0% VAT (VAT-Exempt) setting
        post_data['vat_rate'] = '0.00'
        resp = self.client.post(reverse('administration-company-settings'), post_data, follow=True)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'value="0.00"')

        quote_zero = Quotation.objects.create(
            customer=self.customer,
            valid_until=timezone.now().date() + datetime.timedelta(days=7),
        )
        QuoteLineItem.objects.create(
            quote=quote_zero,
            description='VAT Exempt Cross-Border Transit',
            quantity=Decimal('1.00'),
            unit_price=Decimal('10000.00')
        )
        quote_zero.refresh_from_db()
        self.assertEqual(quote_zero.vat_rate, Decimal('0.00'))
        self.assertEqual(quote_zero.subtotal, Decimal('10000.00'))
        self.assertEqual(quote_zero.vat_amount, Decimal('0.00'))
        self.assertEqual(quote_zero.total_amount, Decimal('10000.00'))

