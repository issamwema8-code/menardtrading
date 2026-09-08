from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static

from django.http import HttpResponse
from django.views.generic import RedirectView
from menard_core.views import (
    DashboardOverviewView,
    InboundOrdersListView,
    QuotationsListView,
    LogisticsJobsListView,
    BillingInvoicesListView,
    PaymentReceiptsListView,
    CustomersListView,
    SyncInboxView,
    EmailQueueStatusView,
    MarkNotificationReadView,
)
from apps.orders.views import UploadPurchaseOrderView, PurchaseOrderDetailView, ReplyPurchaseOrderView
from apps.customers.views import (
    CreateCustomerView,
    CustomerSearchAPIView,
    QuickCreateCustomerView,
    CreatePaymentTermOptionView,
    PaymentTermOptionsAPIView,
)
from apps.quotes.views import (
    CustomerQuotePortalView,
    CustomerApproveQuoteView,
    SendQuoteEmailView,
    CreateQuotationView,
    QuotationPreviewView,
)
from apps.logistics.views import (
    UpdateJobStatusView,
    UploadPODView,
)
from apps.billing.views import (
    IssueInvoiceActionView,
    RecordPaymentActionView,
    InvoicePDFDownloadView,
    ReceiptPDFDownloadView,
    QuotationPDFDownloadView,
    InvoicePreviewView,
    ReceiptPreviewView,
    CreateDirectInvoiceView,
)
from apps.webhooks.views import BrevoInboundWebhookView

from apps.accounts.views import (
    LoginView,
    LogoutView,
    TwoFactorVerifyView,
    ResendOTPView,
    AccountSecuritySettingsView,
    SetPINView,
    ChangePINView,
    RemovePINView,
    AdminReset2FAPINView,
    ForgotPasswordView,
    ForgotPasswordDoneView,
    ResetPasswordConfirmView,
    ResetPasswordCompleteView,
    AccessDeniedView,
    UserManagementView,
    CreateUserView,
    UpdateUserView,
    ToggleUserActiveView,
    RoleManagementView,
    CreateRoleView,
    UpdateRoleView,
    DeleteRoleView,
    AuditLogListView,
    CompanySettingsView,
)

