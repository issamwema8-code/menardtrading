from rest_framework import viewsets, status
from rest_framework.views import APIView
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.authtoken.models import Token
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User

from apps.accounts.permissions import HasGranularPermission, get_user_permissions
from apps.accounts.audit import log_audit_event
from apps.customers.models import Customer
from apps.orders.models import PurchaseOrder
from apps.quotes.models import Quotation
from apps.logistics.models import LogisticsJob
from apps.billing.models import Invoice, PaymentReceipt
from apps.orders.services import process_inbound_purchase_order

from .serializers import (
    CustomerSerializer,
    PurchaseOrderSerializer,
    QuotationSerializer,
    LogisticsJobSerializer,
    InvoiceSerializer,
    PaymentReceiptSerializer,
)


class AuthLoginAPIView(APIView):
    """
    POST /api/v1/auth/login/
    Accepts email/username and password. Returns Token, User details, Roles, and Permissions.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        credential = request.data.get('username') or request.data.get('email')
        password = request.data.get('password')

        if not credential or not password:
            return Response(
                {'error': 'Both username/email and password are required.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Lookup user by username or email
        user_obj = User.objects.filter(username__iexact=credential).first()
        if not user_obj:
            user_obj = User.objects.filter(email__iexact=credential).first()

        username = user_obj.username if user_obj else credential
        user = authenticate(request, username=username, password=password)

        if not user:
            log_audit_event(
                request=request,
                user=user_obj,
                user_email=credential,
                action='API_LOGIN_FAILED',
                resource_type='Auth API',
                details={'reason': 'Invalid credentials'},
                result='FAILURE'
            )
            return Response(
                {'error': 'Invalid email/username or password.'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        if not user.is_active:
            return Response(
                {'error': 'This user account is inactive. Please contact your administrator.'},
                status=status.HTTP_403_FORBIDDEN
            )

        token, _ = Token.objects.get_or_create(user=user)
        permissions = sorted(list(get_user_permissions(user)))
        roles = []
        if hasattr(user, 'profile'):
            roles = [r.name for r in user.profile.roles.filter(is_active=True)]

        log_audit_event(
            request=request,
            user=user,
            action='API_LOGIN_SUCCESS',
            resource_type='Auth API',
            details={'roles': roles, 'token_issued': True},
            result='SUCCESS'
        )

        return Response({
            'token': token.key,
            'user': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'is_superuser': user.is_superuser,
                'roles': roles,
                'permissions': permissions,
            }
        })


class AuthCurrentUserAPIView(APIView):
    """
    GET /api/v1/auth/me/
    Returns current authenticated user details, assigned roles, and effective permissions.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        permissions = sorted(list(get_user_permissions(user)))
        roles = []
        if hasattr(user, 'profile'):
            roles = [r.name for r in user.profile.roles.filter(is_active=True)]

        return Response({
            'user': {
                'id': user.id,
                'username': user.username,
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'is_superuser': user.is_superuser,
                'roles': roles,
                'permissions': permissions,
            }
        })


class AuthLogoutAPIView(APIView):
    """
    POST /api/v1/auth/logout/
    Invalidates the auth token and terminates the session.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        Token.objects.filter(user=request.user).delete()
        logout(request)
        return Response({'status': 'success', 'message': 'Logged out successfully.'})


class CustomerViewSet(viewsets.ModelViewSet):
    queryset = Customer.objects.all()
    serializer_class = CustomerSerializer
    permission_classes = [HasGranularPermission]
    permission_module = 'customers'
    search_fields = ['company_name', 'contact_name', 'email', 'phone']


class PurchaseOrderViewSet(viewsets.ModelViewSet):
    queryset = PurchaseOrder.objects.all()
    serializer_class = PurchaseOrderSerializer
    permission_classes = [HasGranularPermission]
    permission_module = 'orders'

    @action(detail=True, methods=['post'], permission_classes=[HasGranularPermission])
    def parse_now(self, request, pk=None):
        po = self.get_object()
        quote = process_inbound_purchase_order(po)
        return Response({
            'status': 'success',
            'po_status': po.status,
            'quote_id': quote.id,
            'quote_number': quote.quote_number,
        })


class QuotationViewSet(viewsets.ModelViewSet):
    queryset = Quotation.objects.all()
    serializer_class = QuotationSerializer
    permission_classes = [HasGranularPermission]
    permission_module = 'quotations'


class LogisticsJobViewSet(viewsets.ModelViewSet):
    queryset = LogisticsJob.objects.all()
    serializer_class = LogisticsJobSerializer
    permission_classes = [HasGranularPermission]
    permission_module = 'logistics'

    @action(detail=True, methods=['post'], permission_classes=[HasGranularPermission])
    def upload_pod(self, request, pk=None):
        job = self.get_object()
        pod_file = request.FILES.get('pod_document')
        if not pod_file:
            return Response({'error': 'No POD file provided'}, status=status.HTTP_400_BAD_REQUEST)
        job.pod_document = pod_file
        job.status = LogisticsJob.Status.POD_RECEIVED
        job.save()
        return Response({'status': 'success', 'job_status': job.status})


class InvoiceViewSet(viewsets.ModelViewSet):
    queryset = Invoice.objects.all()
    serializer_class = InvoiceSerializer
    permission_classes = [HasGranularPermission]
    permission_module = 'invoices'


class PaymentReceiptViewSet(viewsets.ModelViewSet):
    queryset = PaymentReceipt.objects.all()
    serializer_class = PaymentReceiptSerializer
    permission_classes = [HasGranularPermission]
    permission_module = 'receipts'
