import json
from unittest.mock import patch
from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
from apps.orders.models import PurchaseOrder, InboundEmailMessage, EmailQueueMessage
from apps.accounts.models import AdminNotification, UserProfile
from apps.orders.email_worker import run_worker_cycle


class AdminOfflineAutonomousFlowTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_superuser(
            username='offline_test_admin',
            email='offline_admin@menardtrading.com',
            password='SecretAdminPassword2026!'
        )
        UserProfile.objects.get_or_create(user=self.admin)
        # Explicitly log out to ensure 0 active sessions
        self.client.logout()

    @patch('menard_core.brevo_email.send_departmental_email')
    def test_complete_autonomous_processing_when_admin_offline(self, mock_send):
        mock_send.return_value = (True, "Email dispatched successfully")

        # 1. Simulate incoming PO email from Brevo Webhook while Admin is OFFLINE
        webhook_url = reverse('brevo_inbound_webhook')
        payload = {
            'items': [{
                'Sender': {'Email': 'logistics@walvisbaymining.com', 'Name': 'Walvis Bay Mining CC'},
                'ReplyTo': {'Email': 'orders@walvisbaymining.com'},
                'Subject': 'Official PO #PO-WBM-8841 - 34T Freight Windhoek to Walvis Bay',
                'RawTextBody': 'Please provide 34-ton freight transport for 34 tons of heavy mining gear.',
                'MessageId': '<inbound-msg-wbm-8841@walvisbaymining.com>',
            }]
        }

        # Post webhook (0 logged-in users)
        response = self.client.post(
            webhook_url,
            data=json.dumps(payload),
            content_type='application/json'
        )
        self.assertEqual(response.status_code, 200)

        # 2. Run background worker cycle (autonomous worker / cron)
        cycle_result = run_worker_cycle()

        # 3. Verify backend state after background execution
        po = PurchaseOrder.objects.filter(raw_email_sender='logistics@walvisbaymining.com').first()
        self.assertIsNotNone(po, "Purchase Order must be automatically created in the backend")
        self.assertEqual(po.status, PurchaseOrder.Status.QUOTED)
        self.assertIsNotNone(po.quotation, "Draft Quotation must be generated automatically")

        # Verify auto-reply email was dispatched to customer
        self.assertTrue(po.acknowledgment_sent)
        self.assertIsNotNone(po.acknowledgment_sent_at)

        # Verify AdminNotification was created
        notif = AdminNotification.objects.filter(
            notification_type=AdminNotification.NotificationType.NEW_ORDER,
            is_read=False
        ).first()
        self.assertIsNotNone(notif, "Admin Notification must be created for offline staff")
        self.assertIn("PO-", notif.title)

        # 4. Now Admin logs in later to view the dashboard
        self.client.login(username='offline_test_admin', password='SecretAdminPassword2026!')
        overview_res = self.client.get(reverse('dashboard_overview'))
        self.assertEqual(overview_res.status_code, 200)

        # Confirm notification and PO are already visible in dashboard context
        self.assertIn(po, overview_res.context['orders'])
        self.assertGreaterEqual(overview_res.context['header_counts']['unread_notifications_count'], 1)
