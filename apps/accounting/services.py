from decimal import Decimal
from datetime import date, timedelta
from django.db.models import Sum, Q, F, Count
from django.utils import timezone

from apps.billing.models import Invoice, PaymentReceipt
from apps.customers.models import Customer
from apps.logistics.models import LogisticsJob
from apps.quotes.models import Quotation
from apps.orders.models import PurchaseOrder
from .models import Expense, ExpenseCategory, Vendor, SupplierBill, SupplierBillPayment


def parse_date_range(start_date_str=None, end_date_str=None, default_days=30):
    """
    Parses start and end date parameters safely with sensible business defaults.
    """
    today = timezone.now().date()
    if not end_date_str:
        end_date = today
    elif isinstance(end_date_str, date):
        end_date = end_date_str
    else:
        try:
            end_date = date.fromisoformat(str(end_date_str))
        except (ValueError, TypeError):
            end_date = today

    if not start_date_str:
        start_date = end_date - timedelta(days=default_days)
    elif isinstance(start_date_str, date):
        start_date = start_date_str
    else:
        try:
            start_date = date.fromisoformat(str(start_date_str))
        except (ValueError, TypeError):
            start_date = end_date - timedelta(days=default_days)

    return start_date, end_date


# ============================================================
# 1. ACCOUNTS RECEIVABLE (AR) & AGING SERVICE
# ============================================================

def get_ar_summary():
    """
    Calculates unified Accounts Receivable metrics and aging distribution.
    Reconciles 100% with Invoice model balance_due.
    """
    today = timezone.now().date()
    
    # Active unpaid invoices
    unpaid_invoices = Invoice.objects.filter(
        status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.OVERDUE],
        balance_due__gt=Decimal('0.00')
    ).select_related('customer', 'job')

    total_receivables = unpaid_invoices.aggregate(total=Sum('balance_due'))['total'] or Decimal('0.00')
    total_invoiced = Invoice.objects.exclude(status=Invoice.Status.CANCELLED).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    total_collected = PaymentReceipt.objects.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')

    # Current vs Overdue
    current_ar = unpaid_invoices.filter(due_date__gte=today).aggregate(total=Sum('balance_due'))['total'] or Decimal('0.00')
    overdue_ar = unpaid_invoices.filter(due_date__lt=today).aggregate(total=Sum('balance_due'))['total'] or Decimal('0.00')

    # Aging Buckets
    aging = {
        'current': Decimal('0.00'),
        'days_1_30': Decimal('0.00'),
        'days_31_60': Decimal('0.00'),
        'days_61_90': Decimal('0.00'),
        'days_91_plus': Decimal('0.00'),
    }

    for inv in unpaid_invoices:
        bal = inv.balance_due
        if inv.due_date >= today:
            aging['current'] += bal
        else:
            days_overdue = (today - inv.due_date).days
            if days_overdue <= 30:
                aging['days_1_30'] += bal
            elif days_overdue <= 60:
                aging['days_31_60'] += bal
            elif days_overdue <= 90:
                aging['days_61_90'] += bal
            else:
                aging['days_91_plus'] += bal

    # Customer-level balances
    customer_balances = []
    customers = Customer.objects.filter(is_active=True).prefetch_related('invoices')
    for cust in customers:
        cust_unpaid = [i for i in cust.invoices.all() if i.status in [Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.OVERDUE] and i.balance_due > 0]
        cust_total_due = sum((i.balance_due for i in cust_unpaid), Decimal('0.00'))
        if cust_total_due > 0 or cust.invoices.exists():
            cust_current = sum((i.balance_due for i in cust_unpaid if i.due_date >= today), Decimal('0.00'))
            cust_overdue = sum((i.balance_due for i in cust_unpaid if i.due_date < today), Decimal('0.00'))
            customer_balances.append({
                'customer': cust,
                'total_due': cust_total_due,
                'current_due': cust_current,
                'overdue_due': cust_overdue,
                'invoices_count': len(cust_unpaid),
                'invoices': cust_unpaid,
            })

    customer_balances.sort(key=lambda x: x['total_due'], reverse=True)

    return {
        'total_receivables': total_receivables,
        'total_ar': total_receivables,
        'current_receivables': current_ar,
        'current_ar': current_ar,
        'overdue_receivables': overdue_ar,
        'overdue_ar': overdue_ar,
        'total_invoiced': total_invoiced,
        'total_collected': total_collected,
        'unpaid_count': unpaid_invoices.count(),
        'aging': aging,
        'aging_buckets': aging,
        'customer_balances': customer_balances,
        'unpaid_invoices': unpaid_invoices.order_by('due_date'),
    }


