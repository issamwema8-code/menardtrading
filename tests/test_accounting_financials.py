import datetime
from decimal import Decimal
from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone

from apps.customers.models import Customer
from apps.orders.models import PurchaseOrder
from apps.quotes.models import Quotation
from apps.logistics.models import LogisticsJob
from apps.billing.models import Invoice, PaymentReceipt
from apps.accounting.models import ExpenseCategory, Vendor, Expense, SupplierBill, SupplierBillPayment
from apps.accounting.services import (
    get_ar_summary,
    get_ap_summary,
    get_profit_and_loss,
    get_balance_sheet,
    get_cash_flow,
    get_vat_report,
    get_customer_statement,
    get_financial_dashboard_kpis
)
from apps.accounts.models import Role, UserProfile


class AccountingFinancialsTests(TestCase):
    def setUp(self):
        self.client = Client()

        # Create admin user
        self.admin_user = User.objects.create_superuser(
            username='admin_test',
            email='admin@menardtrading.com',
            password='testpassword123'
        )

        # Create standard staff user
        self.staff_user = User.objects.create_user(
            username='staff_test',
            email='staff@menardtrading.com',
            password='testpassword123'
        )
        # Get or create profile (created via post_save signal)
        self.profile, _ = UserProfile.objects.get_or_create(user=self.staff_user)

        # Create customer
        self.customer = Customer.objects.create(
            company_name='Namibia TransLog CC',
            contact_name='Johannes Shilongo',
            email='johannes@namibiatranslog.com',
            vat_number='VAT40998877',
            payment_terms='30 Days Net'
        )

        # Get or create Expense Categories
        self.fuel_cat, _ = ExpenseCategory.objects.get_or_create(
            name='Fuel',
            defaults={'code': 'FUEL'}
        )
        self.rent_cat, _ = ExpenseCategory.objects.get_or_create(
            name='Rent',
            defaults={'code': 'RENT'}
        )

        # Create Vendor
        self.vendor = Vendor.objects.create(
            name='Engen Namibia Fuel Services',
            contact_name='Petrus Nauyoma',
            email='accounts@engen.com.na'
        )

        # Create PO, Job, Invoice, Payment
        self.po = PurchaseOrder.objects.create(
            customer=self.customer,
            po_number='PO-NAM-001',
            pickup_location='Windhoek Logistics Hub',
            delivery_location='Walvis Bay Harbour',
            status='COMPLETED'
        )
        self.quote = Quotation.objects.create(
            purchase_order=self.po,
            customer=self.customer,
            quote_number='QT-TEST-001',
            subtotal=Decimal('10000.00'),
            vat_rate=Decimal('15.00'),
            vat_amount=Decimal('1500.00'),
            total_amount=Decimal('11500.00'),
            status='APPROVED'
        )
        self.job = LogisticsJob.objects.create(
            quote=self.quote,
            customer=self.customer,
            job_number='JOB-TEST-001',
            driver_name='Klaus Becker',
            status='DELIVERED'
        )
        self.invoice = Invoice.objects.create(
            job=self.job,
            customer=self.customer,
            invoice_number='INV-TEST-001',
            invoice_type='FULL',
            subtotal=Decimal('10000.00'),
            vat_amount=Decimal('1500.00'),
            total_amount=Decimal('11500.00'),
            amount_paid=Decimal('4000.00'),
            balance_due=Decimal('7500.00'),
            status='PARTIALLY_PAID',
            due_date=timezone.now().date() + datetime.timedelta(days=15)
        )
        self.payment = PaymentReceipt.objects.create(
            invoice=self.invoice,
            customer=self.customer,
            receipt_number='RCP-TEST-001',
            amount_paid=Decimal('4000.00'),
            payment_method='EFT_BANK_TRANSFER',
            payment_date=timezone.now().date()
        )

    def test_expense_creation_and_vat_calculation(self):
        """Test creating an expense computes 15% VAT and subtotal correctly."""
        expense = Expense.objects.create(
            category=self.fuel_cat,
            vendor=self.vendor,
            payee=self.vendor.name,
            subtotal=Decimal('2000.00'),
            date=timezone.now().date(),
            status='PAID'
        )
        self.assertTrue(expense.expense_number.startswith('EXP-'))
        self.assertEqual(expense.vat_amount, Decimal('300.00'))
        self.assertEqual(expense.total_amount, Decimal('2300.00'))

    def test_ar_summary_and_aging_buckets(self):
        """Test Accounts Receivable calculation matches invoice balance due."""
        ar_data = get_ar_summary()
        self.assertEqual(ar_data['total_ar'], Decimal('7500.00'))
        self.assertEqual(ar_data['current_ar'], Decimal('7500.00'))
        self.assertEqual(ar_data['aging_buckets']['current'], Decimal('7500.00'))

    def test_ap_summary_and_bill_payment(self):
        """Test Supplier Bills and Accounts Payable calculation."""
        bill = SupplierBill.objects.create(
            vendor=self.vendor,
            supplier_reference='BILL-ENGEN-101',
            issue_date=timezone.now().date(),
            due_date=timezone.now().date() + datetime.timedelta(days=30),
            subtotal=Decimal('5000.00'),
            status='ISSUED'
        )
        bill.recalculate_totals()
        SupplierBillPayment.objects.create(
            bill=bill,
            amount_paid=Decimal('2000.00'),
            payment_date=timezone.now().date()
        )
        bill.refresh_from_db()
        self.assertEqual(bill.balance_due, Decimal('3750.00'))
        ap_data = get_ap_summary()
        self.assertEqual(ap_data['total_ap'], Decimal('3750.00'))
        self.assertEqual(ap_data['aging_buckets']['current'], Decimal('3750.00'))

    def test_profit_and_loss_reconciliation(self):
        """Test P&L: Revenue - Cost of Sales - Operating Expenses = Net Profit."""
        # Direct cost
        Expense.objects.create(
            category=self.fuel_cat, # Cost of sales
            payee='Engen',
            subtotal=Decimal('2000.00'),
            date=timezone.now().date(),
            status='PAID'
        )
        # Operating overhead
        Expense.objects.create(
            category=self.rent_cat, # Operating overhead
            payee='Prime Properties Windhoek',
            subtotal=Decimal('1000.00'),
            date=timezone.now().date(),
            status='PAID'
        )
        pnl = get_profit_and_loss()
        self.assertEqual(pnl['sales_revenue'], Decimal('10000.00')) # Invoiced subtotal
        self.assertEqual(pnl['cost_of_sales'], Decimal('2000.00'))
        self.assertEqual(pnl['gross_profit'], Decimal('8000.00'))
        self.assertEqual(pnl['operating_expenses'], Decimal('1000.00'))
        self.assertEqual(pnl['net_profit'], Decimal('7000.00'))

    def test_balance_sheet_equation(self):
        """Test Balance Sheet: Assets = Liabilities + Equity."""
        # Add expense
        Expense.objects.create(
            category=self.fuel_cat,
            payee='Engen',
            subtotal=Decimal('2000.00'),
            date=timezone.now().date(),
            status='PAID'
        )
        bs = get_balance_sheet()
        self.assertTrue(bs['is_balanced'])
        self.assertEqual(bs['total_assets'], bs['total_liabilities_and_equity'])

    def test_cash_flow_statement(self):
        """Test Cash Flow statement: Cash In - Cash Out = Net Movement."""
        Expense.objects.create(
            category=self.fuel_cat,
            payee='Engen',
            subtotal=Decimal('2000.00'),
            date=timezone.now().date(),
            status='PAID'
        )
        cf = get_cash_flow()
        self.assertEqual(cf['cash_in_customer_payments'], Decimal('4000.00'))
        self.assertEqual(cf['cash_out_expenses'], Decimal('2300.00'))
        self.assertEqual(cf['net_cash_flow'], Decimal('1700.00'))
        self.assertEqual(cf['closing_balance'], Decimal('1700.00'))

    def test_customer_statement_running_balance(self):
        """Test customer running balance matches actual outstanding AR."""
        statement = get_customer_statement(self.customer)
        self.assertEqual(statement['total_invoiced'], Decimal('11500.00'))
        self.assertEqual(statement['total_paid'], Decimal('4000.00'))
        self.assertEqual(statement['closing_balance'], Decimal('7500.00'))
        self.assertEqual(self.invoice.balance_due, statement['closing_balance'])

    def test_vat_report_calculation(self):
        """Test Output VAT - Input VAT = Net VAT Payable."""
        Expense.objects.create(
            category=self.fuel_cat,
            payee='Engen',
            subtotal=Decimal('2000.00'),
            date=timezone.now().date(),
            status='PAID'
        )
        vat = get_vat_report()
        self.assertEqual(vat['output_vat_invoiced'], Decimal('1500.00'))
        self.assertEqual(vat['input_vat_expenses'], Decimal('300.00'))
        self.assertEqual(vat['net_vat_payable'], Decimal('1200.00'))

    def test_accounting_views_access_control(self):
        """Test RBAC on accounting views."""
        # Unauthenticated user should be redirected to login
        response = self.client.get(reverse('accounting:overview'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/login/', response.url)

        # Authenticated Admin has full access
        self.client.force_login(self.admin_user)
        for view_name in [
            'accounting:overview',
            'accounting:expenses',
            'accounting:expense_categories',
            'accounting:receivables',
            'accounting:payables',
            'accounting:profit_loss',
            'accounting:balance_sheet',
            'accounting:cash_flow',
            'accounting:vat_report',
            'accounting:customer_statements',
            'accounting:reports_hub',
            'accounting:analytics_hub'
        ]:
            resp = self.client.get(reverse(view_name))
            self.assertEqual(resp.status_code, 200, f"Failed accessing {view_name}")

    def test_csv_export_endpoint(self):
        """Test CSV export streaming endpoint."""
        self.client.force_login(self.admin_user)
        for report_type in ['sales', 'payments', 'expenses', 'ar', 'ap', 'pnl', 'vat', 'balance_sheet', 'cash_flow', 'operations']:
            resp = self.client.get(reverse('accounting:reports_export'), {'report': report_type})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp['Content-Type'], 'text/csv')
