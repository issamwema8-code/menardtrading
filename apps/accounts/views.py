import json
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.contrib import messages
from django.http import JsonResponse
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.views.decorators.csrf import csrf_protect
from django.conf import settings

from .models import UserProfile, Role, SystemPermission, AuditLog, CompanySettings
from .permissions import require_permission, PermissionRequiredMixin, render_access_denied, has_permission
from .audit import log_audit_event

from menard_core.brevo_email import send_departmental_email
from menard_core.image_cleaner import clean_uploaded_image


from django.contrib.auth.mixins import LoginRequiredMixin


def mask_email(email: str) -> str:
    """Masks an email address for privacy on 2FA verification screen."""
    if not email or '@' not in email:
        return email
    local_part, domain = email.split('@', 1)
    if len(local_part) <= 2:
        masked_local = local_part[0] + '*'
    else:
        masked_local = local_part[0] + ('*' * (len(local_part) - 2)) + local_part[-1]
    return f"{masked_local}@{domain}"


class LoginView(View):
    """
    Secure, branded login view for Menard Trading CC operations portal.
    Step 1: Validates credentials and initiates Two-Factor Verification.
    """
    @method_decorator(csrf_protect)
    def get(self, request):
        if request.user.is_authenticated and request.session.get('is_2fa_verified'):
            return redirect('dashboard_overview')
        return render(request, 'accounts/login.html', {
            'next': request.GET.get('next', '')
        })

    @method_decorator(csrf_protect)
    def post(self, request):
        if request.user.is_authenticated and request.session.get('is_2fa_verified'):
            return redirect('dashboard_overview')

        identifier = (
            request.POST.get('identifier') or 
            request.POST.get('username_or_email') or 
            request.POST.get('username') or 
            request.POST.get('email') or 
            ''
        ).strip()
        password = request.POST.get('password', '')
        remember_me = request.POST.get('remember_me') == 'on'
        next_url = request.POST.get('next', '').strip() or 'dashboard_overview'

        if not identifier or not password:
            messages.error(request, "Please enter your email/username and password.")
            return render(request, 'accounts/login.html', {'identifier': identifier, 'next': next_url}, status=200)

        # 1. Resolve username from email if needed
        username = identifier
        candidate_user = User.objects.filter(username__iexact=username).first()
        if not candidate_user and '@' in identifier:
            candidate_user = User.objects.filter(email__iexact=identifier).first()

        if candidate_user:
            username = candidate_user.username
            if candidate_user.check_password(password) and not candidate_user.is_active:
                log_audit_event(
                    request=request,
                    action='AUTH_LOGIN_DISABLED',
                    resource_type='User',
                    resource_id=candidate_user.id,
                    user=candidate_user,
                    user_email=candidate_user.email,
                    result='FAILURE',
                    details={'reason': 'Account is inactive'}
                )
                messages.error(request, "Your account has been deactivated. Please contact your administrator.")
                return render(request, 'accounts/login.html', {'identifier': identifier, 'next': next_url}, status=200)

        # 2. Authenticate Credentials (Step 1)
        user = authenticate(request, username=username, password=password)

        if user is not None:
            profile, _ = UserProfile.objects.get_or_create(user=user)

            # Store pre-authentication state in session (Full session access is NOT granted yet)
            request.session['pre_auth_user_id'] = user.id
            request.session['pre_auth_remember_me'] = remember_me
            request.session['pre_auth_next'] = next_url

            # Generate 6-digit OTP and send transactional email
            otp_code = profile.generate_email_otp()
            if user.email:
                send_departmental_email(
                    department='info',
                    recipient_list=[user.email],
                    subject="Your Verification Code | Menard Trading CC",
                    template_name='emails/two_factor_otp.html',
                    context={
                        'user_name': user.first_name or user.username,
                        'otp_code': otp_code,
                    }
                )

            log_audit_event(
                request=request,
                action='AUTH_LOGIN_STEP1_SUCCESS',
                resource_type='User',
                resource_id=user.id,
                user=user,
                user_email=user.email,
                result='SUCCESS',
                details={'remember_me': remember_me}
            )
            log_audit_event(
                request=request,
                action='AUTH_2FA_OTP_SENT',
                resource_type='User',
                resource_id=user.id,
                user=user,
                user_email=user.email,
                result='SUCCESS'
            )

            return redirect('verify_2fa')
        else:
            log_audit_event(
                request=request,
                action='AUTH_LOGIN_FAILED',
                resource_type='User',
                resource_id=identifier,
                user_email=identifier,
                result='FAILURE',
                details={'identifier': identifier}
            )
            messages.error(request, "Invalid email/username or password.")
            return render(request, 'accounts/login.html', {'identifier': identifier, 'next': next_url}, status=200)


