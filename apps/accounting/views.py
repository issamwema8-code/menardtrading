import csv
from decimal import Decimal
from datetime import date, timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from django.http import HttpResponse, JsonResponse
from django.contrib import messages
from django.utils import timezone
from django.db.models import Sum, Q

from apps.accounts.permissions import PermissionRequiredMixin
from apps.accounts.audit import log_audit_event
from apps.customers.models import Customer
from apps.billing.models import Invoice, PaymentReceipt
from apps.orders.models import PurchaseOrder
from apps.quotes.models import Quotation
from apps.logistics.models import LogisticsJob
from .models import ExpenseCategory, Vendor, Expense, SupplierBill, SupplierBillPayment
from .services import (
    get_ar_summary,
    get_ap_summary,
    get_profit_and_loss,
    get_balance_sheet,
    get_cash_flow,
    get_vat_report,
    get_customer_statement,
    get_financial_dashboard_kpis,
    get_financial_analytics,
    parse_date_range,
)


# ============================================================
# 1. FINANCIAL DASHBOARD (ACCOUNTS OVERVIEW)
# ============================================================

class FinancialOverviewView(PermissionRequiredMixin, View):
    required_permissions = ('accounts.view',)

    def get(self, request):
        kpis = get_financial_dashboard_kpis()
        analytics = get_financial_analytics(period='year')
        recent_expenses = Expense.objects.select_related('category', 'vendor').order_by('-date', '-created_at')[:8]
        recent_receipts = PaymentReceipt.objects.select_related('customer', 'invoice').order_by('-payment_date', '-created_at')[:8]

        return render(request, 'accounting/overview.html', {
            'active_tab': 'accounts',
            'sub_tab': 'overview',
            'kpis': kpis,
            'analytics': analytics,
            'recent_expenses': recent_expenses,
            'recent_receipts': recent_receipts,
        })


# ============================================================
# 2. EXPENSES MODULE VIEWS
# ============================================================

class ExpensesListView(PermissionRequiredMixin, View):
    required_permissions = ('expenses.view',)

    def get(self, request):
        category_id = request.GET.get('category')
        status_filter = request.GET.get('status')
        start_date_str = request.GET.get('start_date')
        end_date_str = request.GET.get('end_date')
        search_query = request.GET.get('search', '').strip()

        start_date, end_date = parse_date_range(start_date_str, end_date_str, default_days=90)

        expenses_qs = Expense.objects.select_related('category', 'vendor', 'created_by').filter(
            date__gte=start_date,
            date__lte=end_date
        )

        if category_id:
            expenses_qs = expenses_qs.filter(category_id=category_id)
        if status_filter:
            expenses_qs = expenses_qs.filter(status=status_filter)
        if search_query:
            expenses_qs = expenses_qs.filter(
                Q(expense_number__icontains=search_query) |
                Q(payee__icontains=search_query) |
                Q(description__icontains=search_query) |
                Q(vendor__name__icontains=search_query)
            )

        expenses = expenses_qs.order_by('-date', '-created_at')

        # Totals
        total_spent = expenses.exclude(status=Expense.Status.VOID).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
        total_subtotal = expenses.exclude(status=Expense.Status.VOID).aggregate(total=Sum('subtotal'))['total'] or Decimal('0.00')
        total_vat = expenses.exclude(status=Expense.Status.VOID).aggregate(total=Sum('vat_amount'))['total'] or Decimal('0.00')

        categories = ExpenseCategory.objects.filter(is_active=True).order_by('name')
        vendors = Vendor.objects.filter(is_active=True).order_by('name')

        return render(request, 'accounting/expenses_list.html', {
            'active_tab': 'accounts',
            'sub_tab': 'expenses',
            'expenses': expenses,
            'categories': categories,
            'vendors': vendors,
            'total_spent': total_spent,
            'total_subtotal': total_subtotal,
            'total_vat': total_vat,
            'start_date': start_date,
            'end_date': end_date,
            'selected_category': category_id,
            'selected_status': status_filter,
            'search_query': search_query,
        })


