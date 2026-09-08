from django.db.models.signals import post_save, post_migrate
from django.dispatch import receiver
from django.contrib.auth.models import User
from .models import UserProfile, SystemPermission, Role


# Standard System Permissions Matrix (Every Navigation Link and Action has its own dedicated permission)
SYSTEM_PERMISSIONS = [
    # 1. Executive Overview
    ('overview.view', 'View Executive Overview', 'Overview', 'Can access the Operations Overview navigation link and KPI pipeline'),

    # 2. Purchase Orders
    ('orders.view', 'View Purchase Orders', 'Purchase Orders', 'Can access the Purchase Orders navigation link and view inbound orders'),
    ('orders.create', 'Create Purchase Orders', 'Purchase Orders', 'Can manually draft and submit new purchase orders'),
    ('orders.upload', 'Upload PO Files', 'Purchase Orders', 'Can upload customer purchase order PDFs for automated parsing'),
    ('orders.email', 'Sync Mailbox & Send PO Confirmations', 'Purchase Orders', 'Can trigger mailbox sync and dispatch order confirmation emails'),
    ('orders.delete', 'Delete Purchase Orders', 'Purchase Orders', 'Can cancel or remove purchase order records'),

    # 3. Quotations
    ('quotes.view', 'View Quotations', 'Quotations', 'Can access the Quotations navigation link and view quote breakdown'),
    ('quotes.create', 'Create Quotations', 'Quotations', 'Can build, price, and issue customer price quotations'),
    ('quotes.send', 'Email Quotations', 'Quotations', 'Can dispatch price quotations to customers by email'),
    ('quotes.approve', 'Approve Quotations', 'Quotations', 'Can approve quotations and schedule logistics dispatch'),
    ('quotes.download', 'Download Quote PDFs', 'Quotations', 'Can generate and download official quotation PDFs'),
    ('quotes.delete', 'Delete Quotations', 'Quotations', 'Can delete draft and rejected quotations'),

    # 4. Deliveries & Fleet (Logistics)
    ('logistics.view', 'View Deliveries & Fleet', 'Deliveries & Fleet', 'Can access the Deliveries & Fleet navigation link and track consignments'),
    ('logistics.create', 'Create Deliveries', 'Deliveries & Fleet', 'Can initialize new logistics jobs and assign drivers'),
    ('logistics.update_status', 'Update Delivery Status', 'Deliveries & Fleet', 'Can update job tracking status (Dispatched, In Transit, Delivered)'),
    ('logistics.upload_pod', 'Upload Proof of Delivery (POD)', 'Deliveries & Fleet', 'Can upload verified Proof of Delivery scans/photos'),
    ('logistics.delete', 'Delete Deliveries', 'Deliveries & Fleet', 'Can cancel or remove delivery consignments'),

    # 5. Invoices & Billing
    ('invoices.view', 'View Invoices', 'Invoices & Billing', 'Can access the Invoices navigation link and view client billing'),
    ('invoices.create', 'Create Invoices', 'Invoices & Billing', 'Can issue upfront, deposit, balance, and add-on tax invoices'),
    ('invoices.send', 'Email Invoices', 'Invoices & Billing', 'Can dispatch official tax invoices by email'),
    ('invoices.record_payment', 'Record Payments', 'Invoices & Billing', 'Can record customer EFT/card payments and trigger receipts'),
    ('invoices.download', 'Download Invoice PDFs', 'Invoices & Billing', 'Can download official Tax Invoice PDFs'),
    ('invoices.delete', 'Delete / Void Invoices', 'Invoices & Billing', 'Can void or delete draft invoice records'),

    # 6. Payment Receipts
    ('receipts.view', 'View Payment Receipts', 'Payment Receipts', 'Can access the Payment Receipts navigation link and view settled payments'),
    ('receipts.download', 'Download Receipt PDFs', 'Payment Receipts', 'Can download official branded payment receipt PDFs'),
    ('receipts.delete', 'Delete Receipts', 'Payment Receipts', 'Can remove payment receipt records'),

    # 7. Accounts & Financials (Individual Granular Nav Link Permissions)
    ('accounts.view', 'View Financial Overview', 'Accounts & Financials', 'Can access the Financial Overview navigation link and executive metrics'),
    ('accounts.overview.view', 'View Financial Overview (Alias)', 'Accounts & Financials', 'Can access the executive Financial Dashboard'),
    ('accounts.receivables.view', 'View Accounts Receivable (AR)', 'Accounts & Financials', 'Can access the Accounts Receivable (AR) navigation link and aging analysis'),
    ('accounts.payables.view', 'View Accounts Payable (AP)', 'Accounts & Financials', 'Can access the Accounts Payable (AP) navigation link and supplier obligations'),
    ('accounts.payables.create', 'Manage Supplier Bills', 'Accounts & Financials', 'Can record vendor bills and register bill settlement payments'),
    ('expenses.view', 'View Expenses Management', 'Accounts & Financials', 'Can access the Expenses Management navigation link and expense registry'),
    ('expenses.create', 'Record Expenses', 'Accounts & Financials', 'Can record new business expenses, attach receipts, and calculate VAT'),
    ('expenses.update', 'Update Expenses', 'Accounts & Financials', 'Can edit expense categories, payees, and amounts'),
    ('expenses.delete', 'Void Expenses', 'Accounts & Financials', 'Can void or remove business expense records'),
    ('accounts.statements.view', 'View Customer Statements', 'Accounts & Financials', 'Can access the Customer Statements navigation link and download statements'),
    ('accounts.profit_loss.view', 'View Profit & Loss Statement', 'Accounts & Financials', 'Can access the Profit & Loss Statement navigation link'),
    ('accounts.balance_sheet.view', 'View Balance Sheet', 'Accounts & Financials', 'Can access the Balance Sheet (Statement of Financial Position) navigation link'),
    ('accounts.cash_flow.view', 'View Cash Flow Statement', 'Accounts & Financials', 'Can access the Cash Flow Statement navigation link'),
    ('accounts.vat.view', 'View VAT & Tax Reports', 'Accounts & Financials', 'Can access the VAT & Tax Report navigation link and liability breakdowns'),

    # 8. Reports Center
    ('reports.view', 'View Reports Center', 'Reports Center', 'Can access the Reports Center navigation link and view tabbed reports'),
    ('reports.export', 'Export Reports', 'Reports Center', 'Can export financial and operational reports as CSV/PDF'),

    # 9. Analytics Hub
    ('analytics.view', 'View Analytics Hub', 'Analytics Hub', 'Can access the Analytics Hub navigation link and view trend charts'),

    # 10. Customer Directory
    ('customers.view', 'View Customer Directory', 'Customer Directory', 'Can access the Customer Directory navigation link and view customer profiles'),
    ('customers.create', 'Create Customer Accounts', 'Customer Directory', 'Can register new customer accounts and credit terms'),
    ('customers.update', 'Update Customer Details', 'Customer Directory', 'Can edit customer addresses, contacts, and terms'),
    ('customers.delete', 'Delete Customers', 'Customer Directory', 'Can archive or remove customer profiles'),

    # 11. User Management (Administration)
    ('users.view', 'View User Management', 'Administration & Security', 'Can access the User Management administration link and view user directory'),
    ('users.create', 'Create User Accounts', 'Administration & Security', 'Can create new application user logins and set passwords'),
    ('users.update', 'Update User Accounts', 'Administration & Security', 'Can edit user details, status, and reset passwords'),
    ('users.assign_roles', 'Assign User Roles', 'Administration & Security', 'Can grant or revoke business roles and permissions for users'),
    ('users.delete', 'Deactivate / Delete Users', 'Administration & Security', 'Can enable, disable, or remove user accounts'),

    # 12. Roles & Permissions (Administration)
    ('roles.view', 'View Roles & Permissions', 'Administration & Security', 'Can access the Roles & Permissions administration link and view matrix'),
    ('roles.create', 'Create Business Roles', 'Administration & Security', 'Can create custom roles and bundle permissions'),
    ('roles.update', 'Edit Business Roles', 'Administration & Security', 'Can modify role configurations and toggle permission matrix checkboxes'),
    ('roles.delete', 'Delete Business Roles', 'Administration & Security', 'Can delete custom business roles'),

    # 13. Security & Audit Trail (Administration)
    ('audit.view', 'View Security & Audit Logs', 'Administration & Security', 'Can access the Security & Audit Logs administration link and view audit trail'),

    # 14. System Configurations & Company Settings
    ('settings.view', 'View System Settings', 'Administration & Security', 'Can view company info and system configurations'),
    ('settings.edit', 'Edit Company & Business Settings', 'Administration & Security', 'Can modify company details, branding, addresses, and bank settlement info'),
    ('settings.update', 'Update System Settings', 'Administration & Security', 'Can execute system operations, company updates, and demo data resets'),
]