class TwoFactorVerifyView(View):
    """
    Step 2: Two-Factor Verification.
    Validates either the 6-digit Email OTP or user's 4-digit 2FA PIN.
    """
    @method_decorator(csrf_protect)
    def get(self, request):
        if request.user.is_authenticated and request.session.get('is_2fa_verified'):
            return redirect('dashboard_overview')

        user_id = request.session.get('pre_auth_user_id')
        if not user_id:
            return redirect('login')

        user = get_object_or_404(User, pk=user_id)
        profile, _ = UserProfile.objects.get_or_create(user=user)

        return render(request, 'accounts/verify_2fa.html', {
            'has_pin': profile.is_pin_configured,
            'default_method': 'otp',
            'masked_email': mask_email(user.email),
            'user_email': user.email,
            'cooldown_seconds': profile.get_otp_cooldown_remaining_seconds(),
        })

    @method_decorator(csrf_protect)
    def post(self, request):
        user_id = request.session.get('pre_auth_user_id')
        if not user_id:
            return redirect('login')

        user = get_object_or_404(User, pk=user_id)
        profile, _ = UserProfile.objects.get_or_create(user=user)

        method = request.POST.get('method', 'otp')

        if method == 'pin':
            pin = request.POST.get('pin', '').strip()
            ok, msg = profile.check_pin(pin)
            if not ok:
                log_audit_event(
                    request=request,
                    action='AUTH_2FA_PIN_FAILED',
                    resource_type='User',
                    resource_id=user.id,
                    user=user,
                    user_email=user.email,
                    result='FAILURE',
                    details={'reason': msg}
                )
                messages.error(request, msg)
                return render(request, 'accounts/verify_2fa.html', {
                    'has_pin': profile.is_pin_configured,
                    'default_method': 'pin',
                    'masked_email': mask_email(user.email),
                    'user_email': user.email,
                    'cooldown_seconds': profile.get_otp_cooldown_remaining_seconds(),
                }, status=200)

            log_audit_event(
                request=request,
                action='AUTH_2FA_PIN_SUCCESS',
                resource_type='User',
                resource_id=user.id,
                user=user,
                user_email=user.email,
                result='SUCCESS'
            )

        else:
            # Default: Email OTP
            otp_code = request.POST.get('otp_code', '').strip()
            ok, msg = profile.verify_email_otp(otp_code)
            if not ok:
                log_audit_event(
                    request=request,
                    action='AUTH_2FA_OTP_FAILED',
                    resource_type='User',
                    resource_id=user.id,
                    user=user,
                    user_email=user.email,
                    result='FAILURE',
                    details={'reason': msg}
                )
                messages.error(request, msg)
                return render(request, 'accounts/verify_2fa.html', {
                    'has_pin': profile.is_pin_configured,
                    'default_method': 'otp',
                    'masked_email': mask_email(user.email),
                    'user_email': user.email,
                    'cooldown_seconds': profile.get_otp_cooldown_remaining_seconds(),
                }, status=200)

            log_audit_event(
                request=request,
                action='AUTH_2FA_OTP_SUCCESS',
                resource_type='User',
                resource_id=user.id,
                user=user,
                user_email=user.email,
                result='SUCCESS'
            )

        # 3. 2FA Verification Passed -> Complete Django authenticated login session
        remember_me = request.session.pop('pre_auth_remember_me', False)
        next_url = request.session.pop('pre_auth_next', 'dashboard_overview')
        request.session.pop('pre_auth_user_id', None)

        login(request, user)
        request.session['is_2fa_verified'] = True

        if remember_me:
            request.session.set_expiry(1209600)  # 2 weeks
        else:
            request.session.set_expiry(0)  # Browser session close

        log_audit_event(
            request=request,
            action='AUTH_LOGIN_SUCCESS',
            resource_type='User',
            resource_id=user.id,
            user=user,
            user_email=user.email,
            result='SUCCESS',
            details={'method': method, 'remember_me': remember_me}
        )

        messages.success(request, f"Welcome back, {user.get_full_name() or user.username}!")
        return redirect(next_url)