class CreateExpenseView(PermissionRequiredMixin, View):
    required_permissions = ('expenses.create',)

    def post(self, request):
        category_id = request.POST.get('category')
        vendor_id = request.POST.get('vendor')
        payee = request.POST.get('payee', '').strip()
        expense_date_str = request.POST.get('date')
        description = request.POST.get('description', '').strip()
        subtotal_str = request.POST.get('subtotal', '0.00').strip()
        vat_rate_str = request.POST.get('vat_rate', '15.00').strip()
        payment_method = request.POST.get('payment_method', Expense.PaymentMethod.EFT)
        receipt_file = request.FILES.get('receipt_file')
        notes = request.POST.get('notes', '').strip()

        if not category_id or not description or not subtotal_str:
            messages.error(request, "Category, description, and subtotal amount are required.")
            return redirect('accounting-expenses')

        category = get_object_or_404(ExpenseCategory, pk=category_id)
        vendor = Vendor.objects.filter(pk=vendor_id).first() if vendor_id else None

        try:
            subtotal = Decimal(subtotal_str)
            vat_rate = Decimal(vat_rate_str)
        except Exception:
            messages.error(request, "Invalid numeric format for expense amounts.")
            return redirect('accounting-expenses')

        expense_date = date.fromisoformat(expense_date_str) if expense_date_str else timezone.now().date()

        expense = Expense.objects.create(
            category=category,
            vendor=vendor,
            payee=payee or (vendor.name if vendor else ''),
            date=expense_date,
            description=description,
            subtotal=subtotal,
            vat_rate=vat_rate,
            payment_method=payment_method,
            status=Expense.Status.PAID,
            receipt_file=receipt_file,
            notes=notes,
            created_by=request.user
        )

        log_audit_event(
            request=request,
            action='EXPENSE_CREATED',
            resource_type='Expense',
            resource_id=expense.expense_number,
            details={
                'category': category.name,
                'total_amount': str(expense.total_amount),
                'payee': expense.payee
            }
        )

        messages.success(request, f"Expense '{expense.expense_number}' (R{expense.total_amount}) recorded successfully.")
        return redirect('accounting-expenses')


class VoidExpenseView(PermissionRequiredMixin, View):
    required_permissions = ('expenses.delete',)

    def post(self, request, pk):
        expense = get_object_or_404(Expense, pk=pk)
        if expense.status == Expense.Status.VOID:
            messages.warning(request, "This expense is already marked as void.")
            return redirect('accounting-expenses')

        expense.status = Expense.Status.VOID
        expense.save(update_fields=['status', 'updated_at'])

        log_audit_event(
            request=request,
            action='EXPENSE_VOIDED',
            resource_type='Expense',
            resource_id=expense.expense_number,
            details={'amount': str(expense.total_amount)}
        )

        messages.success(request, f"Expense '{expense.expense_number}' has been voided.")
        return redirect('accounting-expenses')


class ExpenseCategoriesView(PermissionRequiredMixin, View):
    required_permissions = ('expenses.update',)

    def get(self, request):
        categories = ExpenseCategory.objects.annotate(
            total_spend=Sum('expenses__total_amount')
        ).order_by('name')
        return render(request, 'accounting/categories_list.html', {
            'active_tab': 'accounts',
            'sub_tab': 'categories',
            'categories': categories,
        })

    def post(self, request):
        name = request.POST.get('name', '').strip()
        code = request.POST.get('code', '').strip()
        description = request.POST.get('description', '').strip()

        if not name:
            messages.error(request, "Category name is required.")
            return redirect('accounting-categories')

        if ExpenseCategory.objects.filter(name__iexact=name).exists():
            messages.error(request, f"A category named '{name}' already exists.")
            return redirect('accounting-categories')

        cat = ExpenseCategory.objects.create(
            name=name,
            code=code,
            description=description,
            is_active=True,
            is_system=False
        )
        messages.success(request, f"Expense category '{cat.name}' created.")
        return redirect('accounting-categories')


# ============================================================
# 3. ACCOUNTS RECEIVABLE (AR) VIEW
# ============================================================

