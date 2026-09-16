import json
from unittest.mock import patch
from django.test import TestCase, Client
from django.urls import reverse
from apps.orders.models import InboundEmailMessage, EmailQueueMessage, PurchaseOrder


class AutonomousInboundWebhookTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.webhook_url = reverse('brevo_inbound_webhook')

    @patch('apps.orders.email_worker.send_departmental_email')
    def test_fast_non_blocking_inbound_acceptance(self, mock_send):
        mock_send.return_value = (True, "Delivered")
        payload = {
            'items': [{
                'Sender': {'Email': 'procurement@namibiacargo.com', 'Name': 'John Doe'},
                'ReplyTo': {'Email': 'john.doe@namibiacargo.com'},
                'Subject': 'Purchase Order #PO-9921 - Mining Supplies',
                'RawTextBody': 'Please find attached our purchase order for transport from Windhoek to Swakopmund.',
                'MessageId': '<msg-auto-9921@namibiacargo.com>',
            }]
        }

        response = self.client.post(
            self.webhook_url,
            data=json.dumps(payload),
            content_type='application/json'
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'accepted')

        # Verify InboundEmailMessage persisted
        inbound = InboundEmailMessage.objects.filter(message_id='<msg-auto-9921@namibiacargo.com>').first()
        self.assertIsNotNone(inbound)
        self.assertEqual(inbound.sender_email, 'procurement@namibiacargo.com')
        self.assertEqual(inbound.reply_to, 'john.doe@namibiacargo.com')

        # Verify skeleton PO created
        self.assertIsNotNone(inbound.purchase_order)

        # Verify optimistic auto-acknowledgement queued
        queued_ack = EmailQueueMessage.objects.filter(
            inbound_email=inbound,
            department='no-reply'
        ).first()
        self.assertIsNotNone(queued_ack)
        self.assertEqual(queued_ack.recipient_list, ['john.doe@namibiacargo.com'])
        self.assertIn('PO-', queued_ack.subject)

    @patch('apps.orders.email_worker.send_departmental_email')
    def test_strict_idempotency_prevents_duplicate_orders(self, mock_send):
        mock_send.return_value = (True, "Delivered")
        payload = {
            'Sender': {'Email': 'alice@freight.com', 'Name': 'Alice'},
            'Subject': 'PO #PO-IDEM-001',
            'Message': 'Transport needed.',
            'MessageId': '<unique-idempotent-key-001@freight.com>',
        }

        # 1st Post
        res1 = self.client.post(self.webhook_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(res1.status_code, 200)

        inbound_count_1 = InboundEmailMessage.objects.count()
        po_count_1 = PurchaseOrder.objects.count()
        queue_count_1 = EmailQueueMessage.objects.count()

        self.assertEqual(inbound_count_1, 1)
        self.assertEqual(po_count_1, 1)
        self.assertEqual(queue_count_1, 1)

        # 2nd Post with identical MessageId (e.g. Webhook Re-delivery)
        res2 = self.client.post(self.webhook_url, data=json.dumps(payload), content_type='application/json')
        self.assertEqual(res2.status_code, 200)

        # Ensure NO duplicate records were created
        self.assertEqual(InboundEmailMessage.objects.count(), 1)
        self.assertEqual(PurchaseOrder.objects.count(), 1)
        self.assertEqual(EmailQueueMessage.objects.count(), 1)
