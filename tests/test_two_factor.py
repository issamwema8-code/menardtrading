import re
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from django.core import mail

from apps.accounts.models import UserProfile, AuditLog, WEAK_2FA_PINS


class TwoFactorAuthenticationTestCase(TestCase):
    def setUp(self):
        self.password = 'MenardSecure2026!'
        self.user = User.objects.create_user(
            username='johndoe',
            email='john.doe@menardtrading.com',
            password=self.password,
            first_name='John',
            last_name='Doe'
        )
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user)
        self.client = Client()

    # ============================================================
    # 1. EMAIL OTP TESTS
    # ============================================================

    def test_email_otp_generation_and_format(self):
        """OTP must be a 6-digit numeric string with 10-minute expiry."""
        otp = self.profile.generate_email_otp()
        self.assertEqual(len(otp), 6)
        self.assertTrue(otp.isdigit())
        self.assertIsNotNone(self.profile.email_otp_hash)
        self.assertNotEqual(self.profile.email_otp_hash, otp)  # Must be securely hashed
        self.assertIsNotNone(self.profile.email_otp_expires_at)
        self.assertTrue(self.profile.email_otp_expires_at > timezone.now())

    def test_email_otp_correct_verification(self):
        """Correct OTP verifies successfully and invalidates code immediately."""
        otp = self.profile.generate_email_otp()
        ok, msg = self.profile.verify_email_otp(otp)
        self.assertTrue(ok)
        self.assertIn("accepted", msg.lower())

        # Single-use check: Trying the same OTP again must fail
        self.profile.refresh_from_db()
        self.assertIsNone(self.profile.email_otp_hash)
        ok_again, msg_again = self.profile.verify_email_otp(otp)
        self.assertFalse(ok_again)

    def test_email_otp_incorrect_verification(self):
        """Incorrect OTP fails and increments attempt counter."""
        self.profile.generate_email_otp()
        ok, msg = self.profile.verify_email_otp('000000')
        self.assertFalse(ok)
        self.assertIn("incorrect", msg.lower())

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.email_otp_failed_attempts, 1)

    def test_email_otp_expired_verification(self):
        """Expired OTP is rejected."""
        otp = self.profile.generate_email_otp()
        # Simulate time travel beyond 10 minutes
        self.profile.email_otp_expires_at = timezone.now() - timezone.timedelta(minutes=1)
        self.profile.save()

        ok, msg = self.profile.verify_email_otp(otp)
        self.assertFalse(ok)
        self.assertIn("expired", msg.lower())

    def test_email_otp_attempt_limits(self):
        """After 5 failed attempts, OTP verification is rejected."""
        otp = self.profile.generate_email_otp()
        for _ in range(5):
            self.profile.verify_email_otp('999999')

        self.profile.refresh_from_db()
        self.assertEqual(self.profile.email_otp_failed_attempts, 5)

        # Even with correct OTP, it is now blocked
        ok, msg = self.profile.verify_email_otp(otp)
        self.assertFalse(ok)
        self.assertIn("too many", msg.lower())

    def test_email_otp_resend_cooldown(self):
        """Cooldown timer is 60 seconds (1 minute) after generating an OTP."""
        self.profile.generate_email_otp()
        self.assertTrue(self.profile.is_otp_cooldown_active)
        self.assertTrue(30 < self.profile.get_otp_cooldown_remaining_seconds() <= 60)

    # ============================================================
    # 2. 4-DIGIT 2FA PIN TESTS
    # ============================================================

    def test_set_valid_2fa_pin(self):
        """Valid 4-digit PIN is hashed and saved."""
        ok, msg = self.profile.set_pin('8392')
        self.assertTrue(ok)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_pin_configured)
        self.assertNotEqual(self.profile.two_factor_pin_hash, '8392')  # Hashed

    def test_reject_invalid_length_or_non_digit_pin(self):
        """PIN must be exactly 4 numeric digits."""
        ok, msg = self.profile.set_pin('123')
        self.assertFalse(ok)

        ok, msg = self.profile.set_pin('12345')
        self.assertFalse(ok)

        ok, msg = self.profile.set_pin('abcd')
        self.assertFalse(ok)

    def test_reject_weak_common_pins(self):
        """Common/trivial PINs like 1234, 0000, 1111 are rejected."""
        for weak in ['0000', '1111', '1234', '4321']:
            ok, msg = self.profile.set_pin(weak)
            self.assertFalse(ok, f"Weak PIN '{weak}' should have been rejected")
            self.assertIn("common", msg.lower())

    def test_check_correct_pin(self):
        """Correct PIN passes verification."""
        self.profile.set_pin('5729')
        ok, msg = self.profile.check_pin('5729')
        self.assertTrue(ok)

    def test_check_incorrect_pin_and_lockout(self):
        """Incorrect PIN fails and locks out after 5 consecutive failures."""
        self.profile.set_pin('5729')
        for i in range(4):
            ok, msg = self.profile.check_pin('0001')
            self.assertFalse(ok)
            self.assertFalse(self.profile.is_pin_locked)

        # 5th failure triggers lockout
        ok, msg = self.profile.check_pin('0001')
        self.assertFalse(ok)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_pin_locked)

        # 6th attempt is blocked even if correct PIN is provided
        ok, msg = self.profile.check_pin('5729')
        self.assertFalse(ok)
        self.assertIn("locked", msg.lower())

    def test_clear_pin(self):
        """Clearing PIN removes hash and unlocks account."""
        self.profile.set_pin('5729')
        self.profile.clear_pin()
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.is_pin_configured)
        self.assertIsNone(self.profile.two_factor_pin_hash)

    # ============================================================
    # 3. END-TO-END LOGIN & TWO-FACTOR AUTHENTICATION WORKFLOW
    # ============================================================

    def test_login_step1_redirects_to_verify_2fa_and_generates_otp(self):
        """Step 1 credentials verify, set pre-auth session, send email OTP, and redirect to /verify-2fa/."""
        resp = self.client.post(reverse('login'), {
            'identifier': self.user.username,
            'password': self.password,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('verify_2fa'))

        # User is NOT authenticated yet
        self.assertEqual(self.client.session.get('pre_auth_user_id'), self.user.id)
        self.assertFalse(self.client.session.get('is_2fa_verified', False))

        # OTP was generated
        self.profile.refresh_from_db()
        self.assertIsNotNone(self.profile.email_otp_hash)

        # Audit events recorded
        self.assertTrue(AuditLog.objects.filter(action='AUTH_LOGIN_STEP1_SUCCESS', user=self.user).exists())
        self.assertTrue(AuditLog.objects.filter(action='AUTH_2FA_OTP_SENT', user=self.user).exists())

    def test_login_step2_verify_with_email_otp_success(self):
        """Step 2 with valid 6-digit OTP logs user in and grants full access."""
        # Step 1
        self.client.post(reverse('login'), {
            'identifier': self.user.username,
            'password': self.password,
        })

        # Capture generated OTP
        self.profile.refresh_from_db()
        raw_otp = self.profile.generate_email_otp()  # Re-generate to know exact value

        # Step 2
        resp = self.client.post(reverse('verify_2fa'), {
            'method': 'otp',
            'otp_code': raw_otp,
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('dashboard_overview'))

        # Session is now fully verified
        self.assertIsNone(self.client.session.get('pre_auth_user_id'))
        self.assertTrue(self.client.session.get('is_2fa_verified'))

        # Dashboard accessible
        dashboard_resp = self.client.get(reverse('dashboard_overview'))
        self.assertEqual(dashboard_resp.status_code, 200)

        # Audit event recorded
        self.assertTrue(AuditLog.objects.filter(action='AUTH_2FA_OTP_SUCCESS', user=self.user).exists())
        self.assertTrue(AuditLog.objects.filter(action='AUTH_LOGIN_SUCCESS', user=self.user).exists())

    def test_login_step2_verify_with_2fa_pin_success(self):
        """Step 2 with configured 4-digit PIN logs user in and grants full access."""
        self.profile.set_pin('9182')

        # Step 1
        self.client.post(reverse('login'), {
            'identifier': self.user.username,
            'password': self.password,
        })

        # Step 2 with PIN
        resp = self.client.post(reverse('verify_2fa'), {
            'method': 'pin',
            'pin': '9182',
        })
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('dashboard_overview'))

        # Session is fully verified
        self.assertTrue(self.client.session.get('is_2fa_verified'))
        self.assertTrue(AuditLog.objects.filter(action='AUTH_2FA_PIN_SUCCESS', user=self.user).exists())

    def test_login_step2_verify_with_incorrect_otp_fails(self):
        """Step 2 with wrong OTP fails and keeps user on 2FA page."""
        self.client.post(reverse('login'), {
            'identifier': self.user.username,
            'password': self.password,
        })

        resp = self.client.post(reverse('verify_2fa'), {
            'method': 'otp',
            'otp_code': '000000',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Incorrect code")
        self.assertFalse(self.client.session.get('is_2fa_verified', False))

    def test_unauthenticated_attempt_to_bypass_2fa_is_blocked(self):
        """Attempting to access protected pages without passing 2FA redirects to login/verify."""
        # Pre-auth user trying to access dashboard directly
        session = self.client.session
        session['pre_auth_user_id'] = self.user.id
        session.save()

        resp = self.client.get(reverse('dashboard_overview'))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('verify_2fa'))

    def test_logout_terminates_2fa_session(self):
        """Logout terminates authenticated session and clears 2FA status."""
        self.client.force_login(self.user)
        session = self.client.session
        session['is_2fa_verified'] = True
        session.save()

        resp = self.client.get(reverse('logout'))
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, reverse('login'))
        self.assertFalse(self.client.session.get('is_2fa_verified', False))

    # ============================================================
    # 4. ACCOUNT SECURITY & PIN MANAGEMENT VIEWS
    # ============================================================

    def test_set_pin_view(self):
        """Authenticated user can configure a 4-digit PIN via Account Security."""
        self.client.force_login(self.user)
        resp = self.client.post(reverse('account_security_set_pin'), {
            'pin': '7429',
            'confirm_pin': '7429',
        })
        self.assertEqual(resp.status_code, 302)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.is_pin_configured)
        self.assertTrue(self.profile.check_pin('7429')[0])
        self.assertTrue(AuditLog.objects.filter(action='2FA_PIN_SET', user=self.user).exists())

    def test_change_pin_view(self):
        """Authenticated user can change their 4-digit PIN."""
        self.profile.set_pin('7429')
        self.client.force_login(self.user)

        resp = self.client.post(reverse('account_security_change_pin'), {
            'old_pin': '7429',
            'new_pin': '9183',
            'confirm_new_pin': '9183',
        })
        self.assertEqual(resp.status_code, 302)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.check_pin('9183')[0])
        self.assertFalse(self.profile.check_pin('7429')[0])
        self.assertTrue(AuditLog.objects.filter(action='2FA_PIN_CHANGED', user=self.user).exists())

    def test_remove_pin_view_with_password(self):
        """Authenticated user can remove their 4-digit PIN confirming with password."""
        self.profile.set_pin('7429')
        self.client.force_login(self.user)

        resp = self.client.post(reverse('account_security_remove_pin'), {
            'confirmation_secret': self.password,
        })
        self.assertEqual(resp.status_code, 302)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.is_pin_configured)
        self.assertTrue(AuditLog.objects.filter(action='2FA_PIN_REMOVED', user=self.user).exists())

    def test_admin_reset_user_2fa_pin(self):
        """Superuser administrator can reset a user's 2FA PIN."""
        self.profile.set_pin('7429')
        admin_user = User.objects.create_superuser(
            username='adminuser',
            email='admin@menardtrading.com',
            password='AdminPassword123!'
        )
        self.client.force_login(admin_user)

        resp = self.client.post(reverse('admin_reset_user_2fa_pin', kwargs={'pk': self.user.id}))
        self.assertEqual(resp.status_code, 302)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.is_pin_configured)
        self.assertTrue(AuditLog.objects.filter(action='2FA_PIN_ADMIN_RESET').exists())
