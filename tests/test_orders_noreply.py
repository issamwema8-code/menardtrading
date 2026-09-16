import os
from unittest.mock import patch
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from apps.customers.models import Customer
from apps.orders.models import PurchaseOrder, OrderCommunication, EmailQueueMessage
from apps.orders.services import process_inbound_purchase_order
from apps.orders.email_worker import process_outbound_queue
from apps.quotes.models import Quotation


class PurchaseOrderNoReplyEmailTest(TestCase):
    def setUp(self):
        self.customer = Customer.objects.create(
            company_name="Acme Logistics Corp",
            contact_name="Alice Smith",
            email="alice@acmelogistics.com",
            phone="+264 81 123 4567"
        )
        self.po = PurchaseOrder.objects.create(
            po_number="PO-TEST-NOREPLY-001",
            customer=self.customer,
            raw_email_sender="alice@acmelogistics.com",
            raw_email_subject="New Freight PO 001",
            weight_tons=Decimal('28.00'),
            pickup_location="Windhoek",
            delivery_location="Walvis Bay",
            status=PurchaseOrder.Status.RECEIVED
        )

    @patch('apps.orders.email_worker.send_departmental_email')
    def test_single_no_reply_email_queued_and_dispatched(self, mock_send_email):
        mock_send_email.return_value = (True, "Dispatched successfully")

        # Initial PO processing
        self.assertFalse(self.po.acknowledgment_sent)
        self.assertIsNone(self.po.acknowledgment_sent_at)

        quote = process_inbound_purchase_order(self.po)

        # Verify queued in EmailQueueMessage
        queued = EmailQueueMessage.objects.filter(purchase_order=self.po, department='no-reply').first()
        self.assertIsNotNone(queued)
        self.assertEqual(queued.recipient_list, ['alice@acmelogistics.com'])

        # Process queue
        process_outbound_queue(batch_size=10)

        # Refresh from DB
        self.po.refresh_from_db()
        self.assertTrue(self.po.acknowledgment_sent)
        self.assertIsNotNone(self.po.acknowledgment_sent_at)
        self.assertEqual(mock_send_email.call_count, 1)

        # Verify call args
        args, kwargs = mock_send_email.call_args
        self.assertEqual(kwargs['department'], 'no-reply')
        self.assertEqual(kwargs['recipient_list'], ['alice@acmelogistics.com'])
        self.assertIn('PO-TEST-NOREPLY-001', kwargs['subject'])
        self.assertEqual(kwargs['template_name'], 'emails/po_received_noreply.html')

        # Verify communication log entry
        comm = OrderCommunication.objects.filter(purchase_order=self.po, sender_department='no-reply').first()
        self.assertIsNotNone(comm)
        self.assertEqual(comm.recipient_email, 'alice@acmelogistics.com')

    @patch('apps.orders.email_worker.send_departmental_email')
    def test_reprocessing_does_not_resend_no_reply_email(self, mock_send_email):
        mock_send_email.return_value = (True, "Dispatched successfully")

        # First run
        process_inbound_purchase_order(self.po)
        process_outbound_queue(batch_size=10)
        self.assertEqual(mock_send_email.call_count, 1)
        self.assertEqual(OrderCommunication.objects.filter(purchase_order=self.po).count(), 1)

        # Second run (e.g. operator triggers re-parse or webhook retry)
        self.po.refresh_from_db()
        quote2 = process_inbound_purchase_order(self.po)
        process_outbound_queue(batch_size=10)

        # Mock count should STILL be exactly 1
        self.assertEqual(mock_send_email.call_count, 1)
        self.assertEqual(OrderCommunication.objects.filter(purchase_order=self.po).count(), 1)

        # Third run
        self.po.refresh_from_db()
        quote3 = process_inbound_purchase_order(self.po)
        process_outbound_queue(batch_size=10)
        self.assertEqual(mock_send_email.call_count, 1)

    @patch('apps.orders.email_worker.send_departmental_email')
    def test_po_without_customer_email_handles_cleanly(self, mock_send_email):
        mock_send_email.return_value = (True, "Dispatched successfully")
        po_no_email = PurchaseOrder.objects.create(
            po_number="PO-NO-EMAIL-001",
            raw_email_sender="",
            status=PurchaseOrder.Status.RECEIVED
        )
        quote = process_inbound_purchase_order(po_no_email)
        self.assertIsNotNone(quote)
        process_outbound_queue(batch_size=10)
        po_no_email.refresh_from_db()
        self.assertTrue(po_no_email.acknowledgment_sent)
        self.assertEqual(mock_send_email.call_count, 1)

        # Reprocessing still sends 0 additional emails
        process_inbound_purchase_order(po_no_email)
        process_outbound_queue(batch_size=10)
        self.assertEqual(mock_send_email.call_count, 1)
