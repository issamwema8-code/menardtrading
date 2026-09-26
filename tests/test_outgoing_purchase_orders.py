from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from apps.customers.models import Customer
from apps.orders.models import PurchaseOrder, OrderCommunication


class OutgoingPurchaseOrderWorkflowTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser(
            username='po-manager',
            email='manager@example.com',
            password='test-password',
        )
        self.client.force_login(self.user)
        self.provider = Customer.objects.create(
            company_name='Provider Logistics',
            contact_name='Piet Provider',
            email='provider@example.com',
            phone='+264 81 000 0000',
            physical_address='Windhoek, Namibia',
        )

    def test_create_outgoing_po_calculates_total_and_links_provider(self):
        response = self.client.post(reverse('create_outgoing_purchase_order'), {
            'supplier_id': self.provider.pk,
            'recipient_email': 'provider@example.com',
            'issue_date': '2026-09-16',
            'load_reference': 'LOAD-100',
            'currency': 'NAD',
            'quantity': '2',
            'unit_price': '1250.50',
            'cargo_description': 'Container transport',
            'pickup_location': 'Walvis Bay',
            'delivery_location': 'Windhoek',
            'payment_terms': '30 Days Net',
            'special_instructions': 'Send signed POD.',
        })
        self.assertEqual(response.status_code, 302)
        po = PurchaseOrder.objects.get(direction=PurchaseOrder.Direction.OUTGOING)
        self.assertEqual(po.supplier, self.provider)
        self.assertEqual(po.status, PurchaseOrder.Status.DRAFT)
        self.assertEqual(po.total_amount, Decimal('2501.00'))
        self.assertEqual(po.load_reference, 'LOAD-100')
        self.assertEqual(po.created_by, self.user)

    @patch('menard_core.brevo_email.send_departmental_email', return_value=(True, 'sent'))
    def test_send_outgoing_po_is_idempotent_and_audited(self, send_email):
        po = PurchaseOrder.objects.create(
            po_number='PO-OUT-2026-0001',
            direction=PurchaseOrder.Direction.OUTGOING,
            status=PurchaseOrder.Status.DRAFT,
            supplier=self.provider,
            recipient_email='provider@example.com',
            cargo_description='Container transport',
            quantity=Decimal('1'),
            unit_price=Decimal('900'),
            pickup_location='Walvis Bay',
            delivery_location='Windhoek',
        )
        response = self.client.post(reverse('send_outgoing_purchase_order', args=[po.pk]))
        self.assertEqual(response.status_code, 302)
        po.refresh_from_db()
        self.assertEqual(po.status, PurchaseOrder.Status.SENT)
        self.assertIsNotNone(po.sent_at)
        self.client.post(reverse('send_outgoing_purchase_order', args=[po.pk]))
        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(OrderCommunication.objects.filter(purchase_order=po).count(), 1)

    def test_create_outgoing_po_generates_pdf_and_preview(self):
        """Verify creating a PO generates the PDF immediately and preview displays company details."""
        from apps.accounts.models import CompanySettings
        cs = CompanySettings.get_settings()
        cs.company_name = 'MENARD TRADING CC'
        cs.postal_address = 'P O BOX 497-19001'
        cs.city = 'RUNDU'
        cs.country = 'NAMIBIA'
        cs.company_reg_number = 'CC/2022/03892'
        cs.vat_number = '13009715-11'
        cs.orders_email = 'orders@menardtrading.com'
        cs.phone = '+264 81 445 5188'
        cs.save()

        response = self.client.post(reverse('create_outgoing_purchase_order'), {
            'supplier_id': self.provider.pk,
            'recipient_email': 'provider@example.com',
            'issue_date': '2026-09-26',
            'load_reference': 'LOAD-2026-099',
            'currency': 'NAD',
            'quantity': '3',
            'unit_price': '4500.00',
            'cargo_description': 'Heavy Machinery Transit Walvis Bay to Rundu',
            'pickup_location': 'Walvis Bay Port',
            'delivery_location': 'Rundu Depot',
            'payment_terms': '14 Days Net',
            'special_instructions': 'Ensure heavy transport permit and escort.',
        })
        self.assertEqual(response.status_code, 302)
        po = PurchaseOrder.objects.get(load_reference='LOAD-2026-099')
        self.assertTrue(bool(po.po_file))

        # Check Preview
        preview_url = reverse('purchase_order_preview', args=[po.pk])
        preview_resp = self.client.get(preview_url)
        self.assertEqual(preview_resp.status_code, 200)
        self.assertContains(preview_resp, 'MENARD TRADING CC')
        self.assertContains(preview_resp, 'P O BOX 497-19001, RUNDU - NAMIBIA')
        self.assertContains(preview_resp, 'CC/2022/03892')
        self.assertContains(preview_resp, '13009715-11')
        self.assertContains(preview_resp, 'orders@menardtrading.com')
        self.assertContains(preview_resp, '+264 81 445 5188')
        self.assertContains(preview_resp, 'PURCHASE ORDER')
        self.assertContains(preview_resp, 'Provider Logistics')
        self.assertContains(preview_resp, '13,500.00')

        # Check PDF download
        pdf_url = reverse('purchase_order_pdf_download', args=[po.pk])
        pdf_resp = self.client.get(pdf_url)
        self.assertEqual(pdf_resp.status_code, 200)
        self.assertEqual(pdf_resp['Content-Type'], 'application/pdf')
        self.assertTrue(pdf_resp.content.startswith(b'%PDF-'))

