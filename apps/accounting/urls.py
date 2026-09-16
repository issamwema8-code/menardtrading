from django.urls import path
from .views import (
    FinancialOverviewView,
    ExpensesListView,
    CreateExpenseView,
    VoidExpenseView,
    ExpenseCategoriesView,
    AccountsReceivableView,
    AccountsPayableView,
    CreateSupplierBillView,
    RecordBillPaymentView,
    ProfitLossView,
    BalanceSheetView,
    CashFlowView,
    VATReportView,
    CustomerStatementsView,
    CustomerStatementDetailView,
    CustomerStatementPDFDownloadView,
    ReportsHubView,
    ExportReportCSVView,
    AnalyticsHubView,
)

app_name = 'accounting'

urlpatterns = [
    # 1. Financial Dashboard
    path('accounts/overview/', FinancialOverviewView.as_view(), name='overview'),

    # 2. Expenses Module
    path('accounts/expenses/', ExpensesListView.as_view(), name='expenses'),
    path('accounts/expenses/create/', CreateExpenseView.as_view(), name='expenses_create'),
    path('accounts/expenses/<int:pk>/void/', VoidExpenseView.as_view(), name='expenses_void'),
    path('accounts/expenses/categories/', ExpenseCategoriesView.as_view(), name='expense_categories'),

    # 3. Accounts Receivable & Aging
    path('accounts/receivables/', AccountsReceivableView.as_view(), name='receivables'),

    # 4. Accounts Payable & Supplier Bills
    path('accounts/payables/', AccountsPayableView.as_view(), name='payables'),
    path('accounts/bills/create/', CreateSupplierBillView.as_view(), name='bills_create'),
    path('accounts/bills/<int:pk>/payment/', RecordBillPaymentView.as_view(), name='bills_payment'),

    # 5. Financial Statements
    path('accounts/profit-loss/', ProfitLossView.as_view(), name='profit_loss'),
    path('accounts/balance-sheet/', BalanceSheetView.as_view(), name='balance_sheet'),
    path('accounts/cash-flow/', CashFlowView.as_view(), name='cash_flow'),
    path('accounts/vat/', VATReportView.as_view(), name='vat_report'),

    # 6. Customer Account Statements
    path('accounts/statements/', CustomerStatementsView.as_view(), name='customer_statements'),
    path('accounts/statements/<int:customer_id>/', CustomerStatementDetailView.as_view(), name='customer_statement_detail'),
    path('accounts/statements/<int:customer_id>/pdf/', CustomerStatementPDFDownloadView.as_view(), name='customer_statement_pdf'),

    # 7. Reports Hub
    path('reports/', ReportsHubView.as_view(), name='reports_hub'),
    path('reports/export-csv/', ExportReportCSVView.as_view(), name='reports_export'),

    # 8. Analytics Hub
    path('analytics/', AnalyticsHubView.as_view(), name='analytics_hub'),
]