class ResendOTPView(View):
    """
    Resends a new 6-digit OTP code to the user's registered email with a 1-minute (60s) rate-limiting cooldown.
    """
    @method_decorator(csrf_protect)
    def post(self, request):
        user_id = request.session.get('pre_auth_user_id')
        if not user_id:
            return redirect('login')

        user = get_object_or_404(User, pk=user_id)
        profile, _ = UserProfile.objects.get_or_create(user=user)

        if profile.is_otp_cooldown_active:
            remaining = profile.get_otp_cooldown_remaining_seconds()
            messages.info(request, f"Please wait {remaining} seconds before requesting a new code.")
            return redirect('verify_2fa')

        otp_code = profile.generate_email_otp()
        if user.email:
            send_departmental_email(
                department='info',
                recipient_list=[user.email],
                subject="Your Menard Trading CC verification code",
                template_name='emails/two_factor_otp.html',
                context={
                    'user_name': user.first_name or user.username,
                    'otp_code': otp_code,
                }
            )

        log_audit_event(
            request=request,
            action='AUTH_2FA_OTP_RESENT',
            resource_type='User',
            resource_id=user.id,
            user=user,
            user_email=user.email,
            result='SUCCESS'
        )

        messages.success(request, "A new 6-digit verification code has been sent to your email.")
        return redirect('verify_2fa')


class AccountSecuritySettingsView(LoginRequiredMixin, View):
    """
    User Account Security & 2FA Management view.
    """
    def get(self, request):
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        return render(request, 'accounts/security_settings.html', {
            'active_tab': 'security',
            'profile': profile,
        })


class SetPINView(LoginRequiredMixin, View):
    """
    Sets a new 4-digit 2FA PIN for the authenticated user.
    """
    @method_decorator(csrf_protect)
    def post(self, request):
        pin = request.POST.get('pin', '').strip()
        confirm_pin = request.POST.get('confirm_pin', '').strip()

        if pin != confirm_pin:
            messages.error(request, "PINs do not match. Please enter the same 4 digits.")
            return redirect('account_security')

        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        ok, msg = profile.set_pin(pin)
        if not ok:
            messages.error(request, msg)
        else:
            log_audit_event(
                request=request,
                action='2FA_PIN_SET',
                resource_type='User',
                resource_id=request.user.id,
                result='SUCCESS'
            )
            messages.success(request, "Your 4-digit 2FA PIN has been saved successfully.")

        return redirect('account_security')


class ChangePINView(LoginRequiredMixin, View):
    """
    Changes the existing 4-digit 2FA PIN for the authenticated user.
    """
    @method_decorator(csrf_protect)
    def post(self, request):
        old_pin = request.POST.get('old_pin', '').strip()
        new_pin = request.POST.get('new_pin', '').strip()
        confirm_new_pin = request.POST.get('confirm_new_pin', '').strip()

        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        ok, msg = profile.check_pin(old_pin)
        if not ok:
            messages.error(request, "Current PIN is incorrect.")
            return redirect('account_security')

        if new_pin != confirm_new_pin:
            messages.error(request, "New PINs do not match.")
            return redirect('account_security')

        ok, msg = profile.set_pin(new_pin)
        if not ok:
            messages.error(request, msg)
        else:
            log_audit_event(
                request=request,
                action='2FA_PIN_CHANGED',
                resource_type='User',
                resource_id=request.user.id,
                result='SUCCESS'
            )
            messages.success(request, "Your 4-digit 2FA PIN has been updated.")

        return redirect('account_security')