class AccountsReceivableView(PermissionRequiredMixin, View):
    required_permissions = ('accounts.receivables.view',)

    def get(self, request):
        ar_data = get_ar_summary()
        customer_id = request.GET.get('customer')
        selected_customer = None
        customer_invoices = []

        if customer_id:
            selected_customer = Customer.objects.filter(pk=customer_id).first()
            if selected_customer:
                customer_invoices = Invoice.objects.filter(
                    customer=selected_customer,
                    status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.OVERDUE]
                ).order_by('due_date')

        return render(request, 'accounting/receivables.html', {
            'active_tab': 'accounts',
            'sub_tab': 'receivables',
            'ar': ar_data,
            'selected_customer': selected_customer,
            'customer_invoices': customer_invoices,
        })


# ============================================================
# 4. ACCOUNTS PAYABLE (AP) & SUPPLIER BILLS VIEWS
# ============================================================

class AccountsPayableView(PermissionRequiredMixin, View):
    required_permissions = ('accounts.payables.view',)

    def get(self, request):
        ap_data = get_ap_summary()
        vendors = Vendor.objects.filter(is_active=True).order_by('name')
        return render(request, 'accounting/payables.html', {
            'active_tab': 'accounts',
            'sub_tab': 'payables',
            'ap': ap_data,
            'vendors': vendors,
        })


class CreateSupplierBillView(PermissionRequiredMixin, View):
    required_permissions = ('accounts.payables.create',)

    def post(self, request):
        vendor_id = request.POST.get('vendor')
        supplier_ref = request.POST.get('supplier_reference', '').strip()
        issue_date_str = request.POST.get('issue_date')
        due_date_str = request.POST.get('due_date')
        subtotal_str = request.POST.get('subtotal', '0.00').strip()
        vat_rate_str = request.POST.get('vat_rate', '15.00').strip()
        notes = request.POST.get('notes', '').strip()
        bill_pdf = request.FILES.get('bill_pdf')

        vendor = get_object_or_404(Vendor, pk=vendor_id)

        try:
            subtotal = Decimal(subtotal_str)
            vat_rate = Decimal(vat_rate_str)
            issue_date = date.fromisoformat(issue_date_str) if issue_date_str else timezone.now().date()
            due_date = date.fromisoformat(due_date_str) if due_date_str else issue_date + timedelta(days=30)
        except Exception:
            messages.error(request, "Invalid dates or amount formats.")
            return redirect('accounting-payables')

        vat_amount = (subtotal * (vat_rate / Decimal('100.00'))).quantize(Decimal('0.01'))
        total_amount = subtotal + vat_amount

        bill = SupplierBill.objects.create(
            vendor=vendor,
            supplier_reference=supplier_ref,
            issue_date=issue_date,
            due_date=due_date,
            subtotal=subtotal,
            vat_rate=vat_rate,
            vat_amount=vat_amount,
            total_amount=total_amount,
            amount_paid=Decimal('0.00'),
            balance_due=total_amount,
            status=SupplierBill.Status.ISSUED,
            notes=notes,
            bill_pdf=bill_pdf,
            created_by=request.user
        )

        messages.success(request, f"Supplier Bill '{bill.bill_number}' for {vendor.name} (R{bill.total_amount}) created.")
        return redirect('accounting-payables')


class RecordBillPaymentView(PermissionRequiredMixin, View):
    required_permissions = ('accounts.payables.create',)

    def post(self, request, pk):
        bill = get_object_or_404(SupplierBill, pk=pk)
        amount_str = request.POST.get('amount_paid', '').strip()
        payment_date_str = request.POST.get('payment_date')
        payment_method = request.POST.get('payment_method', SupplierBillPayment.PaymentMethod.EFT)
        tx_ref = request.POST.get('transaction_reference', '').strip()
        notes = request.POST.get('notes', '').strip()

        try:
            amount = Decimal(amount_str)
            if amount <= 0 or amount > bill.balance_due:
                messages.error(request, f"Payment must be between R0.01 and the remaining balance of R{bill.balance_due}.")
                return redirect('accounting-payables')
            payment_date = date.fromisoformat(payment_date_str) if payment_date_str else timezone.now().date()
        except Exception:
            messages.error(request, "Invalid payment amount or date.")
            return redirect('accounting-payables')

        payment = SupplierBillPayment.objects.create(
            bill=bill,
            amount_paid=amount,
            payment_date=payment_date,
            payment_method=payment_method,
            transaction_reference=tx_ref,
            notes=notes,
            created_by=request.user
        )

        messages.success(request, f"Payment of R{payment.amount_paid} recorded for Bill '{bill.bill_number}'.")
        return redirect('accounting-payables')


