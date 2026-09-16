from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    AuthLoginAPIView,
    AuthCurrentUserAPIView,
    AuthLogoutAPIView,
    CustomerViewSet,
    PurchaseOrderViewSet,
    QuotationViewSet,
    LogisticsJobViewSet,
    InvoiceViewSet,
    PaymentReceiptViewSet,
)

router = DefaultRouter()
router.register(r'customers', CustomerViewSet, basename='api-customers')
router.register(r'purchase-orders', PurchaseOrderViewSet, basename='api-purchase-orders')
router.register(r'quotations', QuotationViewSet, basename='api-quotations')
router.register(r'jobs', LogisticsJobViewSet, basename='api-jobs')
router.register(r'invoices', InvoiceViewSet, basename='api-invoices')
router.register(r'receipts', PaymentReceiptViewSet, basename='api-receipts')

urlpatterns = [
    # Authentication Endpoints
    path('auth/login/', AuthLoginAPIView.as_view(), name='api-auth-login'),
    path('auth/me/', AuthCurrentUserAPIView.as_view(), name='api-auth-me'),
    path('auth/logout/', AuthLogoutAPIView.as_view(), name='api-auth-logout'),

    # REST Resource Endpoints
    path('', include(router.urls)),
]