class RemovePINView(LoginRequiredMixin, View):
    """
    Removes the configured 4-digit 2FA PIN after password or PIN verification.
    """
    @method_decorator(csrf_protect)
    def post(self, request):
        secret = request.POST.get('confirmation_secret', '').strip()
        profile, _ = UserProfile.objects.get_or_create(user=request.user)

        # Allow confirmation via either account password or current 4-digit PIN
        is_password_valid = request.user.check_password(secret)
        is_pin_valid = False
        if profile.is_pin_configured and len(secret) == 4 and secret.isdigit():
            is_pin_valid, _ = profile.check_pin(secret)

        if not is_password_valid and not is_pin_valid:
            messages.error(request, "Incorrect password or PIN confirmation.")
            return redirect('account_security')

        profile.clear_pin()
        log_audit_event(
            request=request,
            action='2FA_PIN_REMOVED',
            resource_type='User',
            resource_id=request.user.id,
            result='SUCCESS'
        )
        messages.success(request, "Your 2FA PIN has been removed. You will continue to use email verification.")
        return redirect('account_security')


class AdminReset2FAPINView(PermissionRequiredMixin, View):
    """
    Administrator action to reset a user's 2FA PIN if they are locked out or forgot their PIN.
    """
    required_permissions = ('users.update',)

    def post(self, request, pk):
        target_user = get_object_or_404(User, pk=pk)

        # Prevent non-superusers from modifying superuser accounts
        if target_user.is_superuser and not request.user.is_superuser:
            messages.error(request, "Only superusers can reset 2FA for other superuser accounts.")
            return redirect('administration-users')

        profile, _ = UserProfile.objects.get_or_create(user=target_user)
        profile.clear_pin()

        log_audit_event(
            request=request,
            action='2FA_PIN_ADMIN_RESET',
            resource_type='User',
            resource_id=target_user.id,
            details={
                'target_username': target_user.username,
                'target_email': target_user.email,
                'reset_by': request.user.username,
            },
            result='SUCCESS'
        )

        messages.success(request, f"2FA PIN for '{target_user.username}' has been reset.")
        return redirect('administration-users')


class LogoutView(View):
    """
    Terminates user session, clears 2FA status, and redirects to login page.
    """
    def post(self, request):
        return self._logout_user(request)

    def get(self, request):
        return self._logout_user(request)

    def _logout_user(self, request):
        if request.user.is_authenticated:
            log_audit_event(
                request=request,
                action='AUTH_LOGOUT',
                resource_type='User',
                resource_id=request.user.id,
                result='SUCCESS'
            )
            request.session.pop('is_2fa_verified', None)
            logout(request)
            messages.info(request, "You have been logged out successfully.")
        return redirect('login')


class AccessDeniedView(View):
    """
    Displays clean, user-friendly 403 Forbidden page.
    """
    def get(self, request):
        return render(request, 'accounts/access_denied.html', {
            'error_title': 'Access Denied',
            'error_message': 'You do not have sufficient permissions to access this page or perform this action.'
        }, status=403)


# ============================================================
# USER MANAGEMENT VIEWS
# ============================================================

class UserManagementView(PermissionRequiredMixin, View):
    """
    Lists all users with search, role badges, and status controls.
    """
    required_permissions = ('users.view',)

    def get(self, request):
        users = User.objects.select_related('profile').prefetch_related('profile__roles').order_by('-date_joined')
        roles = Role.objects.filter(is_active=True).order_by('name')
        return render(request, 'accounts/users_list.html', {
            'active_tab': 'admin_users',
            'users': users,
            'roles': roles,
        })


class CreateUserView(PermissionRequiredMixin, View):
    """
    Creates a new application user and assigns specified roles.
    """
    required_permissions = ('users.create',)

    def post(self, request):
        username = request.POST.get('username', '').strip()
        email = request.POST.get('email', '').strip()
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        password = request.POST.get('password', '').strip()
        role_ids = request.POST.getlist('roles')
        is_superuser_requested = request.POST.get('is_superuser') == 'on'

        if not username or not email or not password:
            messages.error(request, "Username, email, and password are required.")
            return redirect('admin_users_list')

        if User.objects.filter(username__iexact=username).exists():
            messages.error(request, f"Username '{username}' is already taken.")
            return redirect('admin_users_list')

        if User.objects.filter(email__iexact=email).exists():
            messages.error(request, f"A user with email '{email}' already exists.")
            return redirect('admin_users_list')

        # Create user
        user = User.objects.create_user(
            username=username,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name
        )

        # Superuser flag can only be granted by existing superusers
        if is_superuser_requested and request.user.is_superuser:
            user.is_superuser = True
            user.is_staff = True
            user.save()

        # Assign roles
        profile, _ = UserProfile.objects.get_or_create(user=user)
        if role_ids:
            profile.roles.set(Role.objects.filter(id__in=role_ids))

        log_audit_event(
            request=request,
            action='USER_CREATED',
            resource_type='User',
            resource_id=user.id,
            details={
                'username': user.username,
                'email': user.email,
                'roles': list(profile.roles.values_list('name', flat=True)),
                'is_superuser': user.is_superuser
            }
        )

        messages.success(request, f"User '{user.username}' created successfully.")
        return redirect('administration-users')


