from functools import wraps
from django.shortcuts import render, redirect
from django.http import JsonResponse, HttpResponseForbidden
from django.contrib.auth.mixins import AccessMixin
from rest_framework.permissions import BasePermission
from .audit import log_audit_event


def get_user_permissions(user) -> set:
    """
    Returns the full set of effective granular permission codenames for a given user.
    """
    if not user or not user.is_authenticated:
        return set()

    if user.is_superuser:
        from .models import SystemPermission
        return set(SystemPermission.objects.values_list('codename', flat=True))

    if not user.is_active:
        return set()

    if hasattr(user, 'profile'):
        return user.profile.effective_permissions()
    
    # Fallback if profile doesn't exist yet
    from .models import SystemPermission
    return set(
        SystemPermission.objects.filter(
            roles__user_profiles__user=user,
            roles__is_active=True
        ).values_list('codename', flat=True).distinct()
    )


def has_permission(user, *codenames: str, match_all: bool = False) -> bool:
    """
    Evaluates whether user has the requested permission(s).
    If match_all=False (default), user needs at least one of the listed codenames.
    If match_all=True, user needs all of the listed codenames.
    """
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    if not user.is_active:
        return False

    user_perms = get_user_permissions(user)
    if not codenames:
        return True

    if match_all:
        return all(code in user_perms for code in codenames)
    else:
        return any(code in user_perms for code in codenames)


def render_access_denied(request, message: str = "You do not have permission to access this page or perform this action."):
    """
    Renders a friendly, branded 403 Forbidden page or JSON response without leaking sensitive details.
    """
    if request.headers.get('x-requested-with') == 'XMLHttpRequest' or request.path.startswith('/api/'):
        return JsonResponse({
            'status': 'error',
            'error': 'Forbidden',
            'message': message
        }, status=403)
    
    context = {
        'error_title': 'Access Denied',
        'error_message': message,
        'user': request.user,
    }
    return render(request, 'accounts/access_denied.html', context, status=403)


def require_permission(*codenames: str, match_all: bool = False):
    """
    Decorator for views that enforces granular RBAC permissions.
    Redirects unauthenticated users to login; renders 403 Access Denied if unauthorized.
    """
    def decorator(view_func):
        @wraps(view_func)
        def _wrapped_view(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect(f"/login/?next={request.path}")

            if not has_permission(request.user, *codenames, match_all=match_all):
                log_audit_event(
                    request=request,
                    action='PERMISSION_DENIED',
                    resource_type='View',
                    resource_id=request.path,
                    details={'required_permissions': list(codenames), 'method': request.method},
                    result='DENIED'
                )
                return render_access_denied(request)

            return view_func(request, *args, **kwargs)
        return _wrapped_view
    return decorator


class PermissionRequiredMixin(AccessMixin):
    """
    CBV mixin that verifies the user has the required permission(s).
    """
    permission_required = ()
    required_permissions = ()
    match_all_permissions = False
    permission_denied_message = "You do not have permission to access this resource."

    def get_required_permissions(self):
        perms = self.permission_required or self.required_permissions
        if isinstance(perms, str):
            return (perms,)
        return perms

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()

        perms = self.get_required_permissions()
        if perms and not has_permission(request.user, *perms, match_all=self.match_all_permissions):
            log_audit_event(
                request=request,
                action='PERMISSION_DENIED',
                resource_type=self.__class__.__name__,
                resource_id=request.path,
                details={'required_permissions': list(perms), 'method': request.method},
                result='DENIED'
            )
            return render_access_denied(request, self.permission_denied_message)

        return super().dispatch(request, *args, **kwargs)


class HasGranularPermission(BasePermission):
    """
    DRF permission class for REST API endpoints.
    Maps standard DRF action names to granular module.action permission codenames.
    """
    action_permission_map = {
        'list': 'view',
        'retrieve': 'view',
        'create': 'create',
        'update': 'update',
        'partial_update': 'update',
        'destroy': 'delete',
    }

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True
        if not request.user.is_active:
            return False

        # If view specifies explicit required_permissions
        if hasattr(view, 'required_permissions'):
            perms = view.required_permissions
            if isinstance(perms, str):
                perms = (perms,)
            return has_permission(request.user, *perms)

        # Module-based mapping (e.g. view.module_name = 'orders')
        module_name = getattr(view, 'module_name', None)
        if not module_name:
            # Fallback to basename or model name
            basename = getattr(view, 'basename', '')
            if basename.startswith('api-'):
                basename = basename[4:]
            module_name = basename.replace('-', '_')

        action = getattr(view, 'action', None)
        action_suffix = self.action_permission_map.get(action, 'view')
        codename = f"{module_name}.{action_suffix}"

        return has_permission(request.user, codename)