# ============================================================
# 5. PROFIT & LOSS STATEMENT VIEW
# ============================================================

class ProfitLossView(PermissionRequiredMixin, View):
    required_permissions = ('accounts.profit_loss.view',)

    def get(self, request):
        start_date_str = request.GET.get('start_date')
        end_date_str = request.GET.get('end_date')
        preset = request.GET.get('preset', 'ytd')

        today = timezone.now().date()
        if preset == 'mtd':
            start_date = today.replace(day=1)
            end_date = today
        elif preset == 'qtd':
            curr_quarter_month = 3 * ((today.month - 1) // 3) + 1
            start_date = today.replace(month=curr_quarter_month, day=1)
            end_date = today
        elif preset == 'ytd':
            start_date = today.replace(month=1, day=1)
            end_date = today
        elif preset == 'last_year':
            start_date = date(today.year - 1, 1, 1)
            end_date = date(today.year - 1, 12, 31)
        else:
            start_date, end_date = parse_date_range(start_date_str, end_date_str, default_days=365)

        pnl = get_profit_and_loss(start_date=start_date, end_date=end_date)

        return render(request, 'accounting/profit_loss.html', {
            'active_tab': 'accounts',
            'sub_tab': 'profit_loss',
            'pnl': pnl,
            'preset': preset,
            'start_date': start_date,
            'end_date': end_date,
        })


# ============================================================
# 6. BALANCE SHEET VIEW
# ============================================================

class BalanceSheetView(PermissionRequiredMixin, View):
    required_permissions = ('accounts.balance_sheet.view',)

    def get(self, request):
        as_of_date_str = request.GET.get('as_of_date')
        as_of_date = date.fromisoformat(as_of_date_str) if as_of_date_str else timezone.now().date()
        balance_sheet = get_balance_sheet(as_of_date=as_of_date)

        return render(request, 'accounting/balance_sheet.html', {
            'active_tab': 'accounts',
            'sub_tab': 'balance_sheet',
            'bs': balance_sheet,
            'as_of_date': as_of_date,
        })


# ============================================================
# 7. CASH FLOW STATEMENT VIEW
# ============================================================

class CashFlowView(PermissionRequiredMixin, View):
    required_permissions = ('accounts.cash_flow.view',)

    def get(self, request):
        start_date_str = request.GET.get('start_date')
        end_date_str = request.GET.get('end_date')
        start_date, end_date = parse_date_range(start_date_str, end_date_str, default_days=90)
        cash_flow = get_cash_flow(start_date=start_date, end_date=end_date)

        return render(request, 'accounting/cash_flow.html', {
            'active_tab': 'accounts',
            'sub_tab': 'cash_flow',
            'cf': cash_flow,
            'start_date': start_date,
            'end_date': end_date,
        })


# ============================================================
# 8. VAT / TAX REPORT VIEW
# ============================================================

class VATReportView(PermissionRequiredMixin, View):
    required_permissions = ('accounts.vat.view',)

    def get(self, request):
        start_date_str = request.GET.get('start_date')
        end_date_str = request.GET.get('end_date')
        start_date, end_date = parse_date_range(start_date_str, end_date_str, default_days=60)
        vat_data = get_vat_report(start_date=start_date, end_date=end_date)

        return render(request, 'accounting/vat_report.html', {
            'active_tab': 'accounts',
            'sub_tab': 'vat',
            'vat': vat_data,
            'start_date': start_date,
            'end_date': end_date,
        })


# ============================================================
# 9. CUSTOMER ACCOUNT STATEMENTS
# ============================================================

class CustomerStatementsView(PermissionRequiredMixin, View):
    required_permissions = ('accounts.statements.view',)

    def get(self, request):
        customers = Customer.objects.filter(is_active=True).prefetch_related('invoices', 'payment_receipts').order_by('company_name')
        customer_cards = []
        for c in customers:
            total_inv = sum((i.total_amount for i in c.invoices.exclude(status=Invoice.Status.CANCELLED)), Decimal('0.00'))
            total_paid = sum((p.amount_paid for p in c.payment_receipts.all()), Decimal('0.00'))
            outstanding = total_inv - total_paid
            customer_cards.append({
                'customer': c,
                'total_invoiced': total_inv,
                'total_paid': total_paid,
                'outstanding': outstanding,
                'unpaid_invoices_count': c.invoices.filter(status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.OVERDUE]).count()
            })

        return render(request, 'accounting/customer_statements.html', {
            'active_tab': 'accounts',
            'sub_tab': 'statements',
            'customer_cards': customer_cards,
        })