urlpatterns = [
    # Admin
    path('admin/', admin.site.urls),

    # Authentication & Two-Factor Verification
    path('login/', LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('verify-2fa/', TwoFactorVerifyView.as_view(), name='verify_2fa'),
    path('verify-2fa/resend/', ResendOTPView.as_view(), name='resend_2fa_otp'),
    path('account/security/', AccountSecuritySettingsView.as_view(), name='account_security'),
    path('account/security/set-pin/', SetPINView.as_view(), name='account_security_set_pin'),
    path('account/security/change-pin/', ChangePINView.as_view(), name='account_security_change_pin'),
    path('account/security/remove-pin/', RemovePINView.as_view(), name='account_security_remove_pin'),
    path('forgot-password/', ForgotPasswordView.as_view(), name='forgot_password'),
    path('forgot-password/done/', ForgotPasswordDoneView.as_view(), name='forgot_password_done'),
    path('reset-password/<str:uidb64>/<str:token>/', ResetPasswordConfirmView.as_view(), name='reset_password_confirm'),
    path('reset-password/complete/', ResetPasswordCompleteView.as_view(), name='reset_password_complete'),
    path('access-denied/', AccessDeniedView.as_view(), name='access_denied'),

    # Administration Module
    path('administration/company-settings/', CompanySettingsView.as_view(), name='administration-company-settings'),
    path('administration/users/', UserManagementView.as_view(), name='administration-users'),
    path('administration/users/create/', CreateUserView.as_view(), name='administration-users-create'),
    path('administration/users/<int:pk>/update/', UpdateUserView.as_view(), name='administration-users-update'),
    path('administration/users/<int:pk>/toggle-active/', ToggleUserActiveView.as_view(), name='administration-users-toggle-active'),
    path('administration/users/<int:pk>/reset-2fa-pin/', AdminReset2FAPINView.as_view(), name='admin_reset_user_2fa_pin'),
    path('administration/roles/', RoleManagementView.as_view(), name='administration-roles'),
    path('administration/roles/create/', CreateRoleView.as_view(), name='administration-roles-create'),
    path('administration/roles/<int:pk>/update/', UpdateRoleView.as_view(), name='administration-roles-update'),
    path('administration/roles/<int:pk>/delete/', DeleteRoleView.as_view(), name='administration-roles-delete'),
    path('administration/audit-logs/', AuditLogListView.as_view(), name='administration-audit-logs'),


    # 1. Overview Page & Reserved Website Root
    path('', RedirectView.as_view(url='/overview/', permanent=False), name='website_root'),
    path('dashboard/', RedirectView.as_view(url='/overview/', permanent=False), name='dashboard_index'),
    path('overview/', DashboardOverviewView.as_view(), name='dashboard_overview'),

    # 2. Inbound Orders Page & Actions
    path('orders/', InboundOrdersListView.as_view(), name='orders_list'),
    path('orders/upload/', UploadPurchaseOrderView.as_view(), name='upload_purchase_order'),
    path('orders/<int:pk>/', PurchaseOrderDetailView.as_view(), name='purchase_order_detail'),
    path('orders/<int:pk>/reply/', ReplyPurchaseOrderView.as_view(), name='reply_purchase_order'),

    # 3. Quotations Page & Actions
    path('quotes/', QuotationsListView.as_view(), name='quotes_list'),
    path('quotes/create/', CreateQuotationView.as_view(), name='create_quotation'),
    path('quotes/<int:pk>/preview/', QuotationPreviewView.as_view(), name='quote_preview'),
    path('quotes/<int:pk>/send/', SendQuoteEmailView.as_view(), name='send_quote_email'),
    path('quotes/<int:pk>/pdf/', QuotationPDFDownloadView.as_view(), name='quote_pdf_download'),
    path('portal/quotes/<uuid:token>/', CustomerQuotePortalView.as_view(), name='quote_portal'),
    path('portal/quotes/<uuid:token>/approve/', CustomerApproveQuoteView.as_view(), name='quote_approve'),

    # 4. Logistics & Fleet Page & Actions
    path('logistics/', LogisticsJobsListView.as_view(), name='logistics_list'),
    path('jobs/<int:pk>/status/', UpdateJobStatusView.as_view(), name='update_job_status'),
    path('jobs/<int:pk>/pod/', UploadPODView.as_view(), name='upload_pod'),

    # 5. Billing & Invoices Page & Actions
    path('billing/', BillingInvoicesListView.as_view(), name='billing_list'),
    path('invoices/', BillingInvoicesListView.as_view(), name='invoices_list'),
    path('invoices/create/', CreateDirectInvoiceView.as_view(), name='create_invoice'),
    path('invoices/<int:pk>/preview/', InvoicePreviewView.as_view(), name='invoice_preview'),
    path('jobs/<int:job_id>/issue-invoice/', IssueInvoiceActionView.as_view(), name='issue_invoice_action'),
    path('invoices/<int:invoice_id>/record-payment/', RecordPaymentActionView.as_view(), name='record_payment_action'),
    path('invoices/<int:pk>/pdf/', InvoicePDFDownloadView.as_view(), name='invoice_pdf_download'),

    # 6. Payment Receipts Page & PDF
    path('receipts/', PaymentReceiptsListView.as_view(), name='receipts_list'),
    path('receipts/<int:pk>/preview/', ReceiptPreviewView.as_view(), name='receipt_preview'),
    path('receipts/<int:pk>/pdf/', ReceiptPDFDownloadView.as_view(), name='receipt_pdf_download'),

    # 7. Customer Directory Page & Actions
    path('customers/', CustomersListView.as_view(), name='customers_list'),
    path('customers/create/', CreateCustomerView.as_view(), name='create_customer'),
    path('customers/api/search/', CustomerSearchAPIView.as_view(), name='customer_search_api'),
    path('customers/api/quick-create/', QuickCreateCustomerView.as_view(), name='quick_create_customer'),
    path('customers/api/payment-terms/', PaymentTermOptionsAPIView.as_view(), name='payment_term_options_api'),
    path('customers/api/payment-terms/create/', CreatePaymentTermOptionView.as_view(), name='create_payment_term_option'),

    # System Utilities & Email Queue Monitoring
    path('system/sync-inbox/', SyncInboxView.as_view(), name='sync_inbox'),
    path('system/email-queue-status/', EmailQueueStatusView.as_view(), name='email_queue_status'),
    path('system/notifications/<int:pk>/read/', MarkNotificationReadView.as_view(), name='mark_notification_read'),
    path('service-worker.js', lambda r: HttpResponse('// Menard Trading CC Service Worker\nself.addEventListener("install", () => self.skipWaiting());', content_type='application/javascript'), name='service_worker'),

    # Brevo Inbound Webhook Listener
    path('api/webhooks/brevo/', BrevoInboundWebhookView.as_view(), name='brevo_inbound_webhook'),

    # Accounting, Financial Management, Reports & Analytics
    path('', include('apps.accounting.urls', namespace='accounting')),

    # Django REST Framework API Layer for Mobile & Webhooks
    path('api/v1/', include('apps.api.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