# ============================================================
# 2. ACCOUNTS PAYABLE (AP) & AGING SERVICE
# ============================================================

def get_ap_summary():
    """
    Calculates unified Accounts Payable metrics and vendor aging.
    """
    today = timezone.now().date()
    
    unpaid_bills = SupplierBill.objects.filter(
        status__in=[SupplierBill.Status.ISSUED, SupplierBill.Status.PARTIALLY_PAID, SupplierBill.Status.OVERDUE],
        balance_due__gt=Decimal('0.00')
    ).select_related('vendor')

    total_payables = unpaid_bills.aggregate(total=Sum('balance_due'))['total'] or Decimal('0.00')
    total_billed = SupplierBill.objects.exclude(status=SupplierBill.Status.VOID).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    total_paid = SupplierBillPayment.objects.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')

    current_ap = unpaid_bills.filter(due_date__gte=today).aggregate(total=Sum('balance_due'))['total'] or Decimal('0.00')
    overdue_ap = unpaid_bills.filter(due_date__lt=today).aggregate(total=Sum('balance_due'))['total'] or Decimal('0.00')

    aging = {
        'current': Decimal('0.00'),
        'days_1_30': Decimal('0.00'),
        'days_31_60': Decimal('0.00'),
        'days_61_90': Decimal('0.00'),
        'days_91_plus': Decimal('0.00'),
    }

    for bill in unpaid_bills:
        bal = bill.balance_due
        if bill.due_date >= today:
            aging['current'] += bal
        else:
            days_overdue = (today - bill.due_date).days
            if days_overdue <= 30:
                aging['days_1_30'] += bal
            elif days_overdue <= 60:
                aging['days_31_60'] += bal
            elif days_overdue <= 90:
                aging['days_61_90'] += bal
            else:
                aging['days_91_plus'] += bal

    vendor_balances = []
    vendors = Vendor.objects.filter(is_active=True).prefetch_related('bills')
    for v in vendors:
        v_unpaid = [b for b in v.bills.all() if b.status in [SupplierBill.Status.ISSUED, SupplierBill.Status.PARTIALLY_PAID, SupplierBill.Status.OVERDUE] and b.balance_due > 0]
        v_total_due = sum((b.balance_due for b in v_unpaid), Decimal('0.00'))
        if v_total_due > 0 or v.bills.exists():
            v_current = sum((b.balance_due for b in v_unpaid if b.due_date >= today), Decimal('0.00'))
            v_overdue = sum((b.balance_due for b in v_unpaid if b.due_date < today), Decimal('0.00'))
            vendor_balances.append({
                'vendor': v,
                'total_due': v_total_due,
                'current_due': v_current,
                'overdue_due': v_overdue,
                'bills_count': len(v_unpaid),
                'bills': v_unpaid,
            })

    vendor_balances.sort(key=lambda x: x['total_due'], reverse=True)

    return {
        'total_payables': total_payables,
        'total_ap': total_payables,
        'current_payables': current_ap,
        'current_ap': current_ap,
        'overdue_payables': overdue_ap,
        'overdue_ap': overdue_ap,
        'total_billed': total_billed,
        'total_paid': total_paid,
        'unpaid_count': unpaid_bills.count(),
        'aging': aging,
        'aging_buckets': aging,
        'vendor_balances': vendor_balances,
        'unpaid_bills': unpaid_bills.order_by('due_date'),
    }


# ============================================================
# 3. PROFIT & LOSS (P&L) STATEMENT SERVICE
# ============================================================