class CustomerStatementDetailView(PermissionRequiredMixin, View):
    required_permissions = ('accounts.statements.view',)

    def get(self, request, customer_id):
        customer = get_object_or_404(Customer, pk=customer_id)
        start_date_str = request.GET.get('start_date')
        end_date_str = request.GET.get('end_date')
        start_date, end_date = parse_date_range(start_date_str, end_date_str, default_days=60)

        statement = get_customer_statement(customer, start_date=start_date, end_date=end_date)

        return render(request, 'accounting/customer_statement_detail.html', {
            'active_tab': 'accounts',
            'sub_tab': 'statements',
            'statement': statement,
            'customer': customer,
            'start_date': start_date,
            'end_date': end_date,
        })


class CustomerStatementPDFDownloadView(PermissionRequiredMixin, View):
    required_permissions = ('accounts.statements.view',)

    def get(self, request, customer_id):
        customer = get_object_or_404(Customer, pk=customer_id)
        start_date_str = request.GET.get('start_date')
        end_date_str = request.GET.get('end_date')
        start_date, end_date = parse_date_range(start_date_str, end_date_str, default_days=60)

        statement = get_customer_statement(customer, start_date=start_date, end_date=end_date)

        # PDF rendering using HTML template
        return render(request, 'accounting/statement_pdf.html', {
            'statement': statement,
            'customer': customer,
            'start_date': start_date,
            'end_date': end_date,
        })


# ============================================================
# 10. REPORTS HUB & EXPORTS
# ============================================================

class ReportsHubView(PermissionRequiredMixin, View):
    required_permissions = ('reports.view',)

    def get(self, request):
        report_type = request.GET.get('report', 'sales')
        start_date_str = request.GET.get('start_date')
        end_date_str = request.GET.get('end_date')
        start_date, end_date = parse_date_range(start_date_str, end_date_str, default_days=90)

        data = {}
        if report_type == 'sales':
            data['invoices'] = Invoice.objects.filter(
                issue_date__gte=start_date,
                issue_date__lte=end_date
            ).exclude(status=Invoice.Status.CANCELLED).select_related('customer').order_by('-issue_date')
            data['total_sales'] = data['invoices'].aggregate(total=Sum('subtotal'))['total'] or Decimal('0.00')
            data['total_vat'] = data['invoices'].aggregate(total=Sum('vat_amount'))['total'] or Decimal('0.00')
            data['total_gross'] = data['invoices'].aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')

        elif report_type == 'payments':
            data['payments'] = PaymentReceipt.objects.filter(
                payment_date__gte=start_date,
                payment_date__lte=end_date
            ).select_related('customer', 'invoice').order_by('-payment_date')
            data['total_collected'] = data['payments'].aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')

        elif report_type == 'ar':
            data['ar'] = get_ar_summary()

        elif report_type == 'ap':
            data['ap'] = get_ap_summary()

        elif report_type == 'expenses':
            data['expenses'] = Expense.objects.filter(
                date__gte=start_date,
                date__lte=end_date
            ).exclude(status=Expense.Status.VOID).select_related('category', 'vendor').order_by('-date')
            data['total_expenses'] = data['expenses'].aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')

        elif report_type == 'pnl':
            data['pnl'] = get_profit_and_loss(start_date=start_date, end_date=end_date)

        elif report_type == 'vat':
            data['vat'] = get_vat_report(start_date=start_date, end_date=end_date)

        elif report_type == 'operations':
            data['orders_count'] = PurchaseOrder.objects.filter(created_at__date__gte=start_date, created_at__date__lte=end_date).count()
            data['quotes_count'] = Quotation.objects.filter(created_at__date__gte=start_date, created_at__date__lte=end_date).count()
            data['jobs_count'] = LogisticsJob.objects.filter(created_at__date__gte=start_date, created_at__date__lte=end_date).count()
            data['invoices_count'] = Invoice.objects.filter(created_at__date__gte=start_date, created_at__date__lte=end_date).count()

        return render(request, 'accounting/reports_hub.html', {
            'active_tab': 'reports',
            'report_type': report_type,
            'start_date': start_date,
            'end_date': end_date,
            'data': data,
        })


