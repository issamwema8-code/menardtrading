from decimal import Decimal
from django.shortcuts import render, redirect
from django.views import View
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Sum

from apps.accounts.permissions import PermissionRequiredMixin, has_permission
from apps.customers.models import Customer
from apps.orders.models import PurchaseOrder
from apps.quotes.models import Quotation
from apps.logistics.models import LogisticsJob
from apps.billing.models import Invoice, PaymentReceipt


def get_dashboard_metrics(user=None):
    """
    Returns live operational and financial KPI metrics across all jobs and invoices.
    """
    total_orders_count = PurchaseOrder.objects.count() if (user is None or has_permission(user, 'orders.view')) else 0
    pending_quotes_count = Quotation.objects.filter(status__in=[Quotation.Status.DRAFT, Quotation.Status.SENT]).count() if (user is None or has_permission(user, 'quotations.view')) else 0
    active_jobs_count = LogisticsJob.objects.exclude(status=LogisticsJob.Status.CLOSED).count() if (user is None or has_permission(user, 'logistics.view')) else 0
    
    unpaid_invoices_balance = sum(
        (inv.balance_due for inv in Invoice.objects.exclude(status='CANCELLED')),
        Decimal('0.00')
    ) if (user is None or has_permission(user, 'invoices.view')) else Decimal('0.00')
    
    total_revenue_collected = sum(
        (rcp.amount_paid for rcp in PaymentReceipt.objects.all()),
        Decimal('0.00')
    ) if (user is None or has_permission(user, 'receipts.view')) else Decimal('0.00')

    return {
        'total_orders': total_orders_count,
        'pending_quotes': pending_quotes_count,
        'active_jobs': active_jobs_count,
        'unpaid_balance': unpaid_invoices_balance,
        'total_revenue': total_revenue_collected,
    }


class DashboardOverviewView(LoginRequiredMixin, View):
    """
    1. Overview Page: KPI metrics, pipeline workflow, quick dispatches feed, and recent billing.
    """
    def get(self, request):
        user = request.user
        orders = PurchaseOrder.objects.all().order_by('-created_at')[:10] if has_permission(user, 'orders.view') else []
        quotes = Quotation.objects.all().order_by('-created_at')[:10] if has_permission(user, 'quotations.view') else []
        jobs = LogisticsJob.objects.all().order_by('-created_at')[:10] if has_permission(user, 'logistics.view') else []
        invoices = Invoice.objects.all().order_by('-created_at')[:10] if has_permission(user, 'invoices.view') else []
        receipts = PaymentReceipt.objects.all().order_by('-payment_date')[:10] if has_permission(user, 'receipts.view') else []
        customers = Customer.objects.all().order_by('company_name')[:10] if has_permission(user, 'customers.view') else []

        context = {
            'active_tab': 'overview',
            'orders': orders,
            'quotes': quotes,
            'jobs': jobs,
            'invoices': invoices,
            'receipts': receipts,
            'customers': customers,
            'metrics': get_dashboard_metrics(user),
        }
        return render(request, 'dashboard/overview.html', context)


class InboundOrdersListView(PermissionRequiredMixin, View):
    """
    2. Inbound Purchase Orders Page: Mailbox sync, PDF upload, PO extraction table.
    """
    permission_required = 'orders.view'

    def get(self, request):
        orders = PurchaseOrder.objects.all().order_by('-created_at')
        customers = Customer.objects.all().order_by('company_name')
        context = {
            'active_tab': 'orders',
            'orders': orders,
            'customers': customers,
            'metrics': get_dashboard_metrics(request.user),
        }
        return render(request, 'orders/orders_list.html', context)


class QuotationsListView(PermissionRequiredMixin, View):
    """
    3. Quotations Desk Page: Draft, send via Brevo, 1-click approvals, and PDF downloads.
    """
    permission_required = 'quotations.view'

    def get(self, request):
        quotes = Quotation.objects.all().order_by('-created_at')
        customers = Customer.objects.all().order_by('company_name')
        context = {
            'active_tab': 'quotes',
            'quotes': quotes,
            'customers': customers,
            'metrics': get_dashboard_metrics(request.user),
        }
        return render(request, 'quotes/quotes_list.html', context)


class LogisticsJobsListView(PermissionRequiredMixin, View):
    """
    4. Logistics & Fleet Page: Freight consignments, fleet assignment, POD uploads, and milestone invoicing.
    """
    permission_required = 'logistics.view'

    def get(self, request):
        jobs = LogisticsJob.objects.all().order_by('-created_at')
        context = {
            'active_tab': 'logistics',
            'jobs': jobs,
            'metrics': get_dashboard_metrics(request.user),
        }
        return render(request, 'logistics/logistics_list.html', context)


class BillingInvoicesListView(PermissionRequiredMixin, View):
    """
    5. Billing & Invoices Page: Full, deposit, and add-on tax invoices, balance tracking, and payment recording.
    """
    permission_required = 'invoices.view'

    def get(self, request):
        invoices = Invoice.objects.all().order_by('-created_at')
        context = {
            'active_tab': 'billing',
            'invoices': invoices,
            'metrics': get_dashboard_metrics(request.user),
        }
        return render(request, 'billing/invoices_list.html', context)


class PaymentReceiptsListView(PermissionRequiredMixin, View):
    """
    6. Payment Receipts Page: Official settled receipts, payment methods, bank references, and PDF downloads.
    """
    permission_required = 'receipts.view'

    def get(self, request):
        receipts = PaymentReceipt.objects.all().order_by('-payment_date', '-created_at')
        context = {
            'active_tab': 'receipts',
            'receipts': receipts,
            'metrics': get_dashboard_metrics(request.user),
        }
        return render(request, 'billing/receipts_list.html', context)


class CustomersListView(PermissionRequiredMixin, View):
    """
    7. Customer Directory Page: Client accounts, billing terms, contact details, and new registrations.
    """
    permission_required = 'customers.view'

    def get(self, request):
        customers = Customer.objects.all().order_by('company_name')
        context = {
            'active_tab': 'customers',
            'customers': customers,
            'metrics': get_dashboard_metrics(request.user),
        }
        return render(request, 'customers/customers_list.html', context)


class SyncInboxView(PermissionRequiredMixin, View):
    """
    Connects to orders@menardtrading.com IMAP mailbox and pulls incoming POs.
    """
    permission_required = 'orders.sync'

    def post(self, request):
        from apps.orders.imap_service import sync_orders_mailbox
        result = sync_orders_mailbox()
        if result.get('status') == 'success':
            count = result.get('synced_count', 0)
            if count > 0:
                messages.success(request, f"Successfully synced {count} new Purchase Order(s) from orders@menardtrading.com mailbox!")
            else:
                messages.info(request, "Mailbox checked: No new incoming Purchase Orders found.")
        else:
            messages.error(request, f"Mailbox Sync Error: {result.get('error', 'Could not connect to IMAP server')}")
        return redirect(request.META.get('HTTP_REFERER', 'orders_list'))


class AutoPollInboxView(PermissionRequiredMixin, View):
    """
    Asynchronous polling endpoint called by frontend every 20-30s.
    Checks orders@menardtrading.com mailbox and returns JSON status.
    """
    permission_required = 'orders.view'

    def get(self, request):
        from django.http import JsonResponse
        from apps.orders.imap_service import sync_orders_mailbox
        try:
            result = sync_orders_mailbox()
            return JsonResponse(result)
        except Exception as e:
            return JsonResponse({'status': 'error', 'error': str(e), 'synced_count': 0})
