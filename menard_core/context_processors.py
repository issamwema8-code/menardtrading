from django.conf import settings

def branding_context(request):
    """
    Exposes centralized Menard Trading CC branding, counts, and settings to all templates.
    """
    from apps.orders.models import PurchaseOrder
    from apps.quotes.models import Quotation
    from apps.logistics.models import LogisticsJob
    from apps.billing.models import Invoice
    from apps.customers.models import Customer

    try:
        orders_count = PurchaseOrder.objects.count()
        quotes_count = Quotation.objects.filter(status__in=[Quotation.Status.DRAFT, Quotation.Status.SENT]).count()
        jobs_count = LogisticsJob.objects.exclude(status=LogisticsJob.Status.CLOSED).count()
        invoices_count = Invoice.objects.exclude(status='CANCELLED').count()
        global_customers = list(Customer.objects.all().order_by('company_name')[:50])
    except Exception:
        orders_count = 0
        quotes_count = 0
        jobs_count = 0
        invoices_count = 0
        global_customers = []

    try:
        from apps.accounts.models import CompanySettings
        company_info = CompanySettings.get_settings().as_branding_dict()
    except Exception:
        company_info = getattr(settings, 'EMAIL_BRANDING', {})

    return {
        'branding': company_info,
        'company': company_info,
        'base_url': getattr(settings, 'BASE_URL', 'https://menardtrading.com'),
        'header_counts': {
            'orders_count': orders_count,
            'quotes_count': quotes_count,
            'jobs_count': jobs_count,
            'invoices_count': invoices_count,
        },
        'global_customers': global_customers,
    }