class UpdateUserView(PermissionRequiredMixin, View):
    """
    Updates user personal details, active status, and assigned roles.
    """
    required_permissions = ('users.update',)

    def post(self, request, pk):
        target_user = get_object_or_404(User, pk=pk)

        # Prevent non-superusers from modifying superuser accounts
        if target_user.is_superuser and not request.user.is_superuser:
            messages.error(request, "Only superusers can modify other superuser accounts.")
            return redirect('administration-users')

        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        email = request.POST.get('email', '').strip()
        role_ids = request.POST.getlist('roles')
        is_active = request.POST.get('is_active') == 'on'
        new_password = request.POST.get('new_password', '').strip()

        # Prevent deactivating the last active superuser
        if target_user.is_superuser and not is_active:
            active_superusers = User.objects.filter(is_superuser=True, is_active=True).exclude(pk=target_user.pk)
            if not active_superusers.exists():
                messages.error(request, "Cannot deactivate the only remaining active superuser.")
                return redirect('administration-users')

        target_user.first_name = first_name
        target_user.last_name = last_name
        if email and not User.objects.filter(email__iexact=email).exclude(pk=target_user.pk).exists():
            target_user.email = email
        target_user.is_active = is_active

        if new_password:
            target_user.set_password(new_password)

        target_user.save()

        # Update roles
        profile, _ = UserProfile.objects.get_or_create(user=target_user)
        profile.roles.set(Role.objects.filter(id__in=role_ids))

        log_audit_event(
            request=request,
            action='USER_UPDATED',
            resource_type='User',
            resource_id=target_user.id,
            details={
                'username': target_user.username,
                'email': target_user.email,
                'is_active': target_user.is_active,
                'roles': list(profile.roles.values_list('name', flat=True))
            }
        )

        messages.success(request, f"User '{target_user.username}' updated successfully.")
        return redirect('administration-users')


class ToggleUserActiveView(PermissionRequiredMixin, View):
    """
    Quick 1-click activate/deactivate user status.
    """
    required_permissions = ('users.delete',)

    def post(self, request, pk):
        target_user = get_object_or_404(User, pk=pk)
        if target_user == request.user:
            messages.error(request, "You cannot deactivate your own account.")
            return redirect('administration-users')

        if target_user.is_superuser and not request.user.is_superuser:
            messages.error(request, "Only superusers can modify superuser accounts.")
            return redirect('administration-users')

        target_user.is_active = not target_user.is_active
        target_user.save()

        action_str = "activated" if target_user.is_active else "deactivated"
        log_audit_event(
            request=request,
            action='USER_STATUS_TOGGLED',
            resource_type='User',
            resource_id=target_user.id,
            details={'is_active': target_user.is_active}
        )
        messages.success(request, f"User '{target_user.username}' has been {action_str}.")
        return redirect('administration-users')


# ============================================================
# ROLE & PERMISSION MANAGEMENT VIEWS
# ============================================================

class RoleManagementView(PermissionRequiredMixin, View):
    """
    Lists all custom and system roles with grouped permissions.
    """
    required_permissions = ('roles.view',)

    def get(self, request):
        roles = Role.objects.prefetch_related('permissions', 'user_profiles').order_by('-is_system', 'name')
        permissions_by_module = {}
        all_perms = SystemPermission.objects.all().order_by('module', 'name')
        
        for p in all_perms:
            module_name = p.module.capitalize()
            if module_name not in permissions_by_module:
                permissions_by_module[module_name] = []
            permissions_by_module[module_name].append(p)

        return render(request, 'accounts/roles_list.html', {
            'active_tab': 'admin_roles',
            'roles': roles,
            'permissions_by_module': permissions_by_module,
        })