@receiver(post_save, sender=User)
def create_or_update_user_profile(sender, instance, created, **kwargs):
    """
    Ensures every Django User has an associated UserProfile.
    """
    profile, _ = UserProfile.objects.get_or_create(user=instance)
    if not created:
        profile.save()


@receiver(post_migrate)
def seed_system_permissions_and_roles(sender, **kwargs):
    """
    Automatically populates standardized system permissions and default roles.
    """
    # Only run for accounts app if sender is specified
    if sender is not None and getattr(sender, 'name', None) != 'apps.accounts':
        return

    # 1. Seed Permissions
    created_perms = {}
    for codename, name, module, description in SYSTEM_PERMISSIONS:
        perm, _ = SystemPermission.objects.get_or_create(
            codename=codename,
            defaults={
                'name': name,
                'module': module,
                'description': description
            }
        )
        created_perms[codename] = perm

    # 2. Seed Default Roles
    DEFAULT_ROLES = [
        (
            'Operations Manager',
            'Full management over purchase orders, quotations, deliveries, and customer accounts.',
            [
                'orders.view', 'orders.create', 'orders.upload', 'orders.email',
                'quotes.view', 'quotes.create', 'quotes.send', 'quotes.approve', 'quotes.download',
                'logistics.view', 'logistics.create', 'logistics.update_status', 'logistics.upload_pod',
                'customers.view', 'customers.create', 'customers.update',
                'invoices.view', 'receipts.view',
                'reports.view', 'analytics.view'
            ]
        ),
        (
            'Finance & Accounts Manager',
            'Full management over billing, expenses, payables, receivables, financial statements, reports, and tax.',
            [
                'invoices.view', 'invoices.create', 'invoices.send', 'invoices.record_payment', 'invoices.download',
                'receipts.view', 'receipts.download',
                'expenses.view', 'expenses.create', 'expenses.update', 'expenses.delete',
                'accounts.view', 'accounts.receivables.view', 'accounts.payables.view', 'accounts.payables.create',
                'accounts.statements.view', 'accounts.profit_loss.view', 'accounts.balance_sheet.view',
                'accounts.cash_flow.view', 'accounts.vat.view',
                'reports.view', 'reports.export', 'analytics.view',
                'customers.view', 'customers.create', 'customers.update',
                'orders.view', 'quotes.view', 'logistics.view'
            ]
        ),
        (
            'Sales Representative',
            'Handles customer orders, generates quotes, and tracks delivery progress.',
            [
                'orders.view', 'orders.create', 'orders.upload',
                'quotes.view', 'quotes.create', 'quotes.send', 'quotes.download',
                'customers.view', 'customers.create',
                'logistics.view', 'reports.view'
            ]
        ),
        (
            'Auditor / Viewer',
            'Read-only access across orders, quotations, deliveries, invoices, accounting, and reports.',
            [
                'orders.view', 'quotes.view', 'logistics.view', 'invoices.view', 'receipts.view',
                'expenses.view', 'accounts.view', 'accounts.receivables.view', 'accounts.payables.view',
                'accounts.statements.view', 'accounts.profit_loss.view', 'accounts.balance_sheet.view',
                'accounts.cash_flow.view', 'accounts.vat.view', 'reports.view', 'analytics.view',
                'customers.view', 'audit.view'
            ]
        )
    ]

    for role_name, description, perm_codenames in DEFAULT_ROLES:
        role, _ = Role.objects.get_or_create(
            name=role_name,
            defaults={
                'description': description,
                'is_system': True,
                'is_active': True
            }
        )
        role_perms = [created_perms[code] for code in perm_codenames if code in created_perms]
        role.permissions.set(role_perms)

    # 3. Seed Default Expense Categories if accounting app is present
    try:
        from apps.accounting.models import ExpenseCategory
        DEFAULT_EXPENSE_CATEGORIES = [
            ('Transport & Freight', 'EXP-TRN', 'Direct long-haul and subcontracted transport charges', True),
            ('Fuel & Tolls', 'EXP-FUEL', 'Fleet diesel, petrol, and cross-border toll fees', True),
            ('Vehicle Maintenance & Repairs', 'EXP-MAINT', 'Truck servicing, tyres, and mechanical repairs', True),
            ('Salaries & Wages', 'EXP-SAL', 'Driver salaries, operations and administrative staff compensation', True),
            ('Rent & Facilities', 'EXP-RENT', 'Depot lease, office rent, and warehouse storage costs', True),
            ('Utilities & Municipal', 'EXP-UTIL', 'Electricity, water, and waste management services', True),
            ('Office & Administrative', 'EXP-OFFICE', 'Stationery, software licenses, printing, and supplies', True),
            ('Telecommunications & Data', 'EXP-TEL', 'Mobile phones, tracking SIM cards, and internet', True),
            ('Insurance', 'EXP-INS', 'Goods in transit, fleet comprehensive, and public liability', True),
            ('Professional & Legal Fees', 'EXP-PROF', 'Accounting, audit, legal, and compliance fees', True),
            ('Bank Charges & Interest', 'EXP-BANK', 'Bank account management, merchant fees, and finance costs', True),
            ('Marketing & Business Dev', 'EXP-MKT', 'Client acquisition, promotional materials, and advertising', True),
            ('Taxes & Levies', 'EXP-TAX', 'Municipal permits, road levies, and non-claimable duties', True),
            ('Other General Expenses', 'EXP-OTHER', 'Miscellaneous sundries and petty cash expenses', True),
        ]
        for cat_name, cat_code, desc, is_sys in DEFAULT_EXPENSE_CATEGORIES:
            ExpenseCategory.objects.get_or_create(
                name=cat_name,
                defaults={
                    'code': cat_code,
                    'description': desc,
                    'is_system': is_sys,
                    'is_active': True
                }
            )
    except Exception:
        pass

    # 4. Seed Default Superuser if none exists
    if not User.objects.filter(is_superuser=True).exists():
        admin_user = User.objects.create_superuser(
            username='admin',
            email='admin@menardtrading.com',
            password='MenardAdmin2026!',
            first_name='Operations',
            last_name='Administrator'
        )
        UserProfile.objects.get_or_create(user=admin_user)
