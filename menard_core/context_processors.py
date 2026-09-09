from django.conf import settings

def branding_context(request):
    """
    Exposes centralized Menard Trading CC branding, counts, and settings to all templates.
    """
    from apps.orders.models import PurchaseOrder
    from apps.quotes.models import Quotation
    from apps.logistics.models import LogisticsJob
    from apps.billing.models import Invoice
    from apps.customers.models import Customer, PaymentTermOption

    try:
        orders_count = PurchaseOrder.objects.count()
        quotes_count = Quotation.objects.filter(status__in=[Quotation.Status.DRAFT, Quotation.Status.SENT]).count()
        jobs_count = LogisticsJob.objects.exclude(status=LogisticsJob.Status.CLOSED).count()
        invoices_count = Invoice.objects.exclude(status='CANCELLED').count()
        global_customers = list(Customer.objects.all().order_by('company_name')[:100])
        global_payment_terms = list(PaymentTermOption.get_all_terms())
        global_purchase_orders = list(PurchaseOrder.objects.exclude(status=PurchaseOrder.Status.CANCELLED).select_related('customer').order_by('-created_at')[:100])
    except Exception:
        orders_count = 0
        quotes_count = 0
        jobs_count = 0
        invoices_count = 0
        global_customers = []
        global_payment_terms = []
        global_purchase_orders = []

    try:
        from apps.accounts.models import AdminNotification
        unread_notifications_count = AdminNotification.objects.filter(is_read=False).count()
        recent_notifications = list(AdminNotification.objects.all().order_by('-created_at')[:5])
    except Exception:
        unread_notifications_count = 0
        recent_notifications = []

    try:
        from apps.accounts.models import CompanySettings
        company_info = CompanySettings.get_settings().as_branding_dict()
    except Exception:
        company_info = getattr(settings, 'EMAIL_BRANDING', {})

    import json
    customers_data = [
        {
            'id': str(c.id),
            'company_name': c.company_name or '',
            'contact_name': c.contact_name or '',
            'email': c.email or '',
            'phone': c.phone or '',
        }
        for c in global_customers
    ]
    terms_data = [
        {
            'code': t.code if hasattr(t, 'code') else (t.get('code', '') if isinstance(t, dict) else str(t)),
            'name': t.name if hasattr(t, 'name') else (t.get('name', '') if isinstance(t, dict) else str(t)),
        }
        for t in global_payment_terms
    ]

    return {
        'branding': company_info,
        'company': company_info,
        'base_url': getattr(settings, 'BASE_URL', 'https://menardtrading.com'),
        'header_counts': {
            'orders_count': orders_count,
            'quotes_count': quotes_count,
            'jobs_count': jobs_count,
            'invoices_count': invoices_count,
            'unread_notifications_count': unread_notifications_count,
        },
        'recent_notifications': recent_notifications,
        'global_customers': global_customers,
        'global_payment_terms': global_payment_terms,
        'global_purchase_orders': global_purchase_orders,
        'global_customers_json': json.dumps(customers_data),
        'global_payment_terms_json': json.dumps(terms_data),
    }

