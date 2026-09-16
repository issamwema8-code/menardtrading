import email
import imaplib
import logging
from django.core.management.base import BaseCommand
from django.conf import settings
from apps.orders.models import InboundEmailMessage
from menard_core.brevo_email import clean_email

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Seeds all currently existing emails in the IMAP inbox as PROCESSED in the database without creating orders or sending auto-replies."

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Inspect inbox without writing records to database.'
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)

        host = getattr(settings, 'IMAP_HOST', 'mail.menardtrading.com')
        port = getattr(settings, 'IMAP_PORT', 993)
        user = getattr(settings, 'IMAP_USER', 'orders@menardtrading.com')
        password = getattr(settings, 'EMAIL_PASSWORD', '')

        self.stdout.write(self.style.SUCCESS("=" * 80))
        self.stdout.write(self.style.SUCCESS(" MENARD TRADING CC — SEED INBOX (MARK HISTORICAL EMAILS AS PROCESSED)"))
        self.stdout.write(self.style.SUCCESS("=" * 80))

        if not password:
            self.stdout.write(self.style.ERROR("EMAIL_PASSWORD is not configured in settings/environment."))
            return

        try:
            mail = imaplib.IMAP4_SSL(host, port=port, timeout=15)
            mail.login(user, password)
            mail.select('INBOX')

            status, messages = mail.search(None, 'ALL')
            if status != 'OK' or not messages or not messages[0]:
                self.stdout.write(self.style.NOTICE("Inbox is empty. No emails to seed."))
                mail.logout()
                return

            msg_ids = messages[0].split()
            self.stdout.write(f"Found {len(msg_ids)} total email(s) in inbox. Processing Message-IDs...")

            seeded_count = 0
            already_known_count = 0

            for msg_id in msg_ids:
                res, data = mail.fetch(msg_id, '(BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT DATE)])')
                if res != 'OK':
                    continue

                for response_part in data:
                    if not isinstance(response_part, tuple):
                        continue

                    header_data = response_part[1]
                    msg = email.message_from_bytes(header_data)

                    subject = msg.get('Subject', '')
                    sender_raw = msg.get('From', '')
                    sender_name, sender_email = email.utils.parseaddr(sender_raw)
                    sender_email = clean_email(sender_email or sender_raw)
                    rfc_msg_id = (msg.get('Message-ID') or msg.get('Message-Id') or f"IMAP-{msg_id.decode()}-{sender_email}").strip()

                    exists = InboundEmailMessage.objects.filter(message_id=rfc_msg_id).exists()
                    if exists:
                        already_known_count += 1
                    else:
                        seeded_count += 1
                        if not dry_run:
                            InboundEmailMessage.objects.create(
                                message_id=rfc_msg_id,
                                sender_email=sender_email,
                                sender_name=sender_name,
                                recipient_email=user,
                                subject=subject,
                                status=InboundEmailMessage.Status.PROCESSED
                            )

            mail.logout()

            self.stdout.write(self.style.SUCCESS(
                f"\nDONE: {seeded_count} historical email(s) seeded as PROCESSED. {already_known_count} were already registered."
            ))
            self.stdout.write(self.style.SUCCESS("Future mailbox syncs will only ingest NEW incoming emails!"))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"IMAP Error: {e}"))