def get_profit_and_loss(start_date=None, end_date=None):
    """
    Generates a formal Profit & Loss Statement across standard or custom date range.
    Accrual Basis (Revenue earned from Invoices issued, Operating Expenses incurred).
    """
    start_date, end_date = parse_date_range(start_date, end_date, default_days=365)

    # 1. Revenue (Excluding Cancelled invoices)
    invoices_in_period = Invoice.objects.filter(
        issue_date__gte=start_date,
        issue_date__lte=end_date
    ).exclude(status=Invoice.Status.CANCELLED)

    sales_revenue = invoices_in_period.aggregate(total=Sum('subtotal'))['total'] or Decimal('0.00')
    vat_on_sales = invoices_in_period.aggregate(total=Sum('vat_amount'))['total'] or Decimal('0.00')
    gross_billed = invoices_in_period.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')

    total_revenue = sales_revenue

    # 2. Cost of Sales / Direct Costs (Direct Transport & Fuel expenses allocated)
    direct_categories = ['Transport', 'Fuel', 'Transport & Freight', 'Fuel & Tolls', 'Fuel & Diesel']
    direct_expenses_qs = Expense.objects.filter(
        date__gte=start_date,
        date__lte=end_date,
        category__name__in=direct_categories
    ).exclude(status=Expense.Status.VOID)
    cost_of_sales = direct_expenses_qs.aggregate(total=Sum('subtotal'))['total'] or Decimal('0.00')

    # Breakdown for direct costs
    cost_of_sales_breakdown = direct_expenses_qs.values('category__name').annotate(total=Sum('subtotal')).order_by('-total')

    # 3. Gross Profit
    gross_profit = total_revenue - cost_of_sales
    gross_margin_pct = (gross_profit / total_revenue * Decimal('100.00')).quantize(Decimal('0.01')) if total_revenue > 0 else Decimal('0.00')

    # 4. Operating Expenses (Excluding Direct Cost categories and Void)
    operating_expenses_qs = Expense.objects.filter(
        date__gte=start_date,
        date__lte=end_date
    ).exclude(category__name__in=direct_categories).exclude(status=Expense.Status.VOID)

    operating_expenses_breakdown = operating_expenses_qs.values('category__name').annotate(total=Sum('subtotal')).order_by('-total')

    expenses_by_category = []
    all_categories = ExpenseCategory.objects.exclude(name__in=direct_categories).order_by('name')
    for cat in all_categories:
        cat_total = Expense.objects.filter(
            category=cat,
            date__gte=start_date,
            date__lte=end_date
        ).exclude(status=Expense.Status.VOID).aggregate(total=Sum('subtotal'))['total'] or Decimal('0.00')
        
        cat_pct = (cat_total / total_revenue * Decimal('100.00')).quantize(Decimal('0.01')) if total_revenue > 0 else Decimal('0.00')
        expenses_by_category.append({
            'category': cat,
            'amount': cat_total,
            'percentage': cat_pct
        })

    # Sort categories with highest spend first
    expenses_by_category.sort(key=lambda x: x['amount'], reverse=True)
    total_operating_expenses = sum((c['amount'] for c in expenses_by_category), Decimal('0.00'))

    # Total all expenses (Direct + Operating)
    total_all_expenses = cost_of_sales + total_operating_expenses

    # 5. Net Profit / Loss
    net_profit = gross_profit - total_operating_expenses
    net_profit_margin_pct = (net_profit / total_revenue * Decimal('100.00')).quantize(Decimal('0.01')) if total_revenue > 0 else Decimal('0.00')

    return {
        'start_date': start_date,
        'end_date': end_date,
        'total_revenue': total_revenue,
        'sales_revenue': sales_revenue,
        'other_income': Decimal('0.00'),
        'vat_on_sales': vat_on_sales,
        'gross_billed': gross_billed,
        'cost_of_sales': cost_of_sales,
        'cost_of_sales_breakdown': cost_of_sales_breakdown,
        'gross_profit': gross_profit,
        'gross_margin_pct': gross_margin_pct,
        'gross_profit_margin': gross_margin_pct,
        'operating_expenses': total_operating_expenses,
        'operating_expenses_breakdown': operating_expenses_breakdown,
        'expenses_by_category': expenses_by_category,
        'total_operating_expenses': total_operating_expenses,
        'total_all_expenses': total_all_expenses,
        'net_profit': net_profit,
        'net_profit_margin_pct': net_profit_margin_pct,
        'net_profit_margin': net_profit_margin_pct,
        'invoices_count': invoices_in_period.count(),
    }


