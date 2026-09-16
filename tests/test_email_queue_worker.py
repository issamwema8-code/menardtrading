from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from apps.orders.models import EmailQueueMessage, PurchaseOrder, InboundEmailMessage
from apps.accounts.models import AdminNotification
from apps.orders.email_worker import process_outbound_queue, process_inbound_queue, run_worker_cycle


class EmailQueueWorkerTest(TestCase):
    def setUp(self):
        self.po = PurchaseOrder.objects.create(
            po_number="PO-TEST-WORKER-001",
            raw_email_sender="customer@miningcorp.na",
            raw_email_subject="Supply PO 001",
            status=PurchaseOrder.Status.RECEIVED
        )
        self.queue_item = EmailQueueMessage.objects.create(
            department="no-reply",
            recipient_list=["customer@miningcorp.na"],
            subject="Purchase Order Received – #PO-TEST-WORKER-001",
            template_name="emails/po_received_noreply.html",
            context_data={'customer_name': 'Mining Corp'},
            purchase_order=self.po,
            status=EmailQueueMessage.Status.QUEUED
        )

    @patch('apps.orders.email_worker.send_departmental_email')
    def test_outbound_queue_successful_dispatch(self, mock_send):
        mock_send.return_value = (True, "Email dispatched successfully")

        results = process_outbound_queue(batch_size=10)
        self.assertEqual(results['sent'], 1)
        self.assertEqual(results['failed'], 0)

        self.queue_item.refresh_from_db()
        self.assertEqual(self.queue_item.status, EmailQueueMessage.Status.SENT)
        self.assertIsNotNone(self.queue_item.sent_at)
        self.assertEqual(self.queue_item.attempts, 1)

        # PO acknowledgment marked
        self.po.refresh_from_db()
        self.assertTrue(self.po.acknowledgment_sent)
        self.assertIsNotNone(self.po.acknowledgment_sent_at)

    @patch('apps.orders.email_worker.send_departmental_email')
    def test_exponential_backoff_and_permanent_failure(self, mock_send):
        mock_send.return_value = (False, "550 5.1.1 SMTP relay rate limit exceeded")

        # Attempt 1: Should schedule retry
        res1 = process_outbound_queue(batch_size=10)
        self.assertEqual(res1['retrying'], 1)

        self.queue_item.refresh_from_db()
        self.assertEqual(self.queue_item.attempts, 1)
        self.assertEqual(self.queue_item.status, EmailQueueMessage.Status.QUEUED)
        self.assertGreater(self.queue_item.next_attempt_at, timezone.now())
        self.assertIn("rate limit", self.queue_item.last_error)

        # Fast forward time for Attempt 2, 3, 4
        self.queue_item.next_attempt_at = timezone.now() - timezone.timedelta(seconds=1)
        self.queue_item.attempts = 3
        self.queue_item.save()

        # Attempt 4 (Max 4 attempts): Should transition to FAILED
        res4 = process_outbound_queue(batch_size=10)
        self.assertEqual(res4['failed'], 1)

        self.queue_item.refresh_from_db()
        self.assertEqual(self.queue_item.status, EmailQueueMessage.Status.FAILED)
        self.assertEqual(self.queue_item.attempts, 4)

        # Verify AdminNotification created for failed email
        failed_notif = AdminNotification.objects.filter(
            notification_type=AdminNotification.NotificationType.EMAIL_FAILED
        ).first()
        self.assertIsNotNone(failed_notif)
        self.assertIn("failed after 4 attempts", failed_notif.message)
        
        # Verify PO was NOT deleted or corrupted
        self.po.refresh_from_db()
        self.assertEqual(self.po.po_number, "PO-TEST-WORKER-001")