class CreateRoleView(PermissionRequiredMixin, View):
    """
    Creates a new custom role with selected permissions.
    """
    required_permissions = ('roles.create',)

    def post(self, request):
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        permission_ids = request.POST.getlist('permissions')

        if not name:
            messages.error(request, "Role name is required.")
            return redirect('administration-roles')

        if Role.objects.filter(name__iexact=name).exists():
            messages.error(request, f"A role named '{name}' already exists.")
            return redirect('administration-roles')

        role = Role.objects.create(
            name=name,
            description=description,
            is_active=True,
            is_system=False
        )
        if permission_ids:
            role.permissions.set(SystemPermission.objects.filter(id__in=permission_ids))

        log_audit_event(
            request=request,
            action='ROLE_CREATED',
            resource_type='Role',
            resource_id=role.id,
            details={
                'name': role.name,
                'permissions_count': role.permissions.count(),
                'permissions': list(role.permissions.values_list('codename', flat=True))
            }
        )

        messages.success(request, f"Role '{role.name}' created with {role.permissions.count()} permission(s).")
        return redirect('administration-roles')


class UpdateRoleView(PermissionRequiredMixin, View):
    """
    Updates role description and assigned permissions.
    """
    required_permissions = ('roles.update',)

    def post(self, request, pk):
        role = get_object_or_404(Role, pk=pk)
        name = request.POST.get('name', '').strip()
        description = request.POST.get('description', '').strip()
        permission_ids = request.POST.getlist('permissions')

        if name and not role.is_system:
            if not Role.objects.filter(name__iexact=name).exclude(pk=role.pk).exists():
                role.name = name

        role.description = description
        role.save()

        # Update permissions
        role.permissions.set(SystemPermission.objects.filter(id__in=permission_ids))

        log_audit_event(
            request=request,
            action='ROLE_UPDATED',
            resource_type='Role',
            resource_id=role.id,
            details={
                'name': role.name,
                'permissions_count': role.permissions.count(),
                'permissions': list(role.permissions.values_list('codename', flat=True))
            }
        )

        messages.success(request, f"Role '{role.name}' updated successfully.")
        return redirect('administration-roles')


class DeleteRoleView(PermissionRequiredMixin, View):
    """
    Deletes a custom role (protected system roles cannot be deleted).
    """
    required_permissions = ('roles.delete',)

    def post(self, request, pk):
        role = get_object_or_404(Role, pk=pk)
        if role.is_system:
            messages.error(request, "System default roles cannot be deleted.")
            return redirect('administration-roles')

        role_name = role.name
        log_audit_event(
            request=request,
            action='ROLE_DELETED',
            resource_type='Role',
            resource_id=role.id,
            details={'name': role_name}
        )
        role.delete()
        messages.success(request, f"Role '{role_name}' has been deleted.")
        return redirect('administration-roles')


# ============================================================
# AUDIT LOG VIEWER
# ============================================================

class AuditLogListView(PermissionRequiredMixin, View):
    """
    Security audit trail viewer.
    """
    required_permissions = ('audit.view',)

    def get(self, request):
        logs = AuditLog.objects.select_related('user').order_by('-created_at')[:200]
        return render(request, 'accounts/audit_logs.html', {
            'active_tab': 'admin_audit',
            'logs': logs,
        })


# ============================================================
# PASSWORD RESET VIEWS (FORGOT PASSWORD WORKFLOW)
# ============================================================