# ============================================================
# 4. BALANCE SHEET / FINANCIAL POSITION SERVICE
# ============================================================

def get_balance_sheet(as_of_date=None):
    """
    Builds the Statement of Financial Position (Balance Sheet) as of a given date.
    Verifies Assets = Liabilities + Equity.
    """
    if not as_of_date:
        as_of_date = timezone.now().date()
    elif isinstance(as_of_date, str):
        as_of_date = date.fromisoformat(as_of_date)

    # 1. Assets
    total_receipts = PaymentReceipt.objects.filter(payment_date__lte=as_of_date).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
    total_expenses_paid = Expense.objects.filter(date__lte=as_of_date, status=Expense.Status.PAID).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    total_bills_paid = SupplierBillPayment.objects.filter(payment_date__lte=as_of_date).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')

    # Net Cash Position
    cash_and_bank = max(Decimal('0.00'), total_receipts - total_expenses_paid - total_bills_paid)

    # Accounts Receivable as of date
    ar_summary = get_ar_summary()
    accounts_receivable = ar_summary['total_receivables']

    total_assets = cash_and_bank + accounts_receivable

    # 2. Liabilities
    ap_summary = get_ap_summary()
    accounts_payable = ap_summary['total_payables']

    # Net VAT Liability
    total_output_vat = Invoice.objects.filter(issue_date__lte=as_of_date).exclude(status=Invoice.Status.CANCELLED).aggregate(total=Sum('vat_amount'))['total'] or Decimal('0.00')
    total_input_vat_exp = Expense.objects.filter(date__lte=as_of_date).exclude(status=Expense.Status.VOID).aggregate(total=Sum('vat_amount'))['total'] or Decimal('0.00')
    total_input_vat_bills = SupplierBill.objects.filter(issue_date__lte=as_of_date).exclude(status=SupplierBill.Status.VOID).aggregate(total=Sum('vat_amount'))['total'] or Decimal('0.00')
    net_vat_liability = max(Decimal('0.00'), total_output_vat - (total_input_vat_exp + total_input_vat_bills))

    total_liabilities = accounts_payable + net_vat_liability

    # 3. Equity
    pnl_cumulative = get_profit_and_loss(start_date=date(2020, 1, 1), end_date=as_of_date)
    retained_earnings = pnl_cumulative['net_profit']
    total_equity = retained_earnings

    total_liabilities_and_equity = total_liabilities + total_equity

    # Accounting Verification
    discrepancy = total_assets - total_liabilities_and_equity
    is_balanced = abs(discrepancy) < Decimal('1.00')

    return {
        'as_of_date': as_of_date,
        'cash_and_bank': cash_and_bank,
        'cash_on_hand': cash_and_bank,
        'accounts_receivable': accounts_receivable,
        'total_assets': total_assets,
        'accounts_payable': accounts_payable,
        'net_vat_liability': net_vat_liability,
        'tax_liabilities': net_vat_liability,
        'total_liabilities': total_liabilities,
        'retained_earnings': retained_earnings,
        'total_equity': total_equity,
        'total_liabilities_and_equity': total_liabilities_and_equity,
        'is_balanced': is_balanced,
        'discrepancy': discrepancy,
    }


# ============================================================
# 5. CASH FLOW STATEMENT SERVICE
# ============================================================

