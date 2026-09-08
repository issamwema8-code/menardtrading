from .permissions import get_user_permissions, has_permission


def rbac_context(request):
    """
    Template context processor exposing authenticated user's permissions
    and permission-check helper flags to all templates.
    """
    if not hasattr(request, 'user') or not request.user.is_authenticated:
        return {
            'user_perms': set(),
            'is_superuser': False,
            'can_access_admin': False,
        }

    user = request.user
    perms = get_user_permissions(user)
    is_super = user.is_superuser

    return {
        'user_perms': perms,
        'is_superuser': is_super,
        # 1. Executive Overview Nav Link
        'can_view_overview': True,
        # 2. Purchase Orders Nav Link & Actions
        'can_view_orders': is_super or 'orders.view' in perms,
        'can_create_orders': is_super or 'orders.create' in perms,
        'can_upload_orders': is_super or 'orders.upload' in perms or 'orders.create' in perms,
        'can_sync_orders': is_super or 'orders.email' in perms,
        # 3. Quotations Nav Link & Actions
        'can_view_quotes': is_super or 'quotes.view' in perms,
        'can_create_quotes': is_super or 'quotes.create' in perms,
        # 4. Deliveries & Fleet Nav Link & Actions
        'can_view_logistics': is_super or 'logistics.view' in perms,
        'can_create_logistics': is_super or 'logistics.create' in perms,
        # 5. Invoices & Billing Nav Link & Actions
        'can_view_invoices': is_super or 'invoices.view' in perms,
        'can_create_invoices': is_super or 'invoices.create' in perms,
        'can_record_payments': is_super or 'invoices.record_payment' in perms,
        # 6. Payment Receipts Nav Link & Actions
        'can_view_receipts': is_super or 'receipts.view' in perms,
        # 7. Customer Directory Nav Link & Actions
        'can_view_customers': is_super or 'customers.view' in perms,
        'can_create_customers': is_super or 'customers.create' in perms,
        # 8. Accounts & Financials Module Sub-Nav Links
        'can_view_accounts': is_super or any(p in perms for p in [
            'accounts.view', 'accounts.overview.view', 'expenses.view', 'accounts.receivables.view',
            'accounts.payables.view', 'accounts.statements.view', 'accounts.profit_loss.view',
            'accounts.balance_sheet.view', 'accounts.cash_flow.view', 'accounts.vat.view'
        ]),
        'can_view_financial_overview': is_super or 'accounts.view' in perms or 'accounts.overview.view' in perms,
        'can_view_receivables': is_super or 'accounts.receivables.view' in perms,
        'can_view_payables': is_super or 'accounts.payables.view' in perms,
        'can_view_expenses': is_super or 'expenses.view' in perms,
        'can_create_expenses': is_super or 'expenses.create' in perms,
        'can_view_statements': is_super or 'accounts.statements.view' in perms,
        'can_view_profit_loss': is_super or 'accounts.profit_loss.view' in perms,
        'can_view_balance_sheet': is_super or 'accounts.balance_sheet.view' in perms,
        'can_view_cash_flow': is_super or 'accounts.cash_flow.view' in perms,
        'can_view_vat': is_super or 'accounts.vat.view' in perms,
        # 9. Reports Center Nav Link
        'can_view_reports': is_super or 'reports.view' in perms,
        'can_export_reports': is_super or 'reports.export' in perms,
        # 10. Analytics Hub Nav Link
        'can_view_analytics': is_super or 'analytics.view' in perms,
        # 11. Administration Module Sub-Nav Links
        'can_manage_users': is_super or 'users.view' in perms,
        'can_manage_roles': is_super or 'roles.view' in perms,
        'can_view_audit': is_super or 'audit.view' in perms,
        'can_manage_settings': is_super or any(p in perms for p in ['settings.view', 'settings.edit', 'settings.update']),
        'can_edit_settings': is_super or any(p in perms for p in ['settings.edit', 'settings.update']),
        'can_access_admin': is_super or any(p in perms for p in ['users.view', 'roles.view', 'audit.view', 'settings.view', 'settings.edit', 'settings.update']),
    }