class ForgotPasswordView(View):
    """
    Handles user password reset requests.
    Dispatches a secure 1-time reset link via transactional email.
    """
    @method_decorator(csrf_protect)
    def get(self, request):
        if request.user.is_authenticated:
            return redirect('dashboard_overview')
        return render(request, 'accounts/forgot_password.html')

    @method_decorator(csrf_protect)
    def post(self, request):
        if request.user.is_authenticated:
            return redirect('dashboard_overview')

        identifier = request.POST.get('identifier', '').strip()
        if identifier:
            # Look up user by email or username
            user = User.objects.filter(username__iexact=identifier).first()
            if not user and '@' in identifier:
                user = User.objects.filter(email__iexact=identifier).first()

            if user and user.is_active and user.email:
                uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
                token = default_token_generator.make_token(user)
                reset_url = request.build_absolute_uri(
                    reverse('reset_password_confirm', kwargs={'uidb64': uidb64, 'token': token})
                )

                send_departmental_email(
                    department='info',
                    recipient_list=[user.email],
                    subject="Password Reset Request | Menard Trading CC",
                    template_name='emails/password_reset.html',
                    context={
                        'user': user,
                        'reset_url': reset_url,
                    }
                )

                log_audit_event(
                    request=request,
                    action='AUTH_PASSWORD_RESET_REQUESTED',
                    resource_type='User',
                    resource_id=user.id,
                    user=user,
                    user_email=user.email,
                    result='SUCCESS',
                    details={'identifier': identifier}
                )

        return redirect('forgot_password_done')


class ForgotPasswordDoneView(View):
    """
    Displays confirmation that a password reset email has been dispatched.
    """
    def get(self, request):
        return render(request, 'accounts/forgot_password_done.html')


class ResetPasswordConfirmView(View):
    """
    Validates password reset token and allows the user to set a new password.
    """
    def _get_user(self, uidb64):
        try:
            uid = force_str(urlsafe_base64_decode(uidb64))
            return User.objects.filter(pk=uid, is_active=True).first()
        except (TypeError, ValueError, OverflowError):
            return None

    @method_decorator(csrf_protect)
    def get(self, request, uidb64, token):
        user = self._get_user(uidb64)
        if not user or not default_token_generator.check_token(user, token):
            return render(request, 'accounts/reset_password_invalid.html', status=200)

        return render(request, 'accounts/reset_password_confirm.html', {
            'uidb64': uidb64,
            'token': token,
            'target_user': user,
        })

    @method_decorator(csrf_protect)
    def post(self, request, uidb64, token):
        user = self._get_user(uidb64)
        if not user or not default_token_generator.check_token(user, token):
            return render(request, 'accounts/reset_password_invalid.html', status=200)

        new_password = request.POST.get('new_password', '').strip()
        confirm_password = request.POST.get('confirm_password', '').strip()

        if len(new_password) < 8:
            messages.error(request, "Password must be at least 8 characters long.")
            return render(request, 'accounts/reset_password_confirm.html', {
                'uidb64': uidb64,
                'token': token,
                'target_user': user,
            }, status=200)

        if new_password != confirm_password:
            messages.error(request, "Passwords do not match. Please re-enter.")
            return render(request, 'accounts/reset_password_confirm.html', {
                'uidb64': uidb64,
                'token': token,
                'target_user': user,
            }, status=200)

        user.set_password(new_password)
        user.save()

        log_audit_event(
            request=request,
            action='AUTH_PASSWORD_RESET_COMPLETED',
            resource_type='User',
            resource_id=user.id,
            user=user,
            user_email=user.email,
            result='SUCCESS',
            details={'username': user.username}
        )

        return redirect('reset_password_complete')


class ResetPasswordCompleteView(View):
    """
    Displays confirmation that the password was reset successfully.
    """
    def get(self, request):
        return render(request, 'accounts/reset_password_complete.html')