def get_cash_flow(start_date=None, end_date=None):
    """
    Calculates actual cash inflows and outflows for the given period.
    """
    start_date, end_date = parse_date_range(start_date, end_date, default_days=30)

    # Opening Cash (Prior to start_date)
    prior_receipts = PaymentReceipt.objects.filter(payment_date__lt=start_date).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
    prior_expenses = Expense.objects.filter(date__lt=start_date, status=Expense.Status.PAID).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')
    prior_bill_payments = SupplierBillPayment.objects.filter(payment_date__lt=start_date).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
    opening_cash = max(Decimal('0.00'), prior_receipts - prior_expenses - prior_bill_payments)

    # Cash Inflows in period
    receipts_in_period = PaymentReceipt.objects.filter(payment_date__gte=start_date, payment_date__lte=end_date)
    customer_payments = receipts_in_period.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
    total_cash_in = customer_payments

    # Cash Outflows in period
    expenses_in_period = Expense.objects.filter(date__gte=start_date, date__lte=end_date, status=Expense.Status.PAID)
    direct_expense_payments = expenses_in_period.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')

    bill_payments_in_period = SupplierBillPayment.objects.filter(payment_date__gte=start_date, payment_date__lte=end_date)
    supplier_bill_payments = bill_payments_in_period.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')

    total_cash_out = direct_expense_payments + supplier_bill_payments

    # Net Cash Movement
    net_cash_flow = total_cash_in - total_cash_out
    closing_cash = opening_cash + net_cash_flow

    return {
        'start_date': start_date,
        'end_date': end_date,
        'opening_cash': opening_cash,
        'opening_balance': opening_cash,
        'customer_payments': customer_payments,
        'cash_in_customer_payments': customer_payments,
        'total_cash_in': total_cash_in,
        'direct_expenses': direct_expense_payments,
        'cash_out_expenses': direct_expense_payments,
        'supplier_payments': supplier_bill_payments,
        'cash_out_supplier_bills': supplier_bill_payments,
        'total_cash_out': total_cash_out,
        'net_cash_flow': net_cash_flow,
        'closing_cash': closing_cash,
        'closing_balance': closing_cash,
        'receipts_count': receipts_in_period.count(),
        'expenses_count': expenses_in_period.count(),
        'bill_payments_count': bill_payments_in_period.count(),
    }


# ============================================================
# 6. VAT / TAX REPORTING SERVICE
# ============================================================

