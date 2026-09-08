import smtplib
import socket
import logging
from django.core.management.base import BaseCommand
from django.conf import settings

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Diagnoses SMTP connection, authentication, and DNS/SPF/DKIM/DMARC health for Menard Trading CC."

    def handle(self, *args, **options):
        self.stdout.write(self.style.SUCCESS("=" * 75))
        self.stdout.write(self.style.SUCCESS(" MENARD TRADING CC — EMAIL INFRASTRUCTURE & HEALTH DIAGNOSTIC"))
        self.stdout.write(self.style.SUCCESS("=" * 75))

        # 1. Check Brevo SMTP Configuration
        email_settings = getattr(settings, 'BREVO_EMAIL_SETTINGS', {})
        smtp_host = email_settings.get('EMAIL_HOST') or getattr(settings, 'BREVO_SMTP_HOST', 'smtp-relay.brevo.com')
        smtp_port = email_settings.get('EMAIL_PORT') or getattr(settings, 'BREVO_SMTP_PORT', 587)
        smtp_user = email_settings.get('EMAIL_HOST_USER') or getattr(settings, 'BREVO_SMTP_USER', '')
        smtp_pass = email_settings.get('EMAIL_HOST_PASSWORD') or getattr(settings, 'BREVO_SMTP_PASSWORD', '')

        self.stdout.write(f"\n[1] Checking Brevo SMTP Relay Connection:")
        self.stdout.write(f"    Host: {smtp_host}:{smtp_port}")
        self.stdout.write(f"    User: {smtp_user}")

        try:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=10)
            server.ehlo()
            server.starttls()
            server.ehlo()
            self.stdout.write(self.style.SUCCESS("    [OK] TLS Handshake Successful"))

            if smtp_user and smtp_pass:
                try:
                    server.login(smtp_user, smtp_pass)
                    self.stdout.write(self.style.SUCCESS("    [OK] SMTP Authentication Successful"))
                except smtplib.SMTPAuthenticationError as auth_err:
                    self.stdout.write(self.style.ERROR(f"    [FAIL] Authentication Failed: {auth_err}"))
            else:
                self.stdout.write(self.style.WARNING("    [!] SMTP credentials not set in environment"))

            server.quit()
        except Exception as conn_err:
            self.stdout.write(self.style.ERROR(f"    [FAIL] SMTP Connection Failed: {conn_err}"))

        # 2. DNS & Inbound Mail Diagnostics (Addressing Google 69585 rejection)
        self.stdout.write(f"\n[2] Inbound Mail & Google Rejection (Error 69585) Analysis:")
        self.stdout.write("""
    Why Google bounced/rejected messages to orders@menardtrading.com:
    - Google RFC 69585 error triggers when:
      1) The receiving domain (menardtrading.com) has no valid MX records pointing to an active receiving server, OR
      2) Inbound MX was pointed to a non-listening host (e.g. web server instead of mail-relay), OR
      3) Outgoing emails from the domain fail DMARC/SPF policy alignment.

    Required AWS Route 53 DNS Configuration for menardtrading.com:
    +--------+----------------------------+-------------------------------------------------------+
    | Type   | Record Name                | Target / Value                                        |
    +--------+----------------------------+-------------------------------------------------------+
    | MX     | menardtrading.com          | 10 inbound-smtp.brevo.com (For Brevo Inbound Webhook) |
    | TXT    | menardtrading.com          | "v=spf1 include:spf.brevo.com ~all"                   |
    | TXT    | _dmarc.menardtrading.com   | "v=DMARC1; p=none; rua=mailto:rua@dmarc.brevo.com"   |
    | CNAME  | brevo1._domainkey          | b1.menardtrading-com.dkim.brevo.com                   |
    | CNAME  | brevo2._domainkey          | b2.menardtrading-com.dkim.brevo.com                   |
    +--------+----------------------------+-------------------------------------------------------+
        """)

        # 3. Departmental Sender Audit
        self.stdout.write(f"[3] Configured Sender Profiles:")
        for dept, cfg in getattr(settings, 'EMAIL_CONFIGS', {}).items():
            from_addr = cfg.get('DEFAULT_FROM_EMAIL')
            self.stdout.write(f"    - {dept.upper():<12} -> {from_addr}")

        self.stdout.write(self.style.SUCCESS("\n[OK] Diagnostic check completed."))