class CompanySettingsView(LoginRequiredMixin, View):
    """
    Centralized Company & Business Information management view.
    Allows authorized administrators to manage business details, addresses, branding,
    and banking information with RBAC enforcement (settings.view, settings.edit, settings.update).
    """
    def get(self, request):
        if not (request.user.is_superuser or has_permission(request.user, 'settings.view', 'settings.edit', 'settings.update')):
            return render_access_denied(request, "You do not have permission to view company settings.")

        settings_obj = CompanySettings.get_settings()
        can_edit = request.user.is_superuser or has_permission(request.user, 'settings.edit', 'settings.update')

        return render(request, 'accounts/company_settings.html', {
            'active_tab': 'administration',
            'active_subtab': 'company_settings',
            'company_settings': settings_obj,
            'can_edit': can_edit,
        })

    @method_decorator(csrf_protect)
    def post(self, request):
        if not (request.user.is_superuser or has_permission(request.user, 'settings.edit', 'settings.update')):
            return render_access_denied(request, "You do not have permission to edit company settings.")

        settings_obj = CompanySettings.get_settings()

        # Business Identity
        settings_obj.company_name = request.POST.get('company_name', '').strip() or 'MENARD TRADING CC'
        settings_obj.tagline = request.POST.get('tagline', '').strip() or 'ALWAYS ON TIME'

        # Addresses
        settings_obj.postal_address = request.POST.get('postal_address', '').strip()
        settings_obj.physical_address = request.POST.get('physical_address', '').strip()
        settings_obj.city = request.POST.get('city', '').strip()
        settings_obj.country = request.POST.get('country', '').strip()

        # Tax & Legal
        settings_obj.vat_number = request.POST.get('vat_number', '').strip()
        settings_obj.company_reg_number = request.POST.get('company_reg_number', '').strip()

        # Contact Details
        settings_obj.email = request.POST.get('email', '').strip()
        settings_obj.orders_email = request.POST.get('orders_email', '').strip()
        settings_obj.quotes_email = request.POST.get('quotes_email', '').strip()
        settings_obj.accounts_email = request.POST.get('accounts_email', '').strip()
        settings_obj.phone = request.POST.get('phone', '').strip()
        settings_obj.mobile = request.POST.get('mobile', '').strip()
        settings_obj.website = request.POST.get('website', '').strip()

        # Bank Settlement Details
        settings_obj.bank_name = request.POST.get('bank_name', '').strip()
        settings_obj.account_name = request.POST.get('account_name', '').strip()
        settings_obj.account_number = request.POST.get('account_number', '').strip()
        settings_obj.account_type = request.POST.get('account_type', '').strip()
        settings_obj.branch_code = request.POST.get('branch_code', '').strip()
        settings_obj.branch_name = request.POST.get('branch_name', '').strip()
        settings_obj.swift_code = request.POST.get('swift_code', '').strip()

        # Handle Logo Image Upload
        if 'logo_image' in request.FILES:
            settings_obj.logo_image = clean_uploaded_image(request.FILES['logo_image'])
        elif request.POST.get('remove_logo') == '1':
            if settings_obj.logo_image:
                settings_obj.logo_image.delete(save=False)
            settings_obj.logo_image = None

        # Handle Background Image 1 Upload
        if 'login_bg_image_1' in request.FILES:
            settings_obj.login_bg_image_1 = clean_uploaded_image(request.FILES['login_bg_image_1'])
        elif request.POST.get('remove_login_bg_1') == '1':
            if settings_obj.login_bg_image_1:
                settings_obj.login_bg_image_1.delete(save=False)
            settings_obj.login_bg_image_1 = None

        # Handle Background Image 2 Upload
        if 'login_bg_image_2' in request.FILES:
            settings_obj.login_bg_image_2 = clean_uploaded_image(request.FILES['login_bg_image_2'])
        elif request.POST.get('remove_login_bg_2') == '1':
            if settings_obj.login_bg_image_2:
                settings_obj.login_bg_image_2.delete(save=False)
            settings_obj.login_bg_image_2 = None

        # Handle Background Image 3 Upload
        if 'login_bg_image_3' in request.FILES:
            settings_obj.login_bg_image_3 = clean_uploaded_image(request.FILES['login_bg_image_3'])
        elif request.POST.get('remove_login_bg_3') == '1':
            if settings_obj.login_bg_image_3:
                settings_obj.login_bg_image_3.delete(save=False)
            settings_obj.login_bg_image_3 = None

        settings_obj.updated_by = request.user
        settings_obj.save()

        log_audit_event(
            request=request,
            action='COMPANY_SETTINGS_UPDATED',
            resource_type='CompanySettings',
            resource_id=settings_obj.id,
            result='SUCCESS',
            details={
                'company_name': settings_obj.company_name,
                'vat_number': settings_obj.vat_number,
                'email': settings_obj.email,
            }
        )

        messages.success(request, "Company & Business Information updated successfully.")
        return redirect('administration-company-settings')


