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
        self.assertEqual(po.email_send_status, 'SENT')
        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(OrderCommunication.objects.filter(purchase_order=po).count(), 1)

        self.client.post(reverse('send_outgoing_purchase_order', args=[po.pk]))
        self.assertEqual(send_email.call_count, 1)
        self.assertEqual(OrderCommunication.objects.filter(purchase_order=po).count(), 1)