def get_vat_report(start_date=None, end_date=None):
    """
    Generates Output VAT vs Input VAT tax report for VAT filing periods.
    """
    start_date, end_date = parse_date_range(start_date, end_date, default_days=60)

    # Output VAT (from Tax Invoices)
    invoices = Invoice.objects.filter(
        issue_date__gte=start_date,
        issue_date__lte=end_date
    ).exclude(status=Invoice.Status.CANCELLED).select_related('customer')

    total_sales_excl_vat = invoices.aggregate(total=Sum('subtotal'))['total'] or Decimal('0.00')
    output_vat = invoices.aggregate(total=Sum('vat_amount'))['total'] or Decimal('0.00')
    total_sales_incl_vat = invoices.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')

    # Cash VAT collected on paid invoices
    vat_collected_cash = PaymentReceipt.objects.filter(
        payment_date__gte=start_date,
        payment_date__lte=end_date
    ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')
    vat_collected_cash = (vat_collected_cash * Decimal('15.00') / Decimal('115.00')).quantize(Decimal('0.01'))

    # Input VAT (from Expenses)
    expenses = Expense.objects.filter(
        date__gte=start_date,
        date__lte=end_date
    ).exclude(status=Expense.Status.VOID).select_related('category', 'vendor')

    total_expenses_excl_vat = expenses.aggregate(total=Sum('subtotal'))['total'] or Decimal('0.00')
    input_vat_expenses = expenses.aggregate(total=Sum('vat_amount'))['total'] or Decimal('0.00')
    total_expenses_incl_vat = expenses.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')

    # Input VAT (from Supplier Bills)
    bills = SupplierBill.objects.filter(
        issue_date__gte=start_date,
        issue_date__lte=end_date
    ).exclude(status=SupplierBill.Status.VOID).select_related('vendor')

    total_bills_excl_vat = bills.aggregate(total=Sum('subtotal'))['total'] or Decimal('0.00')
    input_vat_bills = bills.aggregate(total=Sum('vat_amount'))['total'] or Decimal('0.00')
    total_bills_incl_vat = bills.aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')

    total_input_vat = input_vat_expenses + input_vat_bills
    total_purchases_excl_vat = total_expenses_excl_vat + total_bills_excl_vat
    total_purchases_incl_vat = total_expenses_incl_vat + total_bills_incl_vat

    net_vat_payable = output_vat - total_input_vat

    return {
        'start_date': start_date,
        'end_date': end_date,
        'total_sales_excl_vat': total_sales_excl_vat,
        'taxable_sales_subtotal': total_sales_excl_vat,
        'output_vat': output_vat,
        'output_vat_invoiced': output_vat,
        'total_sales_incl_vat': total_sales_incl_vat,
        'gross_sales_total': total_sales_incl_vat,
        'vat_collected_cash': vat_collected_cash,
        'total_purchases_excl_vat': total_purchases_excl_vat,
        'taxable_purchases_subtotal': total_purchases_excl_vat,
        'input_vat_expenses': input_vat_expenses,
        'input_vat_bills': input_vat_bills,
        'total_input_vat': total_input_vat,
        'gross_purchases_total': total_purchases_incl_vat,
        'net_vat_payable': net_vat_payable,
        'invoices': invoices.order_by('-issue_date'),
        'expenses': expenses.order_by('-date'),
        'bills': bills.order_by('-issue_date'),
    }


# ============================================================
# 7. CUSTOMER ACCOUNT STATEMENT SERVICE
# ============================================================

def get_customer_statement(customer, start_date=None, end_date=None):
    """
    Generates a reconciled Customer Statement with opening balance,
    chronological debits/credits ledger, running balance, and closing balance.
    """
    if isinstance(customer, int) or isinstance(customer, str):
        customer = Customer.objects.get(pk=customer)

    start_date, end_date = parse_date_range(start_date, end_date, default_days=60)

    # 1. Opening Balance prior to start_date
    prior_invoices = Invoice.objects.filter(
        customer=customer,
        issue_date__lt=start_date
    ).exclude(status=Invoice.Status.CANCELLED).aggregate(total=Sum('total_amount'))['total'] or Decimal('0.00')

    prior_payments = PaymentReceipt.objects.filter(
        customer=customer,
        payment_date__lt=start_date
    ).aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')

    opening_balance = prior_invoices - prior_payments

    # 2. Transactions within date range
    invoices_in_range = list(Invoice.objects.filter(
        customer=customer,
        issue_date__gte=start_date,
        issue_date__lte=end_date
    ).exclude(status=Invoice.Status.CANCELLED))

    payments_in_range = list(PaymentReceipt.objects.filter(
        customer=customer,
        payment_date__gte=start_date,
        payment_date__lte=end_date
    ))

    # Combine into chronological timeline
    ledger_entries = []
    for inv in invoices_in_range:
        ledger_entries.append({
            'date': inv.issue_date,
            'type': 'INVOICE',
            'type_display': f"Tax Invoice ({inv.get_invoice_type_display()})",
            'reference': inv.invoice_number,
            'debit': inv.total_amount,
            'credit': Decimal('0.00'),
            'object': inv,
        })

    for rcp in payments_in_range:
        ledger_entries.append({
            'date': rcp.payment_date,
            'type': 'PAYMENT',
            'type_display': f"Payment Receipt ({rcp.get_payment_method_display()})",
            'reference': rcp.receipt_number,
            'debit': Decimal('0.00'),
            'credit': rcp.amount_paid,
            'object': rcp,
        })

    ledger_entries.sort(key=lambda x: (x['date'], 0 if x['type'] == 'INVOICE' else 1))

    # Calculate running balance
    current_running = opening_balance
    for entry in ledger_entries:
        current_running += (entry['debit'] - entry['credit'])
        entry['running_balance'] = current_running

    closing_balance = current_running

    total_debits = sum((e['debit'] for e in ledger_entries), Decimal('0.00'))
    total_credits = sum((e['credit'] for e in ledger_entries), Decimal('0.00'))

    # All-time outstanding for reconciliation check
    current_ar = Invoice.objects.filter(
        customer=customer,
        status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID, Invoice.Status.OVERDUE]
    ).aggregate(total=Sum('balance_due'))['total'] or Decimal('0.00')

    return {
        'customer': customer,
        'start_date': start_date,
        'end_date': end_date,
        'opening_balance': opening_balance,
        'entries': ledger_entries,
        'transactions': ledger_entries,
        'total_debits': total_debits,
        'total_invoiced': total_debits,
        'total_credits': total_credits,
        'total_paid': total_credits,
        'closing_balance': closing_balance,
        'current_ar_total': current_ar,
    }