class ExportReportCSVView(PermissionRequiredMixin, View):
    required_permissions = ('reports.export',)

    def get(self, request):
        report_type = request.GET.get('report', 'sales')
        start_date_str = request.GET.get('start_date')
        end_date_str = request.GET.get('end_date')
        start_date, end_date = parse_date_range(start_date_str, end_date_str, default_days=90)

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="menard_{report_type}_report_{start_date}_{end_date}.csv"'
        writer = csv.writer(response)

        if report_type == 'sales':
            writer.writerow(['Invoice Number', 'Issue Date', 'Customer', 'Type', 'Status', 'Subtotal (Excl VAT)', 'VAT (15%)', 'Total (Incl VAT)', 'Amount Paid', 'Balance Due'])
            for inv in Invoice.objects.filter(issue_date__gte=start_date, issue_date__lte=end_date).exclude(status=Invoice.Status.CANCELLED).order_by('-issue_date'):
                writer.writerow([inv.invoice_number, inv.issue_date, inv.customer.company_name, inv.get_invoice_type_display(), inv.get_status_display(), inv.subtotal, inv.vat_amount, inv.total_amount, inv.amount_paid, inv.balance_due])

        elif report_type == 'payments':
            writer.writerow(['Receipt Number', 'Payment Date', 'Customer', 'Invoice Number', 'Payment Method', 'Reference', 'Amount Paid'])
            for rcp in PaymentReceipt.objects.filter(payment_date__gte=start_date, payment_date__lte=end_date).order_by('-payment_date'):
                writer.writerow([rcp.receipt_number, rcp.payment_date, rcp.customer.company_name, rcp.invoice.invoice_number, rcp.get_payment_method_display(), rcp.transaction_reference, rcp.amount_paid])

        elif report_type == 'expenses':
            writer.writerow(['Expense Number', 'Date', 'Category', 'Vendor/Payee', 'Description', 'Payment Method', 'Status', 'Subtotal', 'VAT Amount', 'Total Amount'])
            for exp in Expense.objects.filter(date__gte=start_date, date__lte=end_date).order_by('-date'):
                writer.writerow([exp.expense_number, exp.date, exp.category.name, exp.payee, exp.description, exp.get_payment_method_display(), exp.get_status_display(), exp.subtotal, exp.vat_amount, exp.total_amount])

        elif report_type == 'ar':
            ar = get_ar_summary()
            writer.writerow(['Customer', 'Total Balance Due', 'Current', 'Overdue', 'Unpaid Invoices Count'])
            for cb in ar['customer_balances']:
                writer.writerow([cb['customer'].company_name, cb['total_due'], cb['current_due'], cb['overdue_due'], cb['invoices_count']])

        return response


# ============================================================
# 11. ANALYTICS HUB VIEW
# ============================================================

class AnalyticsHubView(PermissionRequiredMixin, View):
    required_permissions = ('analytics.view',)

    def get(self, request):
        period = request.GET.get('period', 'year')
        analytics = get_financial_analytics(period=period)
        kpis = get_financial_dashboard_kpis()

        return render(request, 'accounting/analytics_hub.html', {
            'active_tab': 'analytics',
            'analytics': analytics,
            'kpis': kpis,
            'period': period,
        })
