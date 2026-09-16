import uuid
from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APIClient
from rest_framework.authtoken.models import Token

from apps.accounts.models import SystemPermission, Role, UserProfile, AuditLog
from apps.accounts.permissions import has_permission, get_user_permissions
from apps.customers.models import Customer
from apps.orders.models import PurchaseOrder
from apps.quotes.models import Quotation
from apps.logistics.models import LogisticsJob
from apps.billing.models import Invoice, PaymentReceipt


class AuthenticationAndRBACTestCase(TestCase):
    def setUp(self):
        # Create test superuser
        self.superuser = User.objects.create_superuser(
            username='superuser',
            email='super@menardtrading.com',
            password='Password123!'
        )

        # Create permissions if not created by signal in test db
        self.perm_orders_view, _ = SystemPermission.objects.get_or_create(
            codename='orders.view',
            defaults={'name': 'View Purchase Orders', 'module': 'orders'}
        )
        self.perm_orders_upload, _ = SystemPermission.objects.get_or_create(
            codename='orders.upload',
            defaults={'name': 'Upload Purchase Order', 'module': 'orders'}
        )
        self.perm_quotes_view, _ = SystemPermission.objects.get_or_create(
            codename='quotations.view',
            defaults={'name': 'View Quotations', 'module': 'quotations'}
        )
        self.perm_invoices_view, _ = SystemPermission.objects.get_or_create(
            codename='invoices.view',
            defaults={'name': 'View Invoices', 'module': 'invoices'}
        )
        self.perm_invoices_create, _ = SystemPermission.objects.get_or_create(
            codename='invoices.create',
            defaults={'name': 'Create Invoice', 'module': 'invoices'}
        )
        self.perm_invoices_payment, _ = SystemPermission.objects.get_or_create(
            codename='invoices.record_payment',
            defaults={'name': 'Record Payment', 'module': 'invoices'}
        )
        self.perm_users_manage, _ = SystemPermission.objects.get_or_create(
            codename='users.view',
            defaults={'name': 'View Users', 'module': 'users'}
        )

        # Create custom roles
        self.role_orders_viewer = Role.objects.create(
            name='PO Viewer',
            description='Can only view purchase orders'
        )
        self.role_orders_viewer.permissions.add(self.perm_orders_view)

        self.role_finance_officer = Role.objects.create(
            name='Finance Officer',
            description='Can view and create invoices'
        )
        self.role_finance_officer.permissions.add(self.perm_invoices_view, self.perm_invoices_create, self.perm_invoices_payment)

        # Create regular users
        self.user_viewer = User.objects.create_user(
            username='viewer',
            email='viewer@menardtrading.com',
            password='Password123!'
        )
        self.profile_viewer, _ = UserProfile.objects.get_or_create(user=self.user_viewer)
        self.profile_viewer.roles.add(self.role_orders_viewer)

        self.user_finance = User.objects.create_user(
            username='finance',
            email='finance@menardtrading.com',
            password='Password123!'
        )
        self.profile_finance, _ = UserProfile.objects.get_or_create(user=self.user_finance)
        self.profile_finance.roles.add(self.role_finance_officer)

        self.user_unassigned = User.objects.create_user(
            username='unassigned',
            email='unassigned@menardtrading.com',
            password='Password123!'
        )
        UserProfile.objects.get_or_create(user=self.user_unassigned)

        self.inactive_user = User.objects.create_user(
            username='inactive',
            email='inactive@menardtrading.com',
            password='Password123!',
            is_active=False
        )

        # Create test customer & quote for portal testing
        self.customer = Customer.objects.create(
            company_name='Test Logistics Client CC',
            contact_name='Alice Smith',
            email='alice@client.com',
            phone='+27112223333',
            physical_address='10 Freight Way, Durban'
        )
        self.quote = Quotation.objects.create(
            customer=self.customer,
            notes='Test quotation'
        )

        self.client = Client()
        self.api_client = APIClient()

    # ========================================================
    # 1. AUTHENTICATION & PASSWORD RESET TESTS
    # ========================================================
    def test_login_page_renders_remember_me_and_forgot_password(self):
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Remember me")
        self.assertContains(response, "Forgot password?")
        self.assertContains(response, reverse('forgot_password'))

    def test_login_success_with_username(self):
        response = self.client.post(reverse('login'), {
            'username_or_email': 'viewer',
            'password': 'Password123!'
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('verify_2fa'))

        # Complete 2FA Step 2 with OTP
        self.user_viewer.profile.refresh_from_db()
        raw_otp = self.user_viewer.profile.generate_email_otp()
        verify_resp = self.client.post(reverse('verify_2fa'), {
            'method': 'otp',
            'otp_code': raw_otp,
        })
        self.assertEqual(verify_resp.status_code, 302)
        self.assertRedirects(verify_resp, reverse('dashboard_overview'))

    def test_login_success_with_email(self):
        response = self.client.post(reverse('login'), {
            'username_or_email': 'viewer@menardtrading.com',
            'password': 'Password123!'
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('verify_2fa'))

        # Complete 2FA Step 2 with OTP
        self.user_viewer.profile.refresh_from_db()
        raw_otp = self.user_viewer.profile.generate_email_otp()
        verify_resp = self.client.post(reverse('verify_2fa'), {
            'method': 'otp',
            'otp_code': raw_otp,
        })
        self.assertEqual(verify_resp.status_code, 302)
        self.assertRedirects(verify_resp, reverse('dashboard_overview'))

    def test_login_failure_with_wrong_password(self):
        response = self.client.post(reverse('login'), {
            'username_or_email': 'viewer',
            'password': 'WrongPassword999'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Invalid email/username or password.")

    def test_login_failure_inactive_user(self):
        response = self.client.post(reverse('login'), {
            'username_or_email': 'inactive',
            'password': 'Password123!'
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Your account has been deactivated.")

    def test_logout(self):
        self.client.login(username='viewer', password='Password123!')
        response = self.client.get(reverse('logout'))
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('login'))

    def test_unauthenticated_redirect(self):
        response = self.client.get(reverse('orders_list'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_forgot_password_page_renders(self):
        response = self.client.get(reverse('forgot_password'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Forgot Password")
        self.assertContains(response, "Send Reset Link")

    def test_forgot_password_request_with_valid_email(self):
        response = self.client.post(reverse('forgot_password'), {
            'identifier': 'viewer@menardtrading.com'
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('forgot_password_done'))

        # Check audit log
        audit = AuditLog.objects.filter(action='AUTH_PASSWORD_RESET_REQUESTED', user=self.user_viewer).first()
        self.assertIsNotNone(audit)

    def test_forgot_password_request_with_valid_username(self):
        response = self.client.post(reverse('forgot_password'), {
            'identifier': 'finance'
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('forgot_password_done'))

    def test_forgot_password_request_unknown_user_safely_redirects(self):
        response = self.client.post(reverse('forgot_password'), {
            'identifier': 'nonexistent@nowhere.com'
        })
        self.assertEqual(response.status_code, 302)
        self.assertRedirects(response, reverse('forgot_password_done'))

    def test_password_reset_confirm_and_new_password_flow(self):
        from django.contrib.auth.tokens import default_token_generator
        from django.utils.http import urlsafe_base64_encode
        from django.utils.encoding import force_bytes

        uidb64 = urlsafe_base64_encode(force_bytes(self.user_viewer.pk))
        token = default_token_generator.make_token(self.user_viewer)

        # GET confirm page
        url = reverse('reset_password_confirm', kwargs={'uidb64': uidb64, 'token': token})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Set New Password")

        # POST new password
        post_response = self.client.post(url, {
            'new_password': 'BrandNewPassword2026!',
            'confirm_password': 'BrandNewPassword2026!',
        })
        self.assertEqual(post_response.status_code, 302)
        self.assertRedirects(post_response, reverse('reset_password_complete'))

        # Verify user can login with new password and complete 2FA
        login_response = self.client.post(reverse('login'), {
            'username_or_email': 'viewer',
            'password': 'BrandNewPassword2026!'
        })
        self.assertEqual(login_response.status_code, 302)
        self.assertRedirects(login_response, reverse('verify_2fa'))

        self.user_viewer.profile.refresh_from_db()
        raw_otp = self.user_viewer.profile.generate_email_otp()
        verify_resp = self.client.post(reverse('verify_2fa'), {
            'method': 'otp',
            'otp_code': raw_otp,
        })
        self.assertEqual(verify_resp.status_code, 302)
        self.assertRedirects(verify_resp, reverse('dashboard_overview'))

    def test_password_reset_confirm_invalid_token_rejected(self):
        url = reverse('reset_password_confirm', kwargs={'uidb64': 'invalid-uid', 'token': 'invalid-token'})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Link Expired or Invalid")

    # ========================================================
    # 2. RBAC PERMISSION ENFORCEMENT TESTS
    # ========================================================
    def test_has_permission_helper(self):
        self.assertTrue(has_permission(self.superuser, 'orders.view'))
        self.assertTrue(has_permission(self.superuser, 'anything.wildcard'))
        self.assertTrue(has_permission(self.user_viewer, 'orders.view'))
        self.assertFalse(has_permission(self.user_viewer, 'orders.upload'))
        self.assertFalse(has_permission(self.user_viewer, 'invoices.view'))
        self.assertTrue(has_permission(self.user_finance, 'invoices.view'))
        self.assertTrue(has_permission(self.user_finance, 'invoices.create'))

    def test_authorized_user_access_orders_page(self):
        self.client.login(username='viewer', password='Password123!')
        response = self.client.get(reverse('orders_list'))
        self.assertEqual(response.status_code, 200)

    def test_unauthorized_user_blocked_from_invoices_page(self):
        self.client.login(username='viewer', password='Password123!')
        response = self.client.get(reverse('invoices_list'))
        self.assertEqual(response.status_code, 403)
        self.assertContains(response, "Access Denied", status_code=403)

    def test_user_without_permission_cannot_upload_po(self):
        self.client.login(username='viewer', password='Password123!')
        response = self.client.post(reverse('upload_purchase_order'), {'po_number': 'PO-TEST'})
        self.assertEqual(response.status_code, 403)

    # ========================================================
    # 3. MULTI-ROLE COMBINATION & REMOVAL TESTS
    # ========================================================
    def test_multi_role_combines_permissions(self):
        # Assign Finance Officer role to viewer as well
        self.profile_viewer.roles.add(self.role_finance_officer)
        effective = get_user_permissions(self.user_viewer)
        self.assertIn('orders.view', effective)
        self.assertIn('invoices.view', effective)
        self.assertIn('invoices.create', effective)

        # Remove role and verify removal
        self.profile_viewer.roles.remove(self.role_finance_officer)
        effective_after = get_user_permissions(self.user_viewer)
        self.assertIn('orders.view', effective_after)
        self.assertNotIn('invoices.view', effective_after)

    # ========================================================
    # 4. SUPERUSER ADMINISTRATION & HIERARCHY
    # ========================================================
    def test_superuser_accesses_user_management(self):
        self.client.login(username='superuser', password='Password123!')
        response = self.client.get(reverse('administration-users'))
        self.assertEqual(response.status_code, 200)

    def test_regular_user_blocked_from_user_management(self):
        self.client.login(username='viewer', password='Password123!')
        response = self.client.get(reverse('administration-users'))
        self.assertEqual(response.status_code, 403)

    def test_superuser_can_create_custom_role(self):
        self.client.login(username='superuser', password='Password123!')
        response = self.client.post(reverse('administration-roles-create'), {
            'name': 'Custom Warehouse Manager',
            'description': 'Handles warehouse consignments',
            'permissions': [self.perm_orders_view.id, self.perm_quotes_view.id]
        })
        self.assertEqual(response.status_code, 302)
        new_role = Role.objects.filter(name='Custom Warehouse Manager').first()
        self.assertIsNotNone(new_role)
        self.assertEqual(new_role.permissions.count(), 2)

    # ========================================================
    # 5. REST API AUTH & GRANULAR PERMISSIONS
    # ========================================================
    def test_api_login_and_token(self):
        response = self.api_client.post(reverse('api-auth-login'), {
            'username': 'finance',
            'password': 'Password123!'
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('token', response.data)
        self.assertIn('invoices.view', response.data['user']['permissions'])

    def test_api_me_endpoint(self):
        token = Token.objects.create(user=self.user_finance)
        self.api_client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        response = self.api_client.get(reverse('api-auth-me'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['user']['email'], 'finance@menardtrading.com')
        self.assertIn('Finance Officer', response.data['user']['roles'])

    def test_api_granular_permission_enforcement(self):
        # Finance user can access invoices API
        token_finance = Token.objects.create(user=self.user_finance)
        self.api_client.credentials(HTTP_AUTHORIZATION=f'Token {token_finance.key}')
        response = self.api_client.get(reverse('api-invoices-list'))
        self.assertEqual(response.status_code, 200)

        # Viewer user without invoices.view is blocked with 403 Forbidden
        token_viewer = Token.objects.create(user=self.user_viewer)
        self.api_client.credentials(HTTP_AUTHORIZATION=f'Token {token_viewer.key}')
        response_viewer = self.api_client.get(reverse('api-invoices-list'))
        self.assertEqual(response_viewer.status_code, 403)

    # ========================================================
    # 6. PUBLIC QUOTATION PORTAL EXEMPTION
    # ========================================================
    def test_public_customer_quote_portal_accessible_without_auth(self):
        # Customer portal uses secure UUID token, does not require staff login
        url = reverse('quote_portal', kwargs={'token': self.quote.approval_token})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.customer.company_name)

    # ========================================================
    # 7. AUDIT LOGGING
    # ========================================================
    def test_audit_log_recorded_on_login(self):
        self.client.post(reverse('login'), {
            'username_or_email': 'viewer',
            'password': 'Password123!'
        })
        step1_log = AuditLog.objects.filter(action='AUTH_LOGIN_STEP1_SUCCESS', user=self.user_viewer).first()
        self.assertIsNotNone(step1_log)
        self.assertEqual(step1_log.result, 'SUCCESS')

        # Complete 2FA
        self.user_viewer.profile.refresh_from_db()
        raw_otp = self.user_viewer.profile.generate_email_otp()
        self.client.post(reverse('verify_2fa'), {
            'method': 'otp',
            'otp_code': raw_otp,
        })
        log = AuditLog.objects.filter(action='AUTH_LOGIN_SUCCESS', user=self.user_viewer).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.result, 'SUCCESS')