# ============================================================
# 8. FINANCIAL DASHBOARD & ANALYTICS
# ============================================================

def get_financial_dashboard_kpis():
    """
    Consolidated executive KPI summary for the Accounts Overview page.
    """
    today = timezone.now().date()
    start_of_month = today.replace(day=1)
    
    # Receivables & Payables
    ar_data = get_ar_summary()
    ap_data = get_ap_summary()

    # MTD Profit & Loss
    pnl_mtd = get_profit_and_loss(start_date=start_of_month, end_date=today)
    pnl_ytd = get_profit_and_loss(start_date=today.replace(month=1, day=1), end_date=today)

    # Cash movement this month
    cash_flow_mtd = get_cash_flow(start_date=start_of_month, end_date=today)

    # VAT Position
    vat_data = get_vat_report(start_date=start_of_month, end_date=today)

    return {
        'ar': ar_data,
        'ap': ap_data,
        'pnl_mtd': pnl_mtd,
        'pnl_ytd': pnl_ytd,
        'cash_flow_mtd': cash_flow_mtd,
        'vat': vat_data,
        'active_customers_count': Customer.objects.filter(is_active=True).count(),
        'active_vendors_count': Vendor.objects.filter(is_active=True).count(),
    }


def get_financial_analytics(period='year'):
    """
    Returns monthly time-series analytics for revenue, expenses, and profitability.
    """
    today = timezone.now().date()
    months_count = 12 if period == 'year' else 6
    monthly_series = []

    for i in range(months_count - 1, -1, -1):
        # Calculate month date window
        month_target = (today.replace(day=1) - timedelta(days=i * 28)).replace(day=1)
        next_month = (month_target + timedelta(days=32)).replace(day=1)
        month_end = next_month - timedelta(days=1)

        pnl = get_profit_and_loss(start_date=month_target, end_date=month_end)
        monthly_series.append({
            'month_label': month_target.strftime('%b %Y'),
            'revenue': pnl['total_revenue'],
            'expenses': pnl['total_all_expenses'],
            'net_profit': pnl['net_profit'],
            'margin_pct': pnl['net_profit_margin_pct']
        })

    # Top 5 Customers by Revenue
    top_customers = []
    customers = Customer.objects.filter(is_active=True)
    for c in customers:
        rev = Invoice.objects.filter(customer=c).exclude(status=Invoice.Status.CANCELLED).aggregate(total=Sum('subtotal'))['total'] or Decimal('0.00')
        if rev > 0:
            top_customers.append({'customer': c, 'revenue': rev})
    top_customers.sort(key=lambda x: x['revenue'], reverse=True)
    top_customers = top_customers[:5]

    # Top 5 Expense Categories
    top_expenses = []
    for cat in ExpenseCategory.objects.filter(is_active=True):
        amt = Expense.objects.filter(category=cat).exclude(status=Expense.Status.VOID).aggregate(total=Sum('subtotal'))['total'] or Decimal('0.00')
        if amt > 0:
            top_expenses.append({'category': cat, 'amount': amt})
    top_expenses.sort(key=lambda x: x['amount'], reverse=True)
    top_expenses = top_expenses[:5]

    # Operations metrics
    ops_summary = {
        'orders_count': PurchaseOrder.objects.count(),
        'quotes_count': Quotation.objects.count(),
        'jobs_count': LogisticsJob.objects.count(),
        'invoices_count': Invoice.objects.count(),
        'receipts_count': PaymentReceipt.objects.count(),
    }

    return {
        'monthly_series': monthly_series,
        'top_customers': top_customers,
        'top_expenses': top_expenses,
        'ops_summary': ops_summary,
    }
