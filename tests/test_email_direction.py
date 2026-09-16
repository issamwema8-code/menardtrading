from unittest.mock import patch
from django.test import TestCase
from menard_core.brevo_email import clean_email, is_loopback_or_invalid_recipient, send_departmental_email, queue_departmental_email
from apps.orders.models import EmailQueueMessage


class EmailDirectionAndLoopbackSafetyTest(TestCase):
    def test_clean_email_normalization(self):
        self.assertEqual(clean_email("John Doe <John.Doe@Example.com>"), "john.doe@example.com")
        self.assertEqual(clean_email("  SUPPORT@MENARDTRADING.COM  "), "support@menardtrading.com")
        self.assertEqual(clean_email("invalid-address"), "invalid-address")
        self.assertEqual(clean_email(""), "")

    def test_loopback_detection(self):
        # Disallowed addresses
        self.assertTrue(is_loopback_or_invalid_recipient("orders@menardtrading.com"))
        self.assertTrue(is_loopback_or_invalid_recipient("no-reply@menardtrading.com"))
        self.assertTrue(is_loopback_or_invalid_recipient("mailer-daemon@googlemail.com"))
        self.assertTrue(is_loopback_or_invalid_recipient("postmaster@company.com"))
        self.assertTrue(is_loopback_or_invalid_recipient("bounce-handler@service.com"))
        self.assertTrue(is_loopback_or_invalid_recipient(""))

        # Allowed client addresses
        self.assertFalse(is_loopback_or_invalid_recipient("procurement@clientcorp.na"))
        self.assertFalse(is_loopback_or_invalid_recipient("alice.smith@logistics.co.za"))

    def test_queue_suppresses_loopback_auto_replies(self):
        # Attempt to queue auto-reply back to orders@menardtrading.com
        msg = queue_departmental_email(
            department='no-reply',
            recipient_list=['orders@menardtrading.com'],
            subject='Loop test',
            template_name='emails/po_received_noreply.html',
            context={}
        )
        self.assertIsNone(msg)
        self.assertEqual(EmailQueueMessage.objects.count(), 0)

    @patch('menard_core.brevo_email.EmailMultiAlternatives')
    @patch('menard_core.brevo_email.get_connection')
    def test_safe_auto_reply_to_client_only(self, mock_conn, mock_email_class):
        mock_instance = mock_email_class.return_value

        success, msg = send_departmental_email(
            department='no-reply',
            recipient_list=['customer@miningcorp.na'],
            subject='Purchase Order Received',
            template_name='emails/po_received_noreply.html',
            context={'customer_name': 'Mining Corp', 'po': None},
            reply_to=['orders@menardtrading.com']
        )

        self.assertTrue(success)
        # Verify email constructor kwargs
        args, kwargs = mock_email_class.call_args
        self.assertEqual(kwargs['to'], ['customer@miningcorp.na'])
        self.assertEqual(kwargs['reply_to'], ['orders@menardtrading.com'])
